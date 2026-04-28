"""Database atoms for the group_vlan_mappings feature."""
from typing import Any, Mapping, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from utils.safe_sql import build_safe_set_clause, GROUP_VLAN_MAPPING_UPDATE_COLUMNS


_FULL_COLS = (
    "id, group_name, vlan_id, priority, description, "
    "ldap_server_id, enabled, created_at, updated_at"
)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

async def list_mappings(
    db: AsyncSession, *, tenant_id: str,
) -> list[Mapping[str, Any]]:
    result = await db.execute(
        text(
            f"SELECT {_FULL_COLS} FROM group_vlan_mappings "
            f"WHERE tenant_id = :tenant_id "
            f"ORDER BY priority ASC, group_name ASC"
        ),
        {"tenant_id": tenant_id},
    )
    return list(result.mappings().all())


async def lookup_mapping(
    db: AsyncSession, *, tenant_id: str, mapping_id: UUID,
) -> Optional[Mapping[str, Any]]:
    result = await db.execute(
        text(
            f"SELECT {_FULL_COLS} FROM group_vlan_mappings "
            f"WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(mapping_id), "tenant_id": tenant_id},
    )
    return result.mappings().first()


async def lookup_mapping_summary(
    db: AsyncSession, *, tenant_id: str, mapping_id: UUID,
) -> Optional[Mapping[str, Any]]:
    """Light-touch lookup used by update/delete to fetch audit-context."""
    result = await db.execute(
        text(
            "SELECT id, group_name, vlan_id FROM group_vlan_mappings "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(mapping_id), "tenant_id": tenant_id},
    )
    return result.mappings().first()


async def group_name_taken(
    db: AsyncSession,
    *,
    tenant_id: str,
    group_name: str,
    excluding_id: Optional[UUID] = None,
) -> bool:
    """Used by create/update to enforce group_name uniqueness within a tenant."""
    sql = (
        "SELECT id FROM group_vlan_mappings "
        "WHERE group_name = :group_name AND tenant_id = :tenant_id"
    )
    params: dict = {"group_name": group_name, "tenant_id": tenant_id}
    if excluding_id is not None:
        sql += " AND id != :id"
        params["id"] = str(excluding_id)
    result = await db.execute(text(sql), params)
    return result.first() is not None


async def lookup_vlan_for_groups(
    db: AsyncSession, *, tenant_id: str, groups: list[str],
) -> Optional[Mapping[str, Any]]:
    """FreeRADIUS post_auth helper — pick highest-priority enabled match."""
    result = await db.execute(
        text(
            "SELECT group_name, vlan_id, priority FROM group_vlan_mappings "
            "WHERE group_name = ANY(:groups) "
            "AND enabled = true AND tenant_id = :tenant_id "
            "ORDER BY priority ASC LIMIT 1"
        ),
        {"groups": groups, "tenant_id": tenant_id},
    )
    return result.mappings().first()


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

async def insert_mapping(
    db: AsyncSession,
    *,
    tenant_id: str,
    group_name: str,
    vlan_id: int,
    priority: int,
    description: Optional[str],
    ldap_server_id: Optional[str],
    enabled: bool,
) -> Mapping[str, Any]:
    result = await db.execute(
        text(
            "INSERT INTO group_vlan_mappings "
            "(group_name, vlan_id, priority, description, "
            "ldap_server_id, enabled, tenant_id) "
            "VALUES (:group_name, :vlan_id, :priority, :description, "
            ":ldap_server_id, :enabled, :tenant_id) "
            f"RETURNING {_FULL_COLS}"
        ),
        {
            "group_name": group_name,
            "vlan_id": vlan_id,
            "priority": priority,
            "description": description,
            "ldap_server_id": ldap_server_id,
            "enabled": enabled,
            "tenant_id": tenant_id,
        },
    )
    row = result.mappings().first()
    if row is None:
        raise RuntimeError("INSERT group_vlan_mappings RETURNING produced no row")
    return row


async def update_mapping(
    db: AsyncSession, *, tenant_id: str, mapping_id: UUID, updates: dict,
) -> Optional[Mapping[str, Any]]:
    """Partial update. Returns row or None; raises ValueError on no allowed cols."""
    set_clause, params = build_safe_set_clause(
        updates, GROUP_VLAN_MAPPING_UPDATE_COLUMNS,
    )
    params["id"] = str(mapping_id)
    params["tenant_id"] = tenant_id
    result = await db.execute(
        text(
            f"UPDATE group_vlan_mappings SET {set_clause}, updated_at = NOW() "
            f"WHERE id = :id AND tenant_id = :tenant_id "
            f"RETURNING {_FULL_COLS}"
        ),
        params,
    )
    return result.mappings().first()


async def delete_mapping(
    db: AsyncSession, *, tenant_id: str, mapping_id: UUID,
) -> None:
    await db.execute(
        text(
            "DELETE FROM group_vlan_mappings "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(mapping_id), "tenant_id": tenant_id},
    )
