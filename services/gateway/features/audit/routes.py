"""HTTP routes for the audit feature (Layer 3).

Handlers stay thin. CSV serialization for /export lives here because
it's a transport-format concern, not a business-logic concern.
"""
import csv
import io
import json
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.database import get_db
from middleware.auth import get_current_user, require_operator

from . import service

router = APIRouter(prefix="/audit-log")


_CSV_FIELDS = [
    "id", "timestamp", "user_id", "username",
    "action", "resource_type", "resource_id",
    "details", "ip_address",
]


def _rows_to_csv(rows: list[dict]) -> str:
    """Stringify each cell so csv.DictWriter doesn't choke on dict / datetime."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_FIELDS)
    writer.writeheader()
    for row in rows:
        line = dict(row)
        for k, v in line.items():
            if isinstance(v, datetime):
                line[k] = v.isoformat()
            elif isinstance(v, (dict, list)):
                line[k] = json.dumps(v, default=str)
            elif v is not None:
                line[k] = str(v)
        writer.writerow(line)
    return buf.getvalue()


# ===========================================================================
# /audit-log/export — must come before /{log_id} to avoid path collision
# ===========================================================================

@router.get("/export")
async def export_audit_log(
    start_time: datetime = Query(..., description="Export start time"),
    end_time: datetime = Query(..., description="Export end time"),
    action: str | None = Query(None, description="Filter by action"),
    resource_type: str | None = Query(None, description="Filter by resource type"),
    format: str = Query("json", pattern="^(json|csv)$"),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_operator),
):
    """Export audit log entries as JSON or CSV (operator+ only)."""
    rows = await service.fetch_audit_logs_for_export(
        db,
        tenant_id=user.get("tenant_id"),
        start_time=start_time, end_time=end_time,
        action=action, resource_type=resource_type,
    )
    if format == "csv":
        return StreamingResponse(
            iter([_rows_to_csv(rows) if rows else ""]),
            media_type="text/csv",
            headers={
                "Content-Disposition": "attachment; filename=audit_log_export.csv",
            },
        )
    return {"items": rows, "total": len(rows)}


# ===========================================================================
# /audit-log/{log_id}
# ===========================================================================

@router.get("/{log_id}")
async def get_audit_log_detail(
    log_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Get a single audit log entry by ID."""
    return await service.get_audit_log(
        db, tenant_id=user.get("tenant_id"), log_id=log_id,
    )


# ===========================================================================
# /audit-log
# ===========================================================================

@router.get("")
async def list_audit_logs(
    user_id: UUID | None = Query(None, description="Filter by user ID"),
    action: str | None = Query(None, description="Filter by action"),
    resource_type: str | None = Query(None, description="Filter by resource type"),
    search: str | None = Query(None, description="Text search in details JSONB"),
    start_time: datetime | None = Query(None, description="Start of time range"),
    end_time: datetime | None = Query(None, description="End of time range"),
    last_hours: int | None = Query(None, ge=1, le=720, description="Last N hours"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """List audit log entries with pagination and filtering."""
    return await service.list_audit_logs(
        db,
        tenant_id=user.get("tenant_id"),
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        search=search,
        start_time=start_time,
        end_time=end_time,
        last_hours=last_hours,
        page=page,
        page_size=page_size,
    )
