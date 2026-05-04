"""Unit tests for /api/v1/health/backup status summarisation.

Tests the pure logic in `_summarise_status` — no FastAPI client
needed, just feed it raw dicts shaped like what
`scripts/backup-and-rotate.sh` writes to the status file.

Edge cases covered: ok within freshness window / stale beyond it /
local error / missing local key / malformed timestamp.
"""
from datetime import datetime, timedelta, timezone

from features.health.routes import _summarise_status


NOW = datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc)


def _raw(local_status="ok", started_at="2026-05-05T11:30:00Z",
         offsite_status="ok"):
    return {
        "started_at": started_at,
        "duration_seconds": 18,
        "local": {
            "status": local_status,
            "error": "",
            "archive_path": "/opt/openradiusweb/backups/orw-backup-2026-05-05_023000.tar.gz.gpg",
            "archive_size_bytes": 12_345_678,
        },
        "offsite": {
            "status": offsite_status,
            "error": "",
            "target": "orw-backup@nas.local:/srv/orw-backups/",
        },
        "prune": {"keep_days": 7, "deleted_count": 1},
    }


def test_recent_success_is_ok():
    out = _summarise_status(_raw(), NOW)
    assert out["status"] == "ok"
    assert out["age_seconds"] == 30 * 60  # 30 min
    assert out["local"]["status"] == "ok"
    assert out["offsite"]["status"] == "ok"


def test_old_success_is_stale():
    """Default ORW_BACKUP_STALE_AFTER_SECONDS is 36h. A 48h-old
    successful backup is stale."""
    two_days_ago = (NOW - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = _summarise_status(_raw(started_at=two_days_ago), NOW)
    assert out["status"] == "stale"
    assert out["age_seconds"] == 48 * 3600


def test_local_error_propagates():
    out = _summarise_status(_raw(local_status="error"), NOW)
    assert out["status"] == "error"


def test_offsite_error_does_not_demote_overall_status():
    """Offsite failure is bad but local backup still succeeded —
    overall is still 'ok'. Operator sees the offsite error in the
    drill-down. Don't conflate local DR (offsite) with backup
    correctness (local)."""
    raw = _raw(offsite_status="error")
    raw["offsite"]["error"] = "ssh: connect refused"
    out = _summarise_status(raw, NOW)
    assert out["status"] == "ok"
    assert out["offsite"]["status"] == "error"
    assert "connect refused" in out["offsite"]["error"]


def test_missing_local_key_is_unknown():
    raw = {"started_at": "2026-05-05T11:30:00Z"}
    out = _summarise_status(raw, NOW)
    # local missing → not "ok", not "error" → unknown
    assert out["status"] == "unknown"


def test_malformed_timestamp_is_unknown():
    raw = _raw(started_at="not-a-timestamp")
    out = _summarise_status(raw, NOW)
    assert out["status"] == "unknown"
