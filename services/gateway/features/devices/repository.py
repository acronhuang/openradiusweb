"""Database atoms for the devices feature.

POST is `INSERT ... ON CONFLICT (mac_address, tenant_id) DO UPDATE` —
the operation is logically an "upsert" / "ingest by MAC", not a pure
create. The atom name reflects that.

device_properties is an EAV-style child table; each row is keyed by
(device_id, category, key).
"""
from typing import Any, Mapping, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from utils.safe_sql import build_safe_set_clause, DEVICE_UPDATE_COLUMNS


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------

async def count_devices(
    db: AsyncSession,
    *,
    tenant_id: str,
    status: Optional[str] = None,
    device_type: Optional[str] = None,
    search: Optional[str] = None,
) -> int:
    where, params = _filter_clause(tenant_id, status, device_type, search)
    result = await db.execute(
        text(f"SELECT COUNT(*) FROM devices WHERE {where}"), params,
    )
    return int(result.scalar() or 0)


async def list_devices(
    db: AsyncSession,
    *,
    tenant_id: str,
    status: Optional[str],
    device_type: Optional[str],
    search: Optional[str],
    limit: int,
    offset: int,
) -> list[Mapping[str, Any]]:
    where, params = _filter_clause(tenant_id, status, device_type, search)
    params["limit"] = limit
    params["offset"] = offset
    result = await db.execute(
        text(
            f"SELECT * FROM devices WHERE {where} "
            f"ORDER BY last_seen DESC LIMIT :limit OFFSET :offset"
        ),
        params,
    )
    return list(result.mappings().all())


async def lookup_device(
    db: AsyncSession, *, tenant_id: str, device_id: UUID,
) -> Optional[Mapping[str, Any]]:
    result = await db.execute(
        text("SELECT * FROM devices WHERE id = :id AND tenant_id = :tenant_id"),
        {"id": str(device_id), "tenant_id": tenant_id},
    )
    return result.mappings().first()


async def device_exists(
    db: AsyncSession, *, tenant_id: str, device_id: UUID,
) -> bool:
    """Used by property atoms to validate parent before insert/list."""
    result = await db.execute(
        text(
            "SELECT 1 FROM devices WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(device_id), "tenant_id": tenant_id},
    )
    return result.first() is not None


async def upsert_device(
    db: AsyncSession,
    *,
    tenant_id: str,
    mac_address: str,
    ip_address: Optional[str],
    hostname: Optional[str],
    device_type: Optional[str],
    os_family: Optional[str],
    os_version: Optional[str],
    vendor: Optional[str],
    model: Optional[str],
) -> Mapping[str, Any]:
    """INSERT ON CONFLICT — keep existing IP/hostname when payload omits them."""
    result = await db.execute(
        text(
            "INSERT INTO devices (mac_address, ip_address, hostname, device_type, "
            "os_family, os_version, vendor, model, tenant_id) "
            "VALUES (:mac_address, :ip_address, :hostname, :device_type, "
            ":os_family, :os_version, :vendor, :model, :tenant_id) "
            "ON CONFLICT (mac_address, tenant_id) DO UPDATE SET "
            "ip_address = COALESCE(EXCLUDED.ip_address, devices.ip_address), "
            "hostname = COALESCE(EXCLUDED.hostname, devices.hostname), "
            "last_seen = NOW() "
            "RETURNING *"
        ),
        {
            "mac_address": mac_address,
            "ip_address": ip_address,
            "hostname": hostname,
            "device_type": device_type,
            "os_family": os_family,
            "os_version": os_version,
            "vendor": vendor,
            "model": model,
            "tenant_id": tenant_id,
        },
    )
    row = result.mappings().first()
    if row is None:
        raise RuntimeError("UPSERT devices RETURNING produced no row")
    return row


async def update_device(
    db: AsyncSession, *, tenant_id: str, device_id: UUID, updates: dict,
) -> Optional[Mapping[str, Any]]:
    """Partial update. Raises ValueError if no allowed columns."""
    set_clause, params = build_safe_set_clause(updates, DEVICE_UPDATE_COLUMNS)
    params["id"] = str(device_id)
    params["tenant_id"] = tenant_id
    result = await db.execute(
        text(
            f"UPDATE devices SET {set_clause}, updated_at = NOW() "
            f"WHERE id = :id AND tenant_id = :tenant_id RETURNING *"
        ),
        params,
    )
    return result.mappings().first()


async def delete_device(
    db: AsyncSession, *, tenant_id: str, device_id: UUID,
) -> bool:
    """Returns True if a row was deleted."""
    result = await db.execute(
        text(
            "DELETE FROM devices WHERE id = :id AND tenant_id = :tenant_id "
            "RETURNING id"
        ),
        {"id": str(device_id), "tenant_id": tenant_id},
    )
    return result.first() is not None


# ---------------------------------------------------------------------------
# Device properties (EAV)
# ---------------------------------------------------------------------------

async def upsert_device_property(
    db: AsyncSession,
    *,
    device_id: UUID,
    category: str,
    key: str,
    value: Any,
    source: Optional[str],
    confidence: Optional[float],
) -> None:
    await db.execute(
        text(
            "INSERT INTO device_properties "
            "(device_id, category, key, value, source, confidence) "
            "VALUES (:device_id, :category, :key, :value, :source, :confidence) "
            "ON CONFLICT (device_id, category, key) DO UPDATE SET "
            "value = EXCLUDED.value, source = EXCLUDED.source, "
            "confidence = EXCLUDED.confidence, updated_at = NOW()"
        ),
        {
            "device_id": str(device_id),
            "category": category,
            "key": key,
            "value": value,
            "source": source,
            "confidence": confidence,
        },
    )


async def list_device_properties(
    db: AsyncSession, *, device_id: UUID, category: Optional[str],
) -> list[Mapping[str, Any]]:
    sql = "SELECT * FROM device_properties WHERE device_id = :device_id"
    params: dict = {"device_id": str(device_id)}
    if category:
        sql += " AND category = :category"
        params["category"] = category
    sql += " ORDER BY category, key"
    result = await db.execute(text(sql), params)
    return list(result.mappings().all())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _filter_clause(
    tenant_id: str,
    status: Optional[str],
    device_type: Optional[str],
    search: Optional[str],
) -> tuple[str, dict]:
    conditions = ["tenant_id = :tenant_id"]
    params: dict = {"tenant_id": tenant_id}
    if status:
        conditions.append("status = :status")
        params["status"] = status
    if device_type:
        conditions.append("device_type = :device_type")
        params["device_type"] = device_type
    if search:
        conditions.append(
            "(hostname ILIKE :search OR ip_address::text LIKE :search "
            "OR mac_address::text LIKE :search OR vendor ILIKE :search)"
        )
        params["search"] = f"%{search}%"
    return " AND ".join(conditions), params
