"""Use-case composition for the freeradius_config feature (Layer 2)."""
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from utils.audit import log_audit

from . import events
from . import repository as repo


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

async def get_config_status(
    db: AsyncSession, *, tenant_id: str,
) -> dict:
    """Returns configs + a `needs_apply` flag (any hash mismatch)."""
    rows = await repo.list_config_status(db, tenant_id=tenant_id)
    configs = [dict(r) for r in rows]
    needs_apply = any(
        c["config_hash"] != c.get("last_applied_hash")
        for c in configs
        if c["config_hash"]
    )
    return {
        "configs": configs,
        "needs_apply": needs_apply,
        "total": len(configs),
    }


async def preview_config(
    db: AsyncSession, *, tenant_id: str,
) -> dict:
    """Returns stored configs + counts of source data for context."""
    rows = await repo.list_config_preview(db, tenant_id=tenant_id)
    return {
        "configs": [dict(r) for r in rows],
        "source_data": {
            "ldap_servers": await repo.count_enabled_ldap_servers(
                db, tenant_id=tenant_id,
            ),
            "realms": await repo.count_enabled_realms(
                db, tenant_id=tenant_id,
            ),
            "nas_clients": await repo.count_enabled_nas_clients(
                db, tenant_id=tenant_id,
            ),
            "active_certificates": await repo.count_active_certificates(
                db, tenant_id=tenant_id,
            ),
        },
    }


async def get_history(
    db: AsyncSession,
    *,
    tenant_id: str,
    page: int,
    page_size: int,
) -> dict:
    total = await repo.count_config_history(db, tenant_id=tenant_id)
    rows = await repo.list_config_history(
        db, tenant_id=tenant_id,
        limit=page_size, offset=(page - 1) * page_size,
    )
    return {
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# ---------------------------------------------------------------------------
# Apply (NATS publish + audit)
# ---------------------------------------------------------------------------

async def trigger_apply(
    db: AsyncSession,
    actor: dict,
    *,
    client_ip: Optional[str] = None,
) -> dict:
    await events.publish_freeradius_apply(
        tenant_id=actor.get("tenant_id"),
        requested_by=actor.get("sub"),
    )
    await log_audit(
        db, actor,
        action="freeradius_config_apply",
        resource_type="freeradius_config",
        details={
            "description": "Triggered FreeRADIUS configuration apply and reload",
        },
        ip_address=client_ip,
    )
    return {
        "status": "apply_triggered",
        "message": "Configuration apply request sent to FreeRADIUS",
    }
