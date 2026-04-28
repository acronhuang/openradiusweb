"""Database atoms for the audit feature.

`audit_log` is a TimescaleDB hypertable; queries are tenant-scoped via
`(tenant_id = :uuid OR tenant_id IS NULL)` so system-wide entries (e.g.
unauthenticated login failures) remain visible to all tenants.

All atoms join `users` left-side so the response always carries the
username when it's known.
"""
from datetime import datetime
from typing import Any, Mapping, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# Tenant filter shared by every read in this module.
_TENANT_CLAUSE = (
    "(a.tenant_id = CAST(:tenant_id AS uuid) OR a.tenant_id IS NULL)"
)

# Standard projection for list/export (no tenant_id — caller already knows it).
_LIST_COLS = (
    "a.id, a.timestamp, a.user_id, u.username, "
    "a.action, a.resource_type, a.resource_id, a.details, "
    "a.ip_address"
)


# ---------------------------------------------------------------------------
# Single-row read
# ---------------------------------------------------------------------------

async def lookup_audit_log(
    db: AsyncSession, *, tenant_id: Optional[str], log_id: UUID,
) -> Optional[Mapping[str, Any]]:
    result = await db.execute(
        text(
            f"SELECT {_LIST_COLS}, a.tenant_id FROM audit_log a "
            f"LEFT JOIN users u ON a.user_id = u.id "
            f"WHERE a.id = :id AND {_TENANT_CLAUSE}"
        ),
        {"id": str(log_id), "tenant_id": tenant_id},
    )
    return result.mappings().first()


# ---------------------------------------------------------------------------
# List (paginated) — supports a wide filter surface
# ---------------------------------------------------------------------------

async def count_audit_logs(
    db: AsyncSession, *, tenant_id: Optional[str], filters: dict,
) -> int:
    where, params = _filter_clause(tenant_id, filters)
    result = await db.execute(
        text(f"SELECT COUNT(*) FROM audit_log a WHERE {where}"), params,
    )
    return int(result.scalar() or 0)


async def list_audit_logs(
    db: AsyncSession,
    *,
    tenant_id: Optional[str],
    filters: dict,
    limit: int,
    offset: int,
) -> list[Mapping[str, Any]]:
    where, params = _filter_clause(tenant_id, filters)
    params["limit"] = limit
    params["offset"] = offset
    result = await db.execute(
        text(
            f"SELECT {_LIST_COLS} FROM audit_log a "
            f"LEFT JOIN users u ON a.user_id = u.id "
            f"WHERE {where} "
            f"ORDER BY a.timestamp DESC "
            f"LIMIT :limit OFFSET :offset"
        ),
        params,
    )
    return [dict(r._mapping) for r in result]


# ---------------------------------------------------------------------------
# Export (bounded by time + optional action/resource filters)
# ---------------------------------------------------------------------------

EXPORT_HARD_LIMIT = 10_000


async def list_audit_logs_for_export(
    db: AsyncSession,
    *,
    tenant_id: Optional[str],
    start_time: datetime,
    end_time: datetime,
    action: Optional[str],
    resource_type: Optional[str],
) -> list[Mapping[str, Any]]:
    """Bounded list for export — caps at EXPORT_HARD_LIMIT rows."""
    conditions = [
        "a.timestamp >= :start_time",
        "a.timestamp <= :end_time",
        _TENANT_CLAUSE,
    ]
    params: dict = {
        "start_time": start_time,
        "end_time": end_time,
        "tenant_id": tenant_id,
    }
    if action:
        conditions.append("a.action = :action")
        params["action"] = action
    if resource_type:
        conditions.append("a.resource_type = :resource_type")
        params["resource_type"] = resource_type
    params["limit"] = EXPORT_HARD_LIMIT

    result = await db.execute(
        text(
            f"SELECT {_LIST_COLS} FROM audit_log a "
            f"LEFT JOIN users u ON a.user_id = u.id "
            f"WHERE {' AND '.join(conditions)} "
            f"ORDER BY a.timestamp DESC LIMIT :limit"
        ),
        params,
    )
    return [dict(r._mapping) for r in result]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _filter_clause(tenant_id: Optional[str], filters: dict) -> tuple[str, dict]:
    """Build the WHERE clause for the list endpoint's filter surface."""
    conditions = [_TENANT_CLAUSE]
    params: dict = {"tenant_id": tenant_id}

    since = filters.get("since")
    start_time = filters.get("start_time")
    end_time = filters.get("end_time")
    if since is not None:
        conditions.append("a.timestamp >= :since")
        params["since"] = since
    else:
        if start_time is not None:
            conditions.append("a.timestamp >= :start_time")
            params["start_time"] = start_time
        if end_time is not None:
            conditions.append("a.timestamp <= :end_time")
            params["end_time"] = end_time

    if filters.get("user_id") is not None:
        conditions.append("a.user_id = :user_id")
        params["user_id"] = str(filters["user_id"])
    if filters.get("action"):
        conditions.append("a.action = :action")
        params["action"] = filters["action"]
    if filters.get("resource_type"):
        conditions.append("a.resource_type = :resource_type")
        params["resource_type"] = filters["resource_type"]
    if filters.get("search"):
        conditions.append("a.details::text ILIKE :search")
        params["search"] = f"%{filters['search']}%"

    return " AND ".join(conditions), params
