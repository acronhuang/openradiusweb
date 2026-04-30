"""Database atoms for the coa feature.

This feature is read-only against the DB (it publishes via NATS for
mutations). Two read paths:
- `audit_log` for `/history` (filtered by `resource_type='coa'`)
- `radius_sessions` for `/active-sessions` with JOINs to enrich device
  + switch context
"""
from typing import Any, Mapping, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


_AUDIT_TENANT_CLAUSE = (
    "(a.tenant_id = CAST(:tenant_id AS uuid) OR a.tenant_id IS NULL)"
)


# ---------------------------------------------------------------------------
# CoA history (audit_log filtered to resource_type='coa')
# ---------------------------------------------------------------------------

async def count_coa_history(
    db: AsyncSession, *, tenant_id: Optional[str], action: Optional[str],
) -> int:
    where, params = _coa_history_clause(tenant_id, action)
    result = await db.execute(
        text(f"SELECT COUNT(*) FROM audit_log a WHERE {where}"), params,
    )
    return int(result.scalar() or 0)


async def list_coa_history(
    db: AsyncSession,
    *,
    tenant_id: Optional[str],
    action: Optional[str],
    limit: int,
    offset: int,
) -> list[Mapping[str, Any]]:
    where, params = _coa_history_clause(tenant_id, action)
    params["limit"] = limit
    params["offset"] = offset
    result = await db.execute(
        text(
            "SELECT a.id, a.timestamp, a.user_id, u.username, "
            "a.action, a.resource_type, a.details, a.ip_address "
            "FROM audit_log a "
            "LEFT JOIN users u ON a.user_id = u.id "
            f"WHERE {where} "
            "ORDER BY a.timestamp DESC "
            "LIMIT :limit OFFSET :offset"
        ),
        params,
    )
    return list(result.mappings().all())


# ---------------------------------------------------------------------------
# Active RADIUS sessions (eligible CoA targets)
# ---------------------------------------------------------------------------

async def count_active_sessions(
    db: AsyncSession, *, nas_ip: Optional[str], vlan: Optional[int],
) -> int:
    where, params = _active_session_clause(nas_ip, vlan)
    result = await db.execute(
        text(f"SELECT COUNT(*) FROM radius_sessions rs WHERE {where}"), params,
    )
    return int(result.scalar() or 0)


async def list_active_sessions(
    db: AsyncSession,
    *,
    nas_ip: Optional[str],
    vlan: Optional[int],
    limit: int,
    offset: int,
) -> list[Mapping[str, Any]]:
    where, params = _active_session_clause(nas_ip, vlan)
    params["limit"] = limit
    params["offset"] = offset
    result = await db.execute(
        text(
            f"""
            SELECT rs.*,
                   d.hostname AS device_hostname,
                   d.device_type,
                   d.os_family,
                   nd.hostname AS switch_hostname,
                   nd.vendor AS switch_vendor
            FROM radius_sessions rs
            LEFT JOIN devices d ON rs.device_id = d.id
            LEFT JOIN network_devices nd ON rs.nas_ip::text = nd.ip_address::text
            WHERE {where}
            ORDER BY rs.started_at DESC
            LIMIT :limit OFFSET :offset
            """
        ),
        params,
    )
    return list(result.mappings().all())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _coa_history_clause(
    tenant_id: Optional[str], action: Optional[str],
) -> tuple[str, dict]:
    conditions = ["a.resource_type = 'coa'", _AUDIT_TENANT_CLAUSE]
    params: dict = {"tenant_id": tenant_id}
    if action:
        conditions.append("a.action = :action")
        params["action"] = f"coa_{action}"
    return " AND ".join(conditions), params


def _active_session_clause(
    nas_ip: Optional[str], vlan: Optional[int],
) -> tuple[str, dict]:
    conditions = ["rs.status = 'active'"]
    params: dict = {}
    if nas_ip:
        # Use CAST(:name AS type) form, not the trailing :: typecast —
        # asyncpg's named-param preprocessor mis-parses the latter.
        # See tests/unit/test_no_inline_inet_cast.py.
        conditions.append("rs.nas_ip = CAST(:nas_ip AS inet)")
        params["nas_ip"] = nas_ip
    if vlan:
        conditions.append("rs.assigned_vlan = :vlan")
        params["vlan"] = vlan
    return " AND ".join(conditions), params
