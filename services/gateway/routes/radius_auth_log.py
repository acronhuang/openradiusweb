"""
RADIUS Authentication Log (Access Tracker) routes.

Provides ClearPass-like authentication log querying:
- View all authentication attempts (success/failure)
- Filter by MAC, username, NAS, auth method, result
- Detailed failure reason with AD error codes
- Failure statistics and trends
- Export support
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.database import get_db
from middleware.auth import get_current_user

router = APIRouter(prefix="/radius/auth-log")


# ============================================================
# Authentication Log Queries
# ============================================================

@router.get("")
async def list_auth_logs(
    # Pagination
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    # Time range
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    last_hours: int | None = Query(None, ge=1, le=720, description="Last N hours"),
    # Filters
    auth_result: str | None = Query(None, description="success, reject, timeout, error"),
    calling_station_id: str | None = Query(None, description="Client MAC address"),
    username: str | None = Query(None, description="802.1X username"),
    nas_ip: str | None = Query(None, description="Switch/AP IP"),
    nas_port_id: str | None = Query(None, description="Switch port (e.g., Gi1/0/1)"),
    auth_method: str | None = Query(None, description="EAP-TLS, PEAP, MAB"),
    failure_reason: str | None = Query(None, description="Failure reason keyword"),
    search: str | None = Query(None, description="Search username, MAC, or NAS"),
    # Sorting
    sort_by: str = Query("timestamp", description="Sort field"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """
    List RADIUS authentication log entries with filtering.
    Similar to ClearPass Access Tracker.
    """
    conditions = ["1=1"]
    params: dict = {}

    # Time range
    if last_hours:
        conditions.append("timestamp >= :since")
        params["since"] = datetime.now(timezone.utc) - timedelta(hours=last_hours)
    else:
        if start_time:
            conditions.append("timestamp >= :start_time")
            params["start_time"] = start_time
        if end_time:
            conditions.append("timestamp <= :end_time")
            params["end_time"] = end_time

    # Filters
    if auth_result:
        conditions.append("auth_result = :auth_result")
        params["auth_result"] = auth_result
    if calling_station_id:
        conditions.append("calling_station_id ILIKE :mac")
        params["mac"] = f"%{calling_station_id}%"
    if username:
        conditions.append("username ILIKE :username")
        params["username"] = f"%{username}%"
    if nas_ip:
        conditions.append("nas_ip = :nas_ip::inet")
        params["nas_ip"] = nas_ip
    if nas_port_id:
        conditions.append("nas_port_id ILIKE :nas_port_id")
        params["nas_port_id"] = f"%{nas_port_id}%"
    if auth_method:
        conditions.append("auth_method ILIKE :auth_method")
        params["auth_method"] = f"%{auth_method}%"
    if failure_reason:
        conditions.append("failure_reason ILIKE :failure_reason")
        params["failure_reason"] = f"%{failure_reason}%"
    if search:
        conditions.append(
            "(username ILIKE :search OR calling_station_id ILIKE :search "
            "OR nas_identifier ILIKE :search OR nas_ip::text LIKE :search)"
        )
        params["search"] = f"%{search}%"

    where = " AND ".join(conditions)

    # Validate sort field
    allowed_sort = {
        "timestamp", "auth_result", "username", "calling_station_id",
        "nas_ip", "auth_method", "failure_reason", "processing_time_ms",
    }
    if sort_by not in allowed_sort:
        sort_by = "timestamp"

    # Count
    count_result = await db.execute(
        text(f"SELECT COUNT(*) FROM radius_auth_log WHERE {where}"), params
    )
    total = count_result.scalar()

    # Fetch page
    offset = (page - 1) * page_size
    params["limit"] = page_size
    params["offset"] = offset

    result = await db.execute(
        text(
            f"SELECT id, timestamp, session_id, request_type, auth_result, "
            f"auth_method, eap_type, failure_reason, failure_code, "
            f"ad_error_code, ad_error_message, radius_reply_message, "
            f"calling_station_id, username, user_domain, "
            f"nas_ip, nas_port, nas_port_id, nas_identifier, "
            f"assigned_vlan, assigned_vlan_name, filter_id, "
            f"client_cert_cn, processing_time_ms, policy_matched "
            f"FROM radius_auth_log WHERE {where} "
            f"ORDER BY {sort_by} {sort_order} "
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
        "pages": (total + page_size - 1) // page_size if total > 0 else 0,
    }


@router.get("/detail/{log_id}")
async def get_auth_log_detail(
    log_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """
    Get full details of a single authentication attempt.
    Includes all RADIUS attributes, certificate info, and troubleshooting guidance.
    """
    result = await db.execute(
        text("SELECT * FROM radius_auth_log WHERE id = :id"),
        {"id": str(log_id)},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Auth log entry not found")

    entry = dict(row)

    # If auth failed, look up failure reason details from catalog
    troubleshooting = None
    if entry.get("auth_result") != "success" and entry.get("failure_reason"):
        catalog_result = await db.execute(
            text(
                "SELECT * FROM radius_failure_catalog "
                "WHERE failure_code = :code OR description ILIKE :desc "
                "LIMIT 1"
            ),
            {
                "code": entry.get("ad_error_code") or entry.get("failure_reason", ""),
                "desc": f"%{entry.get('failure_reason', '')[:50]}%",
            },
        )
        catalog_entry = catalog_result.mappings().first()
        if catalog_entry:
            troubleshooting = {
                "category": catalog_entry["category"],
                "description": catalog_entry["description"],
                "possible_causes": catalog_entry["possible_causes"],
                "remediation_steps": catalog_entry["remediation_steps"],
                "severity": catalog_entry["severity"],
                "kb_url": catalog_entry.get("kb_url"),
            }

    # Get related auth history for this MAC/username
    related = []
    if entry.get("calling_station_id"):
        related_result = await db.execute(
            text(
                "SELECT id, timestamp, auth_result, auth_method, failure_reason, "
                "username, nas_ip, nas_port_id "
                "FROM radius_auth_log "
                "WHERE calling_station_id = :mac AND id != :current_id "
                "ORDER BY timestamp DESC LIMIT 10"
            ),
            {
                "mac": entry["calling_station_id"],
                "current_id": str(log_id),
            },
        )
        related = [dict(r._mapping) for r in related_result]

    return {
        "entry": entry,
        "troubleshooting": troubleshooting,
        "related_history": related,
    }


# ============================================================
# Failure Analysis & Statistics
# ============================================================

@router.get("/stats/summary")
async def auth_stats_summary(
    last_hours: int = Query(24, ge=1, le=720),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """
    Authentication statistics summary.
    Shows success/failure counts, top failure reasons, trends.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=last_hours)

    # Overall counts by result
    result_counts = await db.execute(
        text(
            "SELECT auth_result, COUNT(*) as count "
            "FROM radius_auth_log WHERE timestamp >= :since "
            "GROUP BY auth_result ORDER BY count DESC"
        ),
        {"since": since},
    )
    by_result = {r["auth_result"]: r["count"] for r in result_counts.mappings()}

    # Top failure reasons
    failure_reasons = await db.execute(
        text(
            "SELECT failure_reason, COUNT(*) as count "
            "FROM radius_auth_log "
            "WHERE timestamp >= :since AND auth_result != 'success' "
            "AND failure_reason IS NOT NULL "
            "GROUP BY failure_reason ORDER BY count DESC LIMIT 10"
        ),
        {"since": since},
    )
    top_failures = [dict(r._mapping) for r in failure_reasons]

    # Top failing users
    failing_users = await db.execute(
        text(
            "SELECT username, COUNT(*) as failure_count, "
            "array_agg(DISTINCT failure_reason) as reasons "
            "FROM radius_auth_log "
            "WHERE timestamp >= :since AND auth_result != 'success' "
            "AND username IS NOT NULL "
            "GROUP BY username ORDER BY failure_count DESC LIMIT 10"
        ),
        {"since": since},
    )
    top_failing_users = [dict(r._mapping) for r in failing_users]

    # Top failing MACs
    failing_macs = await db.execute(
        text(
            "SELECT calling_station_id, COUNT(*) as failure_count, "
            "array_agg(DISTINCT failure_reason) as reasons, "
            "MAX(username) as last_username "
            "FROM radius_auth_log "
            "WHERE timestamp >= :since AND auth_result != 'success' "
            "AND calling_station_id IS NOT NULL "
            "GROUP BY calling_station_id ORDER BY failure_count DESC LIMIT 10"
        ),
        {"since": since},
    )
    top_failing_macs = [dict(r._mapping) for r in failing_macs]

    # Auth method distribution
    method_dist = await db.execute(
        text(
            "SELECT auth_method, auth_result, COUNT(*) as count "
            "FROM radius_auth_log WHERE timestamp >= :since "
            "AND auth_method IS NOT NULL "
            "GROUP BY auth_method, auth_result ORDER BY count DESC"
        ),
        {"since": since},
    )
    by_method = [dict(r._mapping) for r in method_dist]

    # Hourly trend
    trend = await db.execute(
        text(
            "SELECT time_bucket('1 hour', timestamp) as hour, "
            "auth_result, COUNT(*) as count "
            "FROM radius_auth_log WHERE timestamp >= :since "
            "GROUP BY hour, auth_result ORDER BY hour"
        ),
        {"since": since},
    )
    hourly_trend = [dict(r._mapping) for r in trend]

    total = sum(by_result.values())
    success = by_result.get("success", 0)

    return {
        "period_hours": last_hours,
        "total_attempts": total,
        "success_count": success,
        "failure_count": total - success,
        "success_rate": round(success / total * 100, 1) if total > 0 else 0,
        "by_result": by_result,
        "top_failure_reasons": top_failures,
        "top_failing_users": top_failing_users,
        "top_failing_macs": top_failing_macs,
        "by_auth_method": by_method,
        "hourly_trend": hourly_trend,
    }


@router.get("/stats/by-nas")
async def auth_stats_by_nas(
    last_hours: int = Query(24, ge=1, le=720),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Authentication statistics grouped by NAS (switch/AP)."""
    since = datetime.now(timezone.utc) - timedelta(hours=last_hours)

    result = await db.execute(
        text(
            "SELECT nas_ip, nas_identifier, "
            "COUNT(*) as total, "
            "COUNT(*) FILTER (WHERE auth_result = 'success') as success, "
            "COUNT(*) FILTER (WHERE auth_result != 'success') as failures, "
            "array_agg(DISTINCT auth_method) FILTER (WHERE auth_method IS NOT NULL) as methods "
            "FROM radius_auth_log WHERE timestamp >= :since "
            "GROUP BY nas_ip, nas_identifier "
            "ORDER BY failures DESC, total DESC"
        ),
        {"since": since},
    )
    rows = [dict(r._mapping) for r in result]
    return {"items": rows, "period_hours": last_hours}


@router.get("/stats/by-failure-category")
async def auth_stats_by_failure_category(
    last_hours: int = Query(24, ge=1, le=720),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Failure statistics grouped by category (credential, certificate, network, etc.)."""
    since = datetime.now(timezone.utc) - timedelta(hours=last_hours)

    result = await db.execute(
        text(
            "SELECT fc.category, fc.severity, "
            "COUNT(ral.id) as count, "
            "array_agg(DISTINCT ral.failure_reason) as reasons "
            "FROM radius_auth_log ral "
            "LEFT JOIN radius_failure_catalog fc "
            "ON ral.ad_error_code = fc.failure_code "
            "OR ral.failure_reason ILIKE '%' || fc.failure_code || '%' "
            "WHERE ral.timestamp >= :since AND ral.auth_result != 'success' "
            "GROUP BY fc.category, fc.severity "
            "ORDER BY count DESC"
        ),
        {"since": since},
    )
    rows = [dict(r._mapping) for r in result]
    return {"items": rows, "period_hours": last_hours}


# ============================================================
# Failure Catalog (Troubleshooting Knowledge Base)
# ============================================================

@router.get("/failure-catalog")
async def list_failure_catalog(
    category: str | None = None,
    search: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """List all known failure reasons with troubleshooting guidance."""
    conditions = ["1=1"]
    params: dict = {}

    if category:
        conditions.append("category = :category")
        params["category"] = category
    if search:
        conditions.append(
            "(failure_code ILIKE :search OR description ILIKE :search)"
        )
        params["search"] = f"%{search}%"

    where = " AND ".join(conditions)

    result = await db.execute(
        text(
            f"SELECT * FROM radius_failure_catalog WHERE {where} "
            f"ORDER BY severity DESC, category, failure_code"
        ),
        params,
    )
    rows = [dict(r._mapping) for r in result]
    return {"items": rows}


# ============================================================
# Live Authentication Feed
# ============================================================

@router.get("/live")
async def live_auth_feed(
    last_seconds: int = Query(60, ge=10, le=300),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """
    Get latest authentication events (for live dashboard).
    Returns the most recent events within the specified timeframe.
    """
    since = datetime.now(timezone.utc) - timedelta(seconds=last_seconds)

    result = await db.execute(
        text(
            "SELECT id, timestamp, auth_result, auth_method, "
            "calling_station_id, username, nas_ip, nas_port_id, "
            "nas_identifier, failure_reason, assigned_vlan, "
            "processing_time_ms "
            "FROM radius_auth_log "
            "WHERE timestamp >= :since "
            "ORDER BY timestamp DESC LIMIT 100"
        ),
        {"since": since},
    )
    rows = [dict(r._mapping) for r in result]
    return {"items": rows, "since": since.isoformat()}


# ============================================================
# Export
# ============================================================

@router.get("/export")
async def export_auth_logs(
    start_time: datetime = Query(...),
    end_time: datetime = Query(...),
    auth_result: str | None = None,
    format: str = Query("json", pattern="^(json|csv)$"),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Export authentication logs for a time range."""
    conditions = ["timestamp >= :start_time AND timestamp <= :end_time"]
    params: dict = {"start_time": start_time, "end_time": end_time}

    if auth_result:
        conditions.append("auth_result = :auth_result")
        params["auth_result"] = auth_result

    where = " AND ".join(conditions)

    result = await db.execute(
        text(
            f"SELECT * FROM radius_auth_log WHERE {where} "
            f"ORDER BY timestamp DESC LIMIT 10000"
        ),
        params,
    )
    rows = [dict(r._mapping) for r in result]

    if format == "csv":
        import csv
        import io
        from fastapi.responses import StreamingResponse

        output = io.StringIO()
        if rows:
            writer = csv.DictWriter(output, fieldnames=rows[0].keys())
            writer.writeheader()
            for row in rows:
                # Convert non-serializable types
                for k, v in row.items():
                    if isinstance(v, (datetime,)):
                        row[k] = v.isoformat()
                    elif isinstance(v, (dict, list)):
                        row[k] = str(v)
                writer.writerow(row)

        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=radius_auth_log.csv"},
        )

    return {"items": rows, "total": len(rows)}
