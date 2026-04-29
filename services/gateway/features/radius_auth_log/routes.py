"""HTTP layer for the radius_auth_log feature.

Thin handlers — parse → call service → wrap response. CSV serialization
for ``/export`` lives here because StreamingResponse is an HTTP concern.
"""
import csv
import io
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from middleware.auth import get_current_user
from orw_common.database import get_db

from . import service

router = APIRouter(prefix="/radius/auth-log")


# ---------------------------------------------------------------------------
# List + detail
# ---------------------------------------------------------------------------

@router.get("")
async def list_auth_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    last_hours: int | None = Query(None, ge=1, le=720, description="Last N hours"),
    auth_result: str | None = Query(None, description="success, reject, timeout, error"),
    calling_station_id: str | None = Query(None, description="Client MAC address"),
    username: str | None = Query(None, description="802.1X username"),
    nas_ip: str | None = Query(None, description="Switch/AP IP"),
    nas_port_id: str | None = Query(None, description="Switch port (e.g., Gi1/0/1)"),
    auth_method: str | None = Query(None, description="EAP-TLS, PEAP, MAB"),
    failure_reason: str | None = Query(None, description="Failure reason keyword"),
    search: str | None = Query(None, description="Search username, MAC, or NAS"),
    sort_by: str = Query("timestamp", description="Sort field"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """List RADIUS authentication log entries (ClearPass Access Tracker)."""
    return await service.list_logs(
        db,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
        filters={
            "start_time": start_time,
            "end_time": end_time,
            "last_hours": last_hours,
            "auth_result": auth_result,
            "calling_station_id": calling_station_id,
            "username": username,
            "nas_ip": nas_ip,
            "nas_port_id": nas_port_id,
            "auth_method": auth_method,
            "failure_reason": failure_reason,
            "search": search,
        },
    )


@router.get("/detail/{log_id}")
async def get_auth_log_detail(
    log_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Single auth attempt with troubleshooting + related history."""
    return await service.get_log_detail(db, log_id=log_id)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@router.get("/stats/summary")
async def auth_stats_summary(
    last_hours: int = Query(24, ge=1, le=720),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return await service.get_summary_stats(db, last_hours=last_hours)


@router.get("/stats/by-nas")
async def auth_stats_by_nas(
    last_hours: int = Query(24, ge=1, le=720),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return await service.get_stats_by_nas(db, last_hours=last_hours)


@router.get("/stats/by-failure-category")
async def auth_stats_by_failure_category(
    last_hours: int = Query(24, ge=1, le=720),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return await service.get_stats_by_failure_category(
        db, last_hours=last_hours,
    )


# ---------------------------------------------------------------------------
# Catalog + live + export
# ---------------------------------------------------------------------------

@router.get("/failure-catalog")
async def list_failure_catalog(
    category: str | None = None,
    search: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return await service.list_failure_catalog(
        db, category=category, search=search,
    )


@router.get("/live")
async def live_auth_feed(
    last_seconds: int = Query(60, ge=10, le=300),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return await service.get_live_feed(db, last_seconds=last_seconds)


@router.get("/export")
async def export_auth_logs(
    start_time: datetime = Query(...),
    end_time: datetime = Query(...),
    auth_result: str | None = None,
    format: str = Query("json", pattern="^(json|csv)$"),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    rows = await service.export_logs(
        db,
        start_time=start_time,
        end_time=end_time,
        auth_result=auth_result,
    )
    if format == "csv":
        return _csv_response(rows)
    return {"items": rows, "total": len(rows)}


def _csv_response(rows: list[dict]) -> StreamingResponse:
    output = io.StringIO()
    if rows:
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader()
        for row in rows:
            for k, v in row.items():
                if isinstance(v, datetime):
                    row[k] = v.isoformat()
                elif isinstance(v, (dict, list)):
                    row[k] = str(v)
            writer.writerow(row)
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=radius_auth_log.csv",
        },
    )
