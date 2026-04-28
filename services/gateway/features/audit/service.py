"""Use-case composition for the audit feature (Layer 2).

Read-only feature — no mutations means no audit-of-the-audit-log
(this feature does not call `log_audit`). Three use cases:
- list with pagination + wide filter surface
- single-entry lookup
- bounded export (the route layer wraps the result in JSON or CSV)

Time-range normalization (the legacy route's `last_hours` shortcut)
lives here so the repository keeps a single, consistent filter shape.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.exceptions import NotFoundError

from . import repository as repo


# ---------------------------------------------------------------------------
# Single-row read
# ---------------------------------------------------------------------------

async def get_audit_log(
    db: AsyncSession, *, tenant_id: Optional[str], log_id: UUID,
) -> dict:
    row = await repo.lookup_audit_log(db, tenant_id=tenant_id, log_id=log_id)
    if not row:
        raise NotFoundError("Audit log entry", str(log_id))
    return dict(row)


# ---------------------------------------------------------------------------
# Paginated list
# ---------------------------------------------------------------------------

async def list_audit_logs(
    db: AsyncSession,
    *,
    tenant_id: Optional[str],
    user_id: Optional[UUID],
    action: Optional[str],
    resource_type: Optional[str],
    search: Optional[str],
    start_time: Optional[datetime],
    end_time: Optional[datetime],
    last_hours: Optional[int],
    page: int,
    page_size: int,
) -> dict:
    filters = _build_filters(
        user_id=user_id, action=action, resource_type=resource_type,
        search=search, start_time=start_time, end_time=end_time,
        last_hours=last_hours,
    )
    total = await repo.count_audit_logs(
        db, tenant_id=tenant_id, filters=filters,
    )
    rows = await repo.list_audit_logs(
        db, tenant_id=tenant_id, filters=filters,
        limit=page_size, offset=(page - 1) * page_size,
    )
    return {
        "items": rows,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# ---------------------------------------------------------------------------
# Export (returns rows; route layer chooses JSON vs. CSV)
# ---------------------------------------------------------------------------

async def fetch_audit_logs_for_export(
    db: AsyncSession,
    *,
    tenant_id: Optional[str],
    start_time: datetime,
    end_time: datetime,
    action: Optional[str],
    resource_type: Optional[str],
) -> list[dict[str, Any]]:
    rows = await repo.list_audit_logs_for_export(
        db,
        tenant_id=tenant_id,
        start_time=start_time, end_time=end_time,
        action=action, resource_type=resource_type,
    )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_filters(
    *,
    user_id: Optional[UUID],
    action: Optional[str],
    resource_type: Optional[str],
    search: Optional[str],
    start_time: Optional[datetime],
    end_time: Optional[datetime],
    last_hours: Optional[int],
) -> dict:
    """Normalize the filter surface; `last_hours` short-circuits start_time/end_time."""
    filters: dict = {
        "user_id": user_id,
        "action": action,
        "resource_type": resource_type,
        "search": search,
    }
    if last_hours:
        filters["since"] = datetime.now(timezone.utc) - timedelta(hours=last_hours)
    else:
        filters["start_time"] = start_time
        filters["end_time"] = end_time
    return filters
