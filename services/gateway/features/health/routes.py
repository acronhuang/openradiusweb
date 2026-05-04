"""Health check endpoint (Layer 3 only).

The bare /health is purely a thin shell — no service, repository,
or schemas slots. The /health/backup variant added in PR #102 reads
a metadata JSON file written by scripts/backup-and-rotate.sh on
every scheduled run; the file is mounted read-only into the gateway
container at ORW_BACKUP_STATUS_PATH (default
/var/orw-backups/.last-status.json).
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from orw_common import __version__
from orw_common.models.common import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """Service health check."""
    return HealthResponse(
        status="ok",
        service="orw-gateway",
        version=__version__,
    )


# How old the last successful backup can be before we flag the
# /health/backup endpoint as "stale". Default = 36h (1.5x the daily
# 24h cadence — gives a safety margin for off-by-time-zone surprises
# without raising false alarms on 25-hour gaps).
_STALE_AFTER_SECONDS = int(os.environ.get("ORW_BACKUP_STALE_AFTER_SECONDS", "129600"))
_STATUS_PATH = Path(os.environ.get(
    "ORW_BACKUP_STATUS_PATH",
    "/var/orw-backups/.last-status.json",
))


def _summarise_status(raw: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Layer the freshness check on top of the raw script output.
    Status values: ok / stale / error / unknown.
    """
    started_at = raw.get("started_at")
    local = raw.get("local") or {}
    offsite = raw.get("offsite") or {}

    overall = "unknown"
    age_seconds: int | None = None
    if local.get("status") == "ok" and started_at:
        try:
            ts = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            age_seconds = int((now - ts).total_seconds())
            if age_seconds <= _STALE_AFTER_SECONDS:
                overall = "ok"
            else:
                overall = "stale"
        except ValueError:
            overall = "unknown"
    elif local.get("status") == "error":
        overall = "error"

    return {
        "status": overall,
        "last_run_started_at": started_at,
        "age_seconds": age_seconds,
        "local": local,
        "offsite": offsite,
        "prune": raw.get("prune"),
    }


@router.get("/health/backup")
async def backup_health_check():
    """Read the status file written by backup-and-rotate.sh.

    Returns shape:
      {
        "status": "ok" | "stale" | "error" | "unknown",
        "last_run_started_at": "2026-05-04T18:30:00Z" | null,
        "age_seconds": 12345 | null,
        "local": { "status": ..., "archive_size_bytes": ..., ... },
        "offsite": { "status": ..., "target": ..., "error": ... },
        "prune": { "keep_days": 7, "deleted_count": 0 }
      }

    Use cases:
      - `curl http://gateway/api/v1/health/backup | jq .status` from
        a monitoring loop (Grafana / Uptime Kuma / etc.). Treat `ok`
        as green, `stale` as yellow, anything else as red.
      - Operator quickly checks "did last night's backup succeed?"
        from the UI without SSHing to the host.
    """
    if not _STATUS_PATH.exists():
        return {
            "status": "unknown",
            "last_run_started_at": None,
            "age_seconds": None,
            "local": None,
            "offsite": None,
            "prune": None,
            "note": (
                f"No backup status file at {_STATUS_PATH}. Either the "
                f"scheduled backup has never run, or the volume mount "
                f"isn't wired correctly. Check "
                f"docs/runbook-backup-cron.md install steps."
            ),
        }
    try:
        raw = json.loads(_STATUS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {
            "status": "error",
            "last_run_started_at": None,
            "age_seconds": None,
            "local": None,
            "offsite": None,
            "prune": None,
            "note": f"failed to read status file: {type(exc).__name__}: {exc}",
        }
    return _summarise_status(raw, datetime.now(timezone.utc))
