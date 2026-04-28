"""Use-case composition for the mab_devices feature (Layer 2).

Notable patterns vs. the vanilla CRUD vlans template:
- **Unauthenticated lookup atom** (`check_mac_for_radius`) — used by the
  FreeRADIUS authorize hook; no tenant filter, no audit (high-frequency
  read-only).
- **MAC normalization** lives here as a private helper; promote to
  `shared/orw_common` per §3.2.1 if other features need it.
- **Bulk import correctness fix:** the legacy route counted every
  attempt as `created` even when ON CONFLICT skipped it (so `skipped`
  was always 0). This service uses the repo's `bulk_insert_mab_device`
  return value to count accurately.
"""
import re
from typing import Any, Mapping, Optional
from uuid import UUID
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.exceptions import NotFoundError, ValidationError
from utils.audit import log_audit

from . import repository as repo
from .schemas import MabDeviceBulkItem


# ---------------------------------------------------------------------------
# MAC normalization
# ---------------------------------------------------------------------------

def _normalize_mac(raw: str) -> str:
    """Any MAC format → `aa:bb:cc:dd:ee:ff`. Raises ValidationError on bad input."""
    hex_only = re.sub(r"[^0-9a-fA-F]", "", raw)
    if len(hex_only) != 12:
        raise ValidationError("Invalid MAC address")
    return ":".join(hex_only[i:i + 2].lower() for i in range(0, 12, 2))


def _stringify_mac(row: Mapping[str, Any]) -> dict:
    item = dict(row)
    if item.get("mac_address") is not None:
        item["mac_address"] = str(item["mac_address"])
    return item


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

async def list_mab_devices(
    db: AsyncSession,
    *,
    tenant_id: str,
    enabled: Optional[bool],
    device_type: Optional[str],
    page: int,
    page_size: int,
) -> dict:
    total = await repo.count_mab_devices(
        db, tenant_id=tenant_id, enabled=enabled, device_type=device_type,
    )
    rows = await repo.list_mab_devices(
        db, tenant_id=tenant_id, enabled=enabled, device_type=device_type,
        limit=page_size, offset=(page - 1) * page_size,
    )
    return {
        "items": [_stringify_mac(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def get_mab_device(
    db: AsyncSession, *, tenant_id: str, device_id: UUID,
) -> dict:
    row = await repo.lookup_mab_device(db, tenant_id=tenant_id, device_id=device_id)
    if not row:
        raise NotFoundError("MAB device", str(device_id))
    return _stringify_mac(row)


async def check_mac_for_radius(
    db: AsyncSession, *, raw_mac: str,
) -> dict:
    """FreeRADIUS authorize hook entry point. No tenant filter, no audit.

    Raises ValidationError on malformed MAC, NotFoundError if not whitelisted.
    """
    normalized = _normalize_mac(raw_mac)
    row = await repo.radius_lookup_mac(db, normalized_mac=normalized)
    if not row:
        raise NotFoundError("MAC", normalized)
    return _stringify_mac(row)


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

async def create_mab_device(
    db: AsyncSession,
    actor: dict,
    *,
    mac_address: str,
    name: Optional[str],
    description: Optional[str],
    device_type: Optional[str],
    assigned_vlan_id: Optional[int],
    enabled: bool,
    expiry_date: Optional[datetime],
    client_ip: Optional[str],
) -> dict:
    row = await repo.insert_mab_device(
        db,
        tenant_id=actor["tenant_id"],
        created_by=actor["sub"],
        mac_address=mac_address,
        name=name,
        description=description,
        device_type=device_type,
        assigned_vlan_id=assigned_vlan_id,
        enabled=enabled,
        expiry_date=expiry_date,
    )
    await log_audit(
        db, actor,
        action="create", resource_type="mab_device",
        resource_id=str(row["id"]),
        details={"mac_address": mac_address, "name": name},
        ip_address=client_ip,
    )
    return _stringify_mac(row)


async def update_mab_device(
    db: AsyncSession,
    actor: dict,
    *,
    device_id: UUID,
    updates: dict,
    client_ip: Optional[str],
) -> dict:
    if not updates:
        raise ValidationError("No fields to update")

    existing = await repo.lookup_mab_device_summary(
        db, tenant_id=actor["tenant_id"], device_id=device_id,
    )
    if not existing:
        raise NotFoundError("MAB device", str(device_id))

    try:
        row = await repo.update_mab_device(
            db, tenant_id=actor["tenant_id"],
            device_id=device_id, updates=updates,
        )
    except ValueError:
        raise ValidationError("No valid fields to update")

    if not row:
        raise NotFoundError("MAB device", str(device_id))

    await log_audit(
        db, actor,
        action="update", resource_type="mab_device",
        resource_id=str(device_id),
        details={"changed_fields": updates, "mac": str(existing["mac_address"])},
        ip_address=client_ip,
    )
    return _stringify_mac(row)


async def delete_mab_device(
    db: AsyncSession,
    actor: dict,
    *,
    device_id: UUID,
    client_ip: Optional[str],
) -> None:
    existing = await repo.lookup_mab_device_summary(
        db, tenant_id=actor["tenant_id"], device_id=device_id,
    )
    if not existing:
        raise NotFoundError("MAB device", str(device_id))

    await repo.delete_mab_device(
        db, tenant_id=actor["tenant_id"], device_id=device_id,
    )
    await log_audit(
        db, actor,
        action="delete", resource_type="mab_device",
        resource_id=str(device_id),
        details={"mac": str(existing["mac_address"]), "name": existing["name"]},
        ip_address=client_ip,
    )


async def bulk_import(
    db: AsyncSession,
    actor: dict,
    *,
    devices: list[MabDeviceBulkItem],
    client_ip: Optional[str],
) -> dict:
    """Insert many MAB devices, skipping duplicates idempotently.

    Counts are accurate (the legacy implementation incorrectly counted every
    attempt as `created` because ON CONFLICT DO NOTHING doesn't raise).
    """
    created = 0
    skipped = 0
    for dev in devices:
        inserted = await repo.bulk_insert_mab_device(
            db,
            tenant_id=actor["tenant_id"],
            created_by=actor["sub"],
            mac_address=dev.mac_address,
            name=dev.name,
            device_type=dev.device_type,
            assigned_vlan_id=dev.assigned_vlan_id,
        )
        if inserted:
            created += 1
        else:
            skipped += 1

    await log_audit(
        db, actor,
        action="bulk_import", resource_type="mab_device",
        details={"total": len(devices), "created": created, "skipped": skipped},
        ip_address=client_ip,
    )
    return {"created": created, "skipped": skipped, "total": len(devices)}
