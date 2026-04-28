"""Database atoms for the mab_devices feature.

PostgreSQL detail encapsulated here:
- `mac_address` is a `macaddr` column → needs `::macaddr` on insert and
  `str()` coercion on read.
- The `radius_lookup_*` atom is intentionally NOT scoped to a tenant —
  it serves the FreeRADIUS authorize hook, which has no tenant context.
"""
from datetime import datetime
from typing import Any, Mapping, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from utils.safe_sql import build_safe_set_clause, MAB_DEVICE_UPDATE_COLUMNS


_FULL_COLS = (
    "id, mac_address, name, description, device_type, "
    "assigned_vlan_id, enabled, expiry_date, created_at, updated_at"
)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

async def count_mab_devices(
    db: AsyncSession,
    *,
    tenant_id: str,
    enabled: Optional[bool] = None,
    device_type: Optional[str] = None,
) -> int:
    where, params = _filter_clause(tenant_id, enabled, device_type)
    result = await db.execute(
        text(f"SELECT COUNT(*) FROM mab_devices WHERE {where}"), params,
    )
    return int(result.scalar() or 0)


async def list_mab_devices(
    db: AsyncSession,
    *,
    tenant_id: str,
    enabled: Optional[bool] = None,
    device_type: Optional[str] = None,
    limit: int,
    offset: int,
) -> list[Mapping[str, Any]]:
    where, params = _filter_clause(tenant_id, enabled, device_type)
    params["limit"] = limit
    params["offset"] = offset
    result = await db.execute(
        text(
            f"SELECT {_FULL_COLS} FROM mab_devices WHERE {where} "
            f"ORDER BY name, mac_address "
            f"LIMIT :limit OFFSET :offset"
        ),
        params,
    )
    return list(result.mappings().all())


async def lookup_mab_device(
    db: AsyncSession, *, tenant_id: str, device_id: UUID,
) -> Optional[Mapping[str, Any]]:
    result = await db.execute(
        text(
            f"SELECT {_FULL_COLS} FROM mab_devices "
            f"WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(device_id), "tenant_id": tenant_id},
    )
    return result.mappings().first()


async def lookup_mab_device_summary(
    db: AsyncSession, *, tenant_id: str, device_id: UUID,
) -> Optional[Mapping[str, Any]]:
    """Light-touch lookup used by update/delete to fetch audit-context."""
    result = await db.execute(
        text(
            "SELECT id, mac_address, name FROM mab_devices "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(device_id), "tenant_id": tenant_id},
    )
    return result.mappings().first()


async def radius_lookup_mac(
    db: AsyncSession, *, normalized_mac: str,
) -> Optional[Mapping[str, Any]]:
    """Used by the FreeRADIUS authorize hook — global (no tenant filter).

    Only returns enabled, non-expired devices.
    """
    result = await db.execute(
        text(
            "SELECT id, mac_address, name, device_type, assigned_vlan_id, enabled "
            "FROM mab_devices "
            "WHERE mac_address = :mac::macaddr "
            "AND enabled = true "
            "AND (expiry_date IS NULL OR expiry_date > NOW()) "
            "LIMIT 1"
        ),
        {"mac": normalized_mac},
    )
    return result.mappings().first()


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

async def insert_mab_device(
    db: AsyncSession,
    *,
    tenant_id: str,
    created_by: str,
    mac_address: str,
    name: Optional[str],
    description: Optional[str],
    device_type: Optional[str],
    assigned_vlan_id: Optional[int],
    enabled: bool,
    expiry_date: Optional[datetime],
) -> Mapping[str, Any]:
    result = await db.execute(
        text(
            "INSERT INTO mab_devices "
            "(mac_address, name, description, device_type, "
            "assigned_vlan_id, enabled, expiry_date, tenant_id, created_by) "
            "VALUES (:mac::macaddr, :name, :description, :device_type, "
            ":assigned_vlan_id, :enabled, :expiry_date, :tenant_id, :created_by) "
            f"RETURNING {_FULL_COLS}"
        ),
        {
            "mac": mac_address,
            "name": name,
            "description": description,
            "device_type": device_type,
            "assigned_vlan_id": assigned_vlan_id,
            "enabled": enabled,
            "expiry_date": expiry_date,
            "tenant_id": tenant_id,
            "created_by": created_by,
        },
    )
    row = result.mappings().first()
    if row is None:
        raise RuntimeError("INSERT mab_devices RETURNING produced no row")
    return row


async def update_mab_device(
    db: AsyncSession, *, tenant_id: str, device_id: UUID, updates: dict,
) -> Optional[Mapping[str, Any]]:
    """Partial update. Returns row or None; raises ValueError on no allowed cols."""
    set_clause, params = build_safe_set_clause(updates, MAB_DEVICE_UPDATE_COLUMNS)
    params["id"] = str(device_id)
    params["tenant_id"] = tenant_id
    result = await db.execute(
        text(
            f"UPDATE mab_devices SET {set_clause}, updated_at = NOW() "
            f"WHERE id = :id AND tenant_id = :tenant_id "
            f"RETURNING {_FULL_COLS}"
        ),
        params,
    )
    return result.mappings().first()


async def delete_mab_device(
    db: AsyncSession, *, tenant_id: str, device_id: UUID,
) -> None:
    await db.execute(
        text("DELETE FROM mab_devices WHERE id = :id AND tenant_id = :tenant_id"),
        {"id": str(device_id), "tenant_id": tenant_id},
    )


async def bulk_insert_mab_device(
    db: AsyncSession,
    *,
    tenant_id: str,
    created_by: str,
    mac_address: str,
    name: Optional[str],
    device_type: Optional[str],
    assigned_vlan_id: Optional[int],
) -> bool:
    """Insert one MAB device with ON CONFLICT DO NOTHING.

    Returns True if a row was actually inserted, False if it conflicted
    on (mac_address, tenant_id).
    """
    result = await db.execute(
        text(
            "INSERT INTO mab_devices "
            "(mac_address, name, device_type, assigned_vlan_id, "
            "enabled, tenant_id, created_by) "
            "VALUES (:mac::macaddr, :name, :device_type, "
            ":assigned_vlan_id, true, :tenant_id, :created_by) "
            "ON CONFLICT (mac_address, tenant_id) DO NOTHING "
            "RETURNING id"
        ),
        {
            "mac": mac_address,
            "name": name,
            "device_type": device_type,
            "assigned_vlan_id": assigned_vlan_id,
            "tenant_id": tenant_id,
            "created_by": created_by,
        },
    )
    return result.first() is not None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _filter_clause(
    tenant_id: str,
    enabled: Optional[bool],
    device_type: Optional[str],
) -> tuple[str, dict]:
    conditions = ["tenant_id = :tenant_id"]
    params: dict = {"tenant_id": tenant_id}
    if enabled is not None:
        conditions.append("enabled = :enabled")
        params["enabled"] = enabled
    if device_type:
        conditions.append("device_type = :device_type")
        params["device_type"] = device_type
    return " AND ".join(conditions), params
