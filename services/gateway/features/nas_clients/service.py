"""Use-case composition for the nas_clients feature (Layer 2).

Two notable patterns this feature exercises (vs. the vanilla CRUD vlans):
- **Secret masking in audit:** the `shared_secret` value never enters the
  audit log; `_audit_changed_fields` strips it.
- **NATS event publish:** `sync_radius` is a write-side action whose
  effect is "tell freeradius_config_watcher to reload" — see events.py.
"""
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.exceptions import NotFoundError, ValidationError
from utils.audit import log_audit

from . import events
from . import repository as repo


def _audit_changed_fields(updates: dict) -> dict:
    """Mask the shared_secret value before it reaches the audit log."""
    masked = {k: v for k, v in updates.items() if k != "shared_secret"}
    if "shared_secret" in updates:
        masked["shared_secret"] = "********"
    return masked


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

async def list_nas_clients(db: AsyncSession, *, tenant_id: str) -> list[dict]:
    rows = await repo.list_nas_clients(db, tenant_id=tenant_id)
    return [dict(r) for r in rows]


async def get_nas_client(
    db: AsyncSession, *, tenant_id: str, nas_id: UUID,
) -> dict:
    row = await repo.lookup_nas_client(db, tenant_id=tenant_id, nas_id=nas_id)
    if not row:
        raise NotFoundError("NAS client", str(nas_id))
    return dict(row)


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

async def create_nas_client(
    db: AsyncSession,
    actor: dict,
    *,
    name: str,
    ip_address: str,
    shared_secret: str,
    shortname: Optional[str],
    nas_type: str,
    description: Optional[str],
    client_ip: Optional[str],
) -> dict:
    row = await repo.insert_nas_client(
        db,
        tenant_id=actor["tenant_id"],
        name=name, ip_address=ip_address,
        shared_secret=shared_secret,
        shortname=shortname, nas_type=nas_type,
        description=description,
    )
    await log_audit(
        db, actor,
        action="create", resource_type="nas_client",
        resource_id=str(row["id"]),
        details={"name": name, "ip_address": ip_address},
        ip_address=client_ip,
    )
    return dict(row)


async def update_nas_client(
    db: AsyncSession,
    actor: dict,
    *,
    nas_id: UUID,
    updates: dict,
    client_ip: Optional[str],
) -> dict:
    if not updates:
        raise ValidationError("No fields to update")

    existing = await repo.lookup_nas_client_summary(
        db, tenant_id=actor["tenant_id"], nas_id=nas_id,
    )
    if not existing:
        raise NotFoundError("NAS client", str(nas_id))

    try:
        row = await repo.update_nas_client(
            db, tenant_id=actor["tenant_id"],
            nas_id=nas_id, updates=updates,
        )
    except ValueError:
        raise ValidationError("No valid fields to update")

    if not row:
        raise NotFoundError("NAS client", str(nas_id))

    await log_audit(
        db, actor,
        action="update", resource_type="nas_client",
        resource_id=str(nas_id),
        details={
            "changed_fields": _audit_changed_fields(updates),
            "name": existing["name"],
        },
        ip_address=client_ip,
    )
    return dict(row)


async def delete_nas_client(
    db: AsyncSession,
    actor: dict,
    *,
    nas_id: UUID,
    client_ip: Optional[str],
) -> None:
    existing = await repo.lookup_nas_client_summary(
        db, tenant_id=actor["tenant_id"], nas_id=nas_id,
    )
    if not existing:
        raise NotFoundError("NAS client", str(nas_id))

    await repo.delete_nas_client(
        db, tenant_id=actor["tenant_id"], nas_id=nas_id,
    )
    await log_audit(
        db, actor,
        action="delete", resource_type="nas_client",
        resource_id=str(nas_id),
        details={"name": existing["name"]},
        ip_address=client_ip,
    )


async def sync_radius(
    db: AsyncSession, actor: dict, *, client_ip: Optional[str],
) -> dict:
    """Trigger a FreeRADIUS reload via NATS.

    The actual reload is performed by `freeradius_config_watcher`,
    which subscribes to `orw.config.freeradius.apply`.
    """
    await events.publish_freeradius_apply(
        triggered_by=actor.get("username", actor.get("sub")),
        action="reload_nas_clients",
    )
    await log_audit(
        db, actor,
        action="sync", resource_type="nas_client",
        resource_id=None,
        details={"action": "freeradius_reload_triggered"},
        ip_address=client_ip,
    )
    return {"status": "sync_requested", "message": "FreeRADIUS reload has been triggered"}
