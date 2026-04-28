"""Use-case composition for the vlans feature (Layer 2).

The service layer:
- normalises the row shape (e.g. casts PostgreSQL `inet`/`cidr` to str
  for JSON serialization),
- raises domain exceptions instead of HTTPException, and
- writes the audit log entry alongside each mutation.
"""
from typing import Any, Mapping, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.exceptions import NotFoundError, ValidationError
from utils.audit import log_audit

from . import repository as repo


def _stringify_subnet(row: Mapping[str, Any]) -> dict:
    """PostgreSQL `cidr` deserialises as IPNetwork — coerce to str for JSON."""
    item = dict(row)
    if item.get("subnet"):
        item["subnet"] = str(item["subnet"])
    return item


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

async def list_vlans(
    db: AsyncSession, *, tenant_id: str, purpose: Optional[str] = None,
) -> list[dict]:
    rows = await repo.list_vlans(db, tenant_id=tenant_id, purpose=purpose)
    return [_stringify_subnet(r) for r in rows]


async def get_vlan(
    db: AsyncSession, *, tenant_id: str, vlan_uuid: UUID,
) -> dict:
    row = await repo.lookup_vlan(db, tenant_id=tenant_id, vlan_uuid=vlan_uuid)
    if not row:
        raise NotFoundError("VLAN", str(vlan_uuid))
    return _stringify_subnet(row)


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

async def create_vlan(
    db: AsyncSession,
    actor: dict,
    *,
    vlan_id: int,
    name: str,
    description: Optional[str],
    purpose: Optional[str],
    subnet: Optional[str],
    enabled: bool,
    client_ip: Optional[str],
) -> dict:
    row = await repo.insert_vlan(
        db,
        tenant_id=actor["tenant_id"],
        vlan_id=vlan_id, name=name, description=description,
        purpose=purpose, subnet=subnet, enabled=enabled,
    )
    await log_audit(
        db, actor,
        action="create", resource_type="vlan",
        resource_id=str(row["id"]),
        details={"vlan_id": vlan_id, "name": name, "purpose": purpose},
        ip_address=client_ip,
    )
    return _stringify_subnet(row)


async def update_vlan(
    db: AsyncSession,
    actor: dict,
    *,
    vlan_uuid: UUID,
    updates: dict,
    client_ip: Optional[str],
) -> dict:
    if not updates:
        raise ValidationError("No fields to update")

    existing = await repo.lookup_vlan_summary(
        db, tenant_id=actor["tenant_id"], vlan_uuid=vlan_uuid,
    )
    if not existing:
        raise NotFoundError("VLAN", str(vlan_uuid))

    try:
        row = await repo.update_vlan(
            db, tenant_id=actor["tenant_id"],
            vlan_uuid=vlan_uuid, updates=updates,
        )
    except ValueError:
        raise ValidationError("No valid fields to update")

    if not row:
        # Race: row vanished between summary lookup and update.
        raise NotFoundError("VLAN", str(vlan_uuid))

    await log_audit(
        db, actor,
        action="update", resource_type="vlan",
        resource_id=str(vlan_uuid),
        details={"changed_fields": updates, "name": existing["name"]},
        ip_address=client_ip,
    )
    return _stringify_subnet(row)


async def delete_vlan(
    db: AsyncSession,
    actor: dict,
    *,
    vlan_uuid: UUID,
    client_ip: Optional[str],
) -> None:
    existing = await repo.lookup_vlan_summary(
        db, tenant_id=actor["tenant_id"], vlan_uuid=vlan_uuid,
    )
    if not existing:
        raise NotFoundError("VLAN", str(vlan_uuid))

    await repo.delete_vlan(
        db, tenant_id=actor["tenant_id"], vlan_uuid=vlan_uuid,
    )
    await log_audit(
        db, actor,
        action="delete", resource_type="vlan",
        resource_id=str(vlan_uuid),
        details={"name": existing["name"], "vlan_id": existing["vlan_id"]},
        ip_address=client_ip,
    )
