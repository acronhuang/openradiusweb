"""Use-case composition for the devices feature (Layer 2)."""
from typing import Any, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.exceptions import NotFoundError, ValidationError
from utils.audit import log_audit

from . import events
from . import repository as repo


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

async def list_devices(
    db: AsyncSession,
    *,
    tenant_id: str,
    status: Optional[str],
    device_type: Optional[str],
    search: Optional[str],
    page: int,
    page_size: int,
) -> dict:
    total = await repo.count_devices(
        db, tenant_id=tenant_id,
        status=status, device_type=device_type, search=search,
    )
    rows = await repo.list_devices(
        db, tenant_id=tenant_id,
        status=status, device_type=device_type, search=search,
        limit=page_size, offset=(page - 1) * page_size,
    )
    return {
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total > 0 else 0,
    }


async def get_device(
    db: AsyncSession, *, tenant_id: str, device_id: UUID,
) -> dict:
    row = await repo.lookup_device(db, tenant_id=tenant_id, device_id=device_id)
    if not row:
        raise NotFoundError("Device", str(device_id))
    return dict(row)


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

async def ingest_device(
    db: AsyncSession,
    actor: dict,
    *,
    mac_address: str,
    ip_address: Optional[str],
    hostname: Optional[str],
    device_type: Optional[str],
    os_family: Optional[str],
    os_version: Optional[str],
    vendor: Optional[str],
    model: Optional[str],
    client_ip: Optional[str] = None,
) -> dict:
    """UPSERT by (mac_address, tenant_id). Publishes orw.device.upserted."""
    row = await repo.upsert_device(
        db,
        tenant_id=actor["tenant_id"],
        mac_address=mac_address, ip_address=ip_address, hostname=hostname,
        device_type=device_type, os_family=os_family, os_version=os_version,
        vendor=vendor, model=model,
    )
    await events.publish_device_upserted(
        device_id=row["id"],
        mac_address=mac_address,
        ip_address=ip_address,
    )
    await log_audit(
        db, actor,
        action="create", resource_type="device",
        resource_id=str(row["id"]),
        details={"mac_address": mac_address, "ip_address": ip_address},
        ip_address=client_ip,
    )
    return dict(row)


async def update_device(
    db: AsyncSession,
    actor: dict,
    *,
    device_id: UUID,
    updates: dict,
    client_ip: Optional[str] = None,
) -> dict:
    cleaned = {k: v for k, v in updates.items() if v is not None}
    if not cleaned:
        raise ValidationError("No fields to update")

    try:
        row = await repo.update_device(
            db, tenant_id=actor["tenant_id"],
            device_id=device_id, updates=cleaned,
        )
    except ValueError:
        raise ValidationError("No valid fields to update")

    if not row:
        raise NotFoundError("Device", str(device_id))

    await log_audit(
        db, actor,
        action="update", resource_type="device",
        resource_id=str(device_id),
        details={"changed_fields": list(cleaned.keys())},
        ip_address=client_ip,
    )
    return dict(row)


async def delete_device(
    db: AsyncSession,
    actor: dict,
    *,
    device_id: UUID,
    client_ip: Optional[str] = None,
) -> None:
    if not await repo.delete_device(
        db, tenant_id=actor["tenant_id"], device_id=device_id,
    ):
        raise NotFoundError("Device", str(device_id))

    await log_audit(
        db, actor,
        action="delete", resource_type="device",
        resource_id=str(device_id),
        ip_address=client_ip,
    )


# ---------------------------------------------------------------------------
# Properties (EAV)
# ---------------------------------------------------------------------------

async def set_device_property(
    db: AsyncSession,
    actor: dict,
    *,
    device_id: UUID,
    category: str,
    key: str,
    value: Any,
    source: Optional[str],
    confidence: Optional[float],
    client_ip: Optional[str] = None,
) -> dict:
    if not await repo.device_exists(
        db, tenant_id=actor["tenant_id"], device_id=device_id,
    ):
        raise NotFoundError("Device", str(device_id))

    await repo.upsert_device_property(
        db, device_id=device_id,
        category=category, key=key, value=value,
        source=source, confidence=confidence,
    )
    await log_audit(
        db, actor,
        action="set_property", resource_type="device",
        resource_id=str(device_id),
        details={"category": category, "key": key},
        ip_address=client_ip,
    )
    return {"status": "ok"}


async def list_device_properties(
    db: AsyncSession,
    *,
    tenant_id: str,
    device_id: UUID,
    category: Optional[str],
) -> list[dict]:
    if not await repo.device_exists(
        db, tenant_id=tenant_id, device_id=device_id,
    ):
        raise NotFoundError("Device", str(device_id))
    rows = await repo.list_device_properties(
        db, device_id=device_id, category=category,
    )
    return [dict(r) for r in rows]
