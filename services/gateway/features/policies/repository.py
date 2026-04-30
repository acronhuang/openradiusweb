"""Database atoms for the policies feature.

JSONB columns (`conditions`, `match_actions`, `no_match_actions`) are
serialised by the service layer; the repo just casts them to ``::jsonb``.
"""
from typing import Any, Mapping, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from utils.safe_sql import (
    POLICY_TYPE_CASTS,
    POLICY_UPDATE_COLUMNS,
    build_safe_set_clause,
)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

async def count_policies(
    db: AsyncSession,
    *,
    tenant_id: str,
    enabled: Optional[bool] = None,
) -> int:
    where, params = _scope_where(tenant_id=tenant_id, enabled=enabled)
    result = await db.execute(
        text(f"SELECT COUNT(*) FROM policies WHERE {where}"), params,
    )
    return int(result.scalar() or 0)


async def list_policies(
    db: AsyncSession,
    *,
    tenant_id: str,
    enabled: Optional[bool],
    limit: int,
    offset: int,
) -> list[Mapping[str, Any]]:
    where, params = _scope_where(tenant_id=tenant_id, enabled=enabled)
    params["limit"] = limit
    params["offset"] = offset
    result = await db.execute(
        text(
            f"SELECT * FROM policies WHERE {where} "
            f"ORDER BY priority ASC, name ASC "
            f"LIMIT :limit OFFSET :offset"
        ),
        params,
    )
    return list(result.mappings().all())


async def list_enabled_policies(
    db: AsyncSession, *, tenant_id: str,
) -> list[Mapping[str, Any]]:
    """Used by simulate-all — full rows ordered by priority."""
    result = await db.execute(
        text(
            "SELECT * FROM policies "
            "WHERE enabled = true AND tenant_id = :tenant_id "
            "ORDER BY priority ASC"
        ),
        {"tenant_id": tenant_id},
    )
    return list(result.mappings().all())


async def lookup_policy(
    db: AsyncSession, *, tenant_id: str, policy_id: UUID,
) -> Optional[Mapping[str, Any]]:
    result = await db.execute(
        text(
            "SELECT * FROM policies "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(policy_id), "tenant_id": tenant_id},
    )
    return result.mappings().first()


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

async def insert_policy(
    db: AsyncSession,
    *,
    tenant_id: str,
    created_by: str,
    name: str,
    description: Optional[str],
    priority: int,
    conditions_json: str,
    match_actions_json: str,
    no_match_actions_json: str,
    enabled: bool,
) -> Mapping[str, Any]:
    result = await db.execute(
        text(
            "INSERT INTO policies "
            "(name, description, priority, conditions, match_actions, "
            "no_match_actions, enabled, tenant_id, created_by) "
            "VALUES (:name, :description, :priority, "
            "CAST(:conditions AS jsonb), CAST(:match_actions AS jsonb), "
            "CAST(:no_match_actions AS jsonb), :enabled, "
            ":tenant_id, :created_by) RETURNING *"
        ),
        {
            "name": name,
            "description": description,
            "priority": priority,
            "conditions": conditions_json,
            "match_actions": match_actions_json,
            "no_match_actions": no_match_actions_json,
            "enabled": enabled,
            "tenant_id": tenant_id,
            "created_by": created_by,
        },
    )
    row = result.mappings().first()
    if row is None:
        raise RuntimeError("INSERT policies RETURNING produced no row")
    return row


async def update_policy(
    db: AsyncSession,
    *,
    tenant_id: str,
    policy_id: UUID,
    updates: dict,
) -> Optional[Mapping[str, Any]]:
    """Partial update with jsonb casts on the three JSON columns.

    Raises ValueError if `updates` contains no allowed columns.
    Returns the updated row, or None if (id, tenant) didn't match.
    """
    set_clause, params = build_safe_set_clause(
        updates, POLICY_UPDATE_COLUMNS, type_casts=POLICY_TYPE_CASTS,
    )
    params["id"] = str(policy_id)
    params["tenant_id"] = tenant_id
    result = await db.execute(
        text(
            f"UPDATE policies SET {set_clause}, updated_at = NOW() "
            f"WHERE id = :id AND tenant_id = :tenant_id RETURNING *"
        ),
        params,
    )
    return result.mappings().first()


async def delete_policy(
    db: AsyncSession, *, tenant_id: str, policy_id: UUID,
) -> bool:
    """Returns True if a row was deleted, False if not found."""
    result = await db.execute(
        text(
            "DELETE FROM policies WHERE id = :id AND tenant_id = :tenant_id "
            "RETURNING id"
        ),
        {"id": str(policy_id), "tenant_id": tenant_id},
    )
    return result.first() is not None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _scope_where(
    *, tenant_id: str, enabled: Optional[bool],
) -> tuple[str, dict[str, Any]]:
    conditions = ["tenant_id = :tenant_id"]
    params: dict[str, Any] = {"tenant_id": tenant_id}
    if enabled is not None:
        conditions.append("enabled = :enabled")
        params["enabled"] = enabled
    return " AND ".join(conditions), params
