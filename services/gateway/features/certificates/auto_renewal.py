"""Periodic auto-renewal of server certs nearing expiry.

The gateway lifespan task wakes every ``ORW_CERT_RENEW_INTERVAL_HOURS``
hours (default 6), calls ``run_auto_renewal_once`` for each tenant, and
goes back to sleep. The function is the integration boundary the loop
driver in ``main.py`` calls — keeping it here lets us unit-test the
flow without standing up FastAPI's lifespan machinery.

Renewal scope:
  - server certs only (CA certs renew rarely / manually)
  - is_active=true (we don't pre-emptively renew dormant certs)
  - imported=false (operator must handle imported certs themselves)
  - expires within ORW_CERT_RENEW_THRESHOLD_DAYS (default 30)

For each candidate the service layer reconstructs a
GenerateServerRequest from the row's existing metadata, signs a fresh
cert with the active CA, and activates it. The activation deactivates
the previous cert and publishes orw.config.freeradius.apply via NATS,
which the freeradius_config_watcher picks up and SIGHUPs freeradius.

Audit actor: the seeded `admin` user. Auto-renewal isn't user-initiated
but the audit log requires SOMEONE; using `admin` keeps the audit
trail readable and avoids needing a dedicated `system` user row + a
schema migration. The audit `details` field flags the row as auto-
renewed so a reader can distinguish from a manual `admin` action.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from . import service


def _renew_threshold_days() -> int:
    raw = os.environ.get("ORW_CERT_RENEW_THRESHOLD_DAYS", "30")
    try:
        v = int(raw)
        return v if v > 0 else 30
    except ValueError:
        return 30


def _renew_interval_seconds() -> float:
    raw = os.environ.get("ORW_CERT_RENEW_INTERVAL_HOURS", "6")
    try:
        v = float(raw)
        return v * 3600.0 if v > 0 else 6 * 3600.0
    except ValueError:
        return 6 * 3600.0


async def _resolve_system_actor(db: AsyncSession) -> Optional[dict[str, Any]]:
    """Build the audit actor for renewal events.

    Looks up the seeded `admin` user under the `default` tenant (per
    migrations/seed.sql). Returns None if either is missing — the
    caller logs the warning and skips the cycle, since
    ``service.generate_server`` requires a real user FK in created_by.
    """
    result = await db.execute(
        text(
            "SELECT u.id AS sub, u.tenant_id AS tenant_id "
            "FROM users u JOIN tenants t ON u.tenant_id = t.id "
            "WHERE u.username = 'admin' AND t.name = 'default' LIMIT 1"
        )
    )
    row = result.mappings().first()
    if row is None:
        return None
    return {"sub": str(row["sub"]), "tenant_id": str(row["tenant_id"])}


async def run_auto_renewal_once(db: AsyncSession) -> dict:
    """One pass of the renewal loop. Returns a structured summary so
    the caller can log it. Idempotent: rows that don't need renewal are
    untouched; rows that already have a renewed-named twin from an
    earlier pass will fail the UNIQUE(name, tenant_id) constraint and
    surface in summary['errors'] (which is fine — means the previous
    pass already handled it but didn't deactivate the original).
    """
    actor = await _resolve_system_actor(db)
    if actor is None:
        return {
            "checked": 0,
            "renewed": [],
            "errors": [
                "auto-renewal actor unresolved: no `admin` user under "
                "the `default` tenant. Check migrations/seed.sql."
            ],
        }

    return await service.auto_renew_expiring_server_certs(
        db, actor, threshold_days=_renew_threshold_days(),
    )
