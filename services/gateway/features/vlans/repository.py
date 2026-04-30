"""Database atoms for the vlans feature.

Each function performs a single DB statement, scoped to a tenant.
The PostgreSQL `cidr` cast for `subnet` is applied here, not in
the route handler — keeping the SQL detail with the storage atom.
"""
from typing import Any, Mapping, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from utils.safe_sql import build_safe_set_clause, VLAN_UPDATE_COLUMNS


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

_SELECT_COLS = (
    "id, vlan_id, name, description, purpose, "
    "subnet, enabled, created_at, updated_at"
)


async def list_vlans(
    db: AsyncSession, *, tenant_id: str, purpose: Optional[str] = None,
) -> list[Mapping[str, Any]]:
    where = ["tenant_id = :tenant_id"]
    params: dict = {"tenant_id": tenant_id}
    if purpose:
        where.append("purpose = :purpose")
        params["purpose"] = purpose
    result = await db.execute(
        text(
            f"SELECT {_SELECT_COLS} FROM vlans "
            f"WHERE {' AND '.join(where)} ORDER BY vlan_id"
        ),
        params,
    )
    return list(result.mappings().all())


async def lookup_vlan(
    db: AsyncSession, *, tenant_id: str, vlan_uuid: UUID,
) -> Optional[Mapping[str, Any]]:
    result = await db.execute(
        text(
            f"SELECT {_SELECT_COLS} FROM vlans "
            f"WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(vlan_uuid), "tenant_id": tenant_id},
    )
    return result.mappings().first()


async def lookup_vlan_summary(
    db: AsyncSession, *, tenant_id: str, vlan_uuid: UUID,
) -> Optional[Mapping[str, Any]]:
    """Light-touch lookup used by update/delete to fetch audit-context columns."""
    result = await db.execute(
        text(
            "SELECT id, vlan_id, name FROM vlans "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(vlan_uuid), "tenant_id": tenant_id},
    )
    return result.mappings().first()


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

async def insert_vlan(
    db: AsyncSession,
    *,
    tenant_id: str,
    vlan_id: int,
    name: str,
    description: Optional[str],
    purpose: Optional[str],
    subnet: Optional[str],
    enabled: bool,
) -> Mapping[str, Any]:
    result = await db.execute(
        text(
            "INSERT INTO vlans "
            "(vlan_id, name, description, purpose, subnet, enabled, tenant_id) "
            "VALUES (:vlan_id, :name, :description, :purpose, "
            "CAST(:subnet AS cidr), :enabled, :tenant_id) "
            f"RETURNING {_SELECT_COLS}"
        ),
        {
            "vlan_id": vlan_id,
            "name": name,
            "description": description,
            "purpose": purpose,
            "subnet": subnet,
            "enabled": enabled,
            "tenant_id": tenant_id,
        },
    )
    row = result.mappings().first()
    if row is None:
        raise RuntimeError("INSERT vlans RETURNING produced no row")
    return row


async def update_vlan(
    db: AsyncSession, *, tenant_id: str, vlan_uuid: UUID, updates: dict,
) -> Optional[Mapping[str, Any]]:
    """Apply a partial update via the safe SET clause builder.

    Returns the updated row, or None if the (id, tenant) pair didn't match.
    Raises ValueError if `updates` contains no allowed columns.
    """
    set_clause, params = build_safe_set_clause(updates, VLAN_UPDATE_COLUMNS)
    # `subnet` needs an explicit cidr cast — use the CAST(:name AS type)
    # form, not the trailing :: typecast, which asyncpg mis-parses.
    # See tests/unit/test_no_inline_inet_cast.py.
    if "subnet" in params:
        set_clause = set_clause.replace(
            "subnet = :subnet", "subnet = CAST(:subnet AS cidr)"
        )
    params["id"] = str(vlan_uuid)
    params["tenant_id"] = tenant_id
    result = await db.execute(
        text(
            f"UPDATE vlans SET {set_clause}, updated_at = NOW() "
            f"WHERE id = :id AND tenant_id = :tenant_id "
            f"RETURNING {_SELECT_COLS}"
        ),
        params,
    )
    return result.mappings().first()


async def delete_vlan(
    db: AsyncSession, *, tenant_id: str, vlan_uuid: UUID,
) -> None:
    await db.execute(
        text("DELETE FROM vlans WHERE id = :id AND tenant_id = :tenant_id"),
        {"id": str(vlan_uuid), "tenant_id": tenant_id},
    )
