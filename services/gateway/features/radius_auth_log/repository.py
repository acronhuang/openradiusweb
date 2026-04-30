"""Database atoms for the radius_auth_log feature.

Each atom is one SQL statement against the TimescaleDB hypertable
``radius_auth_log`` (or the troubleshooting catalog ``radius_failure_catalog``).
The service layer composes these into use cases.

The free-form filter set used by the list/export endpoints is built by
``_build_log_where`` so the column → filter mapping stays in one place.
"""
from datetime import datetime
from typing import Any, Mapping
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# Sortable / projected columns (validated at the boundary, not in SQL)
# ---------------------------------------------------------------------------

ALLOWED_SORT_COLUMNS = frozenset({
    "timestamp", "auth_result", "username", "calling_station_id",
    "nas_ip", "auth_method", "failure_reason", "processing_time_ms",
})

_LIST_PROJECTION = (
    "id, timestamp, session_id, request_type, auth_result, "
    "auth_method, eap_type, failure_reason, failure_code, "
    "ad_error_code, ad_error_message, radius_reply_message, "
    "calling_station_id, username, user_domain, "
    "nas_ip, nas_port, nas_port_id, nas_identifier, "
    "assigned_vlan, assigned_vlan_name, filter_id, "
    "client_cert_cn, processing_time_ms, policy_matched"
)

_LIVE_PROJECTION = (
    "id, timestamp, auth_result, auth_method, "
    "calling_station_id, username, nas_ip, nas_port_id, "
    "nas_identifier, failure_reason, assigned_vlan, "
    "processing_time_ms"
)

_RELATED_PROJECTION = (
    "id, timestamp, auth_result, auth_method, failure_reason, "
    "username, nas_ip, nas_port_id"
)


# ---------------------------------------------------------------------------
# Filter → WHERE composition
# ---------------------------------------------------------------------------

def _build_log_where(filters: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """Translate a filters dict into a SQL WHERE fragment + bound params.

    Returned ``where`` always starts with "1=1" so it can be appended to
    safely. All values are bound; only column names are emitted as SQL.
    """
    conditions = ["1=1"]
    params: dict[str, Any] = {}

    if filters.get("since"):
        conditions.append("timestamp >= :since")
        params["since"] = filters["since"]
    if filters.get("start_time"):
        conditions.append("timestamp >= :start_time")
        params["start_time"] = filters["start_time"]
    if filters.get("end_time"):
        conditions.append("timestamp <= :end_time")
        params["end_time"] = filters["end_time"]
    if filters.get("auth_result"):
        conditions.append("auth_result = :auth_result")
        params["auth_result"] = filters["auth_result"]
    if filters.get("calling_station_id"):
        conditions.append("calling_station_id ILIKE :mac")
        params["mac"] = f"%{filters['calling_station_id']}%"
    if filters.get("username"):
        conditions.append("username ILIKE :username")
        params["username"] = f"%{filters['username']}%"
    if filters.get("nas_ip"):
        # Use CAST(:name AS type) form, not the trailing :: typecast —
        # asyncpg's named-param preprocessor mis-parses the latter.
        # See tests/unit/test_no_inline_inet_cast.py.
        conditions.append("nas_ip = CAST(:nas_ip AS inet)")
        params["nas_ip"] = filters["nas_ip"]
    if filters.get("nas_port_id"):
        conditions.append("nas_port_id ILIKE :nas_port_id")
        params["nas_port_id"] = f"%{filters['nas_port_id']}%"
    if filters.get("auth_method"):
        conditions.append("auth_method ILIKE :auth_method")
        params["auth_method"] = f"%{filters['auth_method']}%"
    if filters.get("failure_reason"):
        conditions.append("failure_reason ILIKE :failure_reason")
        params["failure_reason"] = f"%{filters['failure_reason']}%"
    if filters.get("search"):
        conditions.append(
            "(username ILIKE :search OR calling_station_id ILIKE :search "
            "OR nas_identifier ILIKE :search OR nas_ip::text LIKE :search)"
        )
        params["search"] = f"%{filters['search']}%"

    return " AND ".join(conditions), params


# ---------------------------------------------------------------------------
# List / detail
# ---------------------------------------------------------------------------

async def count_logs(
    db: AsyncSession, filters: Mapping[str, Any],
) -> int:
    where, params = _build_log_where(filters)
    result = await db.execute(
        text(f"SELECT COUNT(*) FROM radius_auth_log WHERE {where}"),
        params,
    )
    return int(result.scalar() or 0)


async def list_logs(
    db: AsyncSession,
    filters: Mapping[str, Any],
    *,
    sort_by: str,
    sort_order: str,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    where, params = _build_log_where(filters)
    params["limit"] = limit
    params["offset"] = offset
    # sort_by + sort_order are validated by the service; safe to interpolate
    result = await db.execute(
        text(
            f"SELECT {_LIST_PROJECTION} FROM radius_auth_log WHERE {where} "
            f"ORDER BY {sort_by} {sort_order} LIMIT :limit OFFSET :offset"
        ),
        params,
    )
    return [dict(r) for r in result.mappings().all()]


async def get_log_by_id(
    db: AsyncSession, log_id: UUID,
) -> dict[str, Any] | None:
    result = await db.execute(
        text("SELECT * FROM radius_auth_log WHERE id = :id"),
        {"id": str(log_id)},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def list_related_by_mac(
    db: AsyncSession, *, mac: str, exclude_id: UUID, limit: int = 10,
) -> list[dict[str, Any]]:
    result = await db.execute(
        text(
            f"SELECT {_RELATED_PROJECTION} FROM radius_auth_log "
            f"WHERE calling_station_id = :mac AND id != :current_id "
            f"ORDER BY timestamp DESC LIMIT :lim"
        ),
        {"mac": mac, "current_id": str(exclude_id), "lim": limit},
    )
    return [dict(r) for r in result.mappings().all()]


# ---------------------------------------------------------------------------
# Failure catalog
# ---------------------------------------------------------------------------

async def find_failure_catalog_entry(
    db: AsyncSession, *, code: str, description_keyword: str,
) -> dict[str, Any] | None:
    """Match by failure_code OR description ILIKE keyword. First hit wins."""
    result = await db.execute(
        text(
            "SELECT * FROM radius_failure_catalog "
            "WHERE failure_code = :code OR description ILIKE :desc LIMIT 1"
        ),
        {"code": code, "desc": f"%{description_keyword}%"},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def list_failure_catalog(
    db: AsyncSession,
    *,
    category: str | None = None,
    search: str | None = None,
) -> list[dict[str, Any]]:
    conditions = ["1=1"]
    params: dict[str, Any] = {}
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
    return [dict(r) for r in result.mappings().all()]


# ---------------------------------------------------------------------------
# Stats: summary
# ---------------------------------------------------------------------------

async def count_by_result(
    db: AsyncSession, *, since: datetime,
) -> dict[str, int]:
    result = await db.execute(
        text(
            "SELECT auth_result, COUNT(*) AS cnt "
            "FROM radius_auth_log WHERE timestamp >= :since "
            "GROUP BY auth_result ORDER BY cnt DESC"
        ),
        {"since": since},
    )
    return {r["auth_result"]: r["cnt"] for r in result.mappings().all()}


async def top_failure_reasons(
    db: AsyncSession, *, since: datetime, limit: int = 10,
) -> list[dict[str, Any]]:
    result = await db.execute(
        text(
            "SELECT failure_reason, COUNT(*) AS count "
            "FROM radius_auth_log "
            "WHERE timestamp >= :since AND auth_result != 'success' "
            "AND failure_reason IS NOT NULL "
            "GROUP BY failure_reason ORDER BY count DESC LIMIT :lim"
        ),
        {"since": since, "lim": limit},
    )
    return [dict(r) for r in result.mappings().all()]


async def top_failing_users(
    db: AsyncSession, *, since: datetime, limit: int = 10,
) -> list[dict[str, Any]]:
    result = await db.execute(
        text(
            "SELECT username, COUNT(*) AS failure_count, "
            "array_agg(DISTINCT failure_reason) AS reasons "
            "FROM radius_auth_log "
            "WHERE timestamp >= :since AND auth_result != 'success' "
            "AND username IS NOT NULL "
            "GROUP BY username ORDER BY failure_count DESC LIMIT :lim"
        ),
        {"since": since, "lim": limit},
    )
    return [dict(r) for r in result.mappings().all()]


async def top_failing_macs(
    db: AsyncSession, *, since: datetime, limit: int = 10,
) -> list[dict[str, Any]]:
    result = await db.execute(
        text(
            "SELECT calling_station_id, COUNT(*) AS failure_count, "
            "array_agg(DISTINCT failure_reason) AS reasons, "
            "MAX(username) AS last_username "
            "FROM radius_auth_log "
            "WHERE timestamp >= :since AND auth_result != 'success' "
            "AND calling_station_id IS NOT NULL "
            "GROUP BY calling_station_id "
            "ORDER BY failure_count DESC LIMIT :lim"
        ),
        {"since": since, "lim": limit},
    )
    return [dict(r) for r in result.mappings().all()]


async def auth_method_distribution(
    db: AsyncSession, *, since: datetime,
) -> list[dict[str, Any]]:
    result = await db.execute(
        text(
            "SELECT auth_method, auth_result, COUNT(*) AS count "
            "FROM radius_auth_log WHERE timestamp >= :since "
            "AND auth_method IS NOT NULL "
            "GROUP BY auth_method, auth_result ORDER BY count DESC"
        ),
        {"since": since},
    )
    return [dict(r) for r in result.mappings().all()]


async def hourly_trend(
    db: AsyncSession, *, since: datetime,
) -> list[dict[str, Any]]:
    """Uses TimescaleDB ``time_bucket`` for 1-hour buckets."""
    result = await db.execute(
        text(
            "SELECT time_bucket('1 hour', timestamp) AS hour, "
            "auth_result, COUNT(*) AS count "
            "FROM radius_auth_log WHERE timestamp >= :since "
            "GROUP BY hour, auth_result ORDER BY hour"
        ),
        {"since": since},
    )
    return [dict(r) for r in result.mappings().all()]


# ---------------------------------------------------------------------------
# Stats: by-nas / by-failure-category
# ---------------------------------------------------------------------------

async def stats_by_nas(
    db: AsyncSession, *, since: datetime,
) -> list[dict[str, Any]]:
    result = await db.execute(
        text(
            "SELECT nas_ip, nas_identifier, "
            "COUNT(*) AS total, "
            "COUNT(*) FILTER (WHERE auth_result = 'success') AS success, "
            "COUNT(*) FILTER (WHERE auth_result != 'success') AS failures, "
            "array_agg(DISTINCT auth_method) "
            "  FILTER (WHERE auth_method IS NOT NULL) AS methods "
            "FROM radius_auth_log WHERE timestamp >= :since "
            "GROUP BY nas_ip, nas_identifier "
            "ORDER BY failures DESC, total DESC"
        ),
        {"since": since},
    )
    return [dict(r) for r in result.mappings().all()]


async def stats_by_failure_category(
    db: AsyncSession, *, since: datetime,
) -> list[dict[str, Any]]:
    result = await db.execute(
        text(
            "SELECT fc.category, fc.severity, "
            "COUNT(ral.id) AS count, "
            "array_agg(DISTINCT ral.failure_reason) AS reasons "
            "FROM radius_auth_log ral "
            "LEFT JOIN radius_failure_catalog fc "
            "  ON ral.ad_error_code = fc.failure_code "
            "  OR ral.failure_reason ILIKE '%' || fc.failure_code || '%' "
            "WHERE ral.timestamp >= :since AND ral.auth_result != 'success' "
            "GROUP BY fc.category, fc.severity "
            "ORDER BY count DESC"
        ),
        {"since": since},
    )
    return [dict(r) for r in result.mappings().all()]


# ---------------------------------------------------------------------------
# Live feed / export
# ---------------------------------------------------------------------------

async def live_feed(
    db: AsyncSession, *, since: datetime, limit: int = 100,
) -> list[dict[str, Any]]:
    result = await db.execute(
        text(
            f"SELECT {_LIVE_PROJECTION} FROM radius_auth_log "
            f"WHERE timestamp >= :since "
            f"ORDER BY timestamp DESC LIMIT :lim"
        ),
        {"since": since, "lim": limit},
    )
    return [dict(r) for r in result.mappings().all()]


async def list_logs_for_export(
    db: AsyncSession,
    filters: Mapping[str, Any],
    *,
    limit: int = 10000,
) -> list[dict[str, Any]]:
    """Full-row export with hard upper limit (matches legacy 10k cap)."""
    where, params = _build_log_where(filters)
    params["lim"] = limit
    result = await db.execute(
        text(
            f"SELECT * FROM radius_auth_log WHERE {where} "
            f"ORDER BY timestamp DESC LIMIT :lim"
        ),
        params,
    )
    return [dict(r) for r in result.mappings().all()]
