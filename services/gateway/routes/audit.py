"""Audit log query and export routes."""

import csv
import io
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.database import get_db
from middleware.auth import get_current_user, require_operator

router = APIRouter(prefix="/audit-log")


# ============================================================
# List / Query
# ============================================================

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
    """
    Export audit log entries as JSON or CSV.
    Operator or admin access required.
    """
    conditions = [
        "a.timestamp >= :start_time",
        "a.timestamp <= :end_time",
        "(a.tenant_id = CAST(:tenant_id AS uuid) OR a.tenant_id IS NULL)",
    ]
    params: dict = {
        "start_time": start_time,
        "end_time": end_time,
        "tenant_id": user.get("tenant_id"),
    }

    if action:
        conditions.append("a.action = :action")
        params["action"] = action
    if resource_type:
        conditions.append("a.resource_type = :resource_type")
        params["resource_type"] = resource_type

    where = " AND ".join(conditions)

    result = await db.execute(
        text(
            f"SELECT a.id, a.timestamp, a.user_id, u.username, "
            f"a.action, a.resource_type, a.resource_id, a.details, "
            f"a.ip_address "
            f"FROM audit_log a "
            f"LEFT JOIN users u ON a.user_id = u.id "
            f"WHERE {where} "
            f"ORDER BY a.timestamp DESC LIMIT 10000"
        ),
        params,
    )
    rows = [dict(r._mapping) for r in result]

    if format == "csv":
        output = io.StringIO()
        if rows:
            # Flatten details for CSV
            fieldnames = [
                "id", "timestamp", "user_id", "username",
                "action", "resource_type", "resource_id",
                "details", "ip_address",
            ]
            writer = csv.DictWriter(output, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                csv_row = dict(row)
                for k, v in csv_row.items():
                    if isinstance(v, datetime):
                        csv_row[k] = v.isoformat()
                    elif isinstance(v, (dict, list)):
                        csv_row[k] = json.dumps(v, default=str)
                    elif v is not None:
                        csv_row[k] = str(v)
                writer.writerow(csv_row)

        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={
                "Content-Disposition": "attachment; filename=audit_log_export.csv"
            },
        )

    # JSON format
    return {"items": rows, "total": len(rows)}


@router.get("/{log_id}")
async def get_audit_log_detail(
    log_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """
    Get a single audit log entry by ID.
    Note: audit_log is a TimescaleDB hypertable with composite PK (id, timestamp).
    Querying by id alone still works but may scan multiple chunks.
    """
    result = await db.execute(
        text(
            "SELECT a.id, a.timestamp, a.user_id, u.username, "
            "a.action, a.resource_type, a.resource_id, a.details, "
            "a.ip_address, a.tenant_id "
            "FROM audit_log a "
            "LEFT JOIN users u ON a.user_id = u.id "
            "WHERE a.id = :id "
            "AND (a.tenant_id = CAST(:tenant_id AS uuid) OR a.tenant_id IS NULL)"
        ),
        {"id": str(log_id), "tenant_id": user.get("tenant_id")},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Audit log entry not found")

    return dict(row)


@router.get("")
async def list_audit_logs(
    # Filters
    user_id: UUID | None = Query(None, description="Filter by user ID"),
    action: str | None = Query(None, description="Filter by action (create, update, delete, login, ...)"),
    resource_type: str | None = Query(None, description="Filter by resource type"),
    search: str | None = Query(None, description="Text search in details JSONB"),
    # Time range
    start_time: datetime | None = Query(None, description="Start of time range"),
    end_time: datetime | None = Query(None, description="End of time range"),
    last_hours: int | None = Query(None, ge=1, le=720, description="Last N hours"),
    # Pagination
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """
    List audit log entries with pagination and filtering.
    Joins users table to include username.
    """
    conditions = ["(a.tenant_id = CAST(:tenant_id AS uuid) OR a.tenant_id IS NULL)"]
    params: dict = {"tenant_id": user.get("tenant_id")}

    # Time range
    if last_hours:
        conditions.append("a.timestamp >= :since")
        params["since"] = datetime.now(timezone.utc) - timedelta(hours=last_hours)
    else:
        if start_time:
            conditions.append("a.timestamp >= :start_time")
            params["start_time"] = start_time
        if end_time:
            conditions.append("a.timestamp <= :end_time")
            params["end_time"] = end_time

    # Filters
    if user_id:
        conditions.append("a.user_id = :user_id")
        params["user_id"] = str(user_id)
    if action:
        conditions.append("a.action = :action")
        params["action"] = action
    if resource_type:
        conditions.append("a.resource_type = :resource_type")
        params["resource_type"] = resource_type
    if search:
        conditions.append("a.details::text ILIKE :search")
        params["search"] = f"%{search}%"

    where = " AND ".join(conditions)

    # Count
    count_result = await db.execute(
        text(f"SELECT COUNT(*) FROM audit_log a WHERE {where}"),
        params,
    )
    total = count_result.scalar()

    # Fetch page
    offset = (page - 1) * page_size
    params["limit"] = page_size
    params["offset"] = offset

    result = await db.execute(
        text(
            f"SELECT a.id, a.timestamp, a.user_id, u.username, "
            f"a.action, a.resource_type, a.resource_id, a.details, "
            f"a.ip_address "
            f"FROM audit_log a "
            f"LEFT JOIN users u ON a.user_id = u.id "
            f"WHERE {where} "
            f"ORDER BY a.timestamp DESC "
            f"LIMIT :limit OFFSET :offset"
        ),
        params,
    )
    rows = [dict(r._mapping) for r in result]

    return {
        "items": rows,
        "total": total,
        "page": page,
        "page_size": page_size,
    }
