"""Database atoms for the radius_realms feature.

The DB column is `proxy_secret_encrypted` but the request field is
`proxy_secret` — column-mapping lives here so the route layer doesn't
carry SQL detail. Reads JOIN ldap_servers to surface ldap_server_name.
"""
from typing import Any, Mapping, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from utils.safe_sql import build_safe_set_clause, REALM_UPDATE_COLUMNS


# Columns safe for API responses (excludes proxy_secret_encrypted).
_PUBLIC_COLS = (
    "r.id, r.name, r.description, r.realm_type, r.strip_username, "
    "r.proxy_host, r.proxy_port, r.proxy_nostrip, "
    "r.proxy_retry_count, r.proxy_retry_delay_seconds, r.proxy_dead_time_seconds, "
    "r.ldap_server_id, r.auth_types_allowed, "
    "r.default_vlan, r.default_filter_id, r.fallback_realm_id, "
    "r.priority, r.enabled, r.tenant_id"
)
_PUBLIC_COLS_PLAIN = _PUBLIC_COLS.replace("r.", "")


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

async def count_realms(
    db: AsyncSession,
    *,
    tenant_id: str,
    realm_type: Optional[str] = None,
    enabled: Optional[bool] = None,
) -> int:
    where, params = _filter_clause(tenant_id, realm_type, enabled)
    result = await db.execute(
        text(f"SELECT COUNT(*) FROM radius_realms r WHERE {where}"), params,
    )
    return int(result.scalar() or 0)


async def list_realms(
    db: AsyncSession,
    *,
    tenant_id: str,
    realm_type: Optional[str] = None,
    enabled: Optional[bool] = None,
    limit: int,
    offset: int,
) -> list[Mapping[str, Any]]:
    where, params = _filter_clause(tenant_id, realm_type, enabled)
    params["limit"] = limit
    params["offset"] = offset
    result = await db.execute(
        text(
            f"SELECT {_PUBLIC_COLS}, ls.name as ldap_server_name "
            f"FROM radius_realms r "
            f"LEFT JOIN ldap_servers ls ON r.ldap_server_id = ls.id "
            f"WHERE {where} "
            f"ORDER BY r.priority ASC, r.name ASC "
            f"LIMIT :limit OFFSET :offset"
        ),
        params,
    )
    return list(result.mappings().all())


async def lookup_realm(
    db: AsyncSession, *, tenant_id: str, realm_id: UUID,
) -> Optional[Mapping[str, Any]]:
    result = await db.execute(
        text(
            f"SELECT {_PUBLIC_COLS}, ls.name as ldap_server_name "
            f"FROM radius_realms r "
            f"LEFT JOIN ldap_servers ls ON r.ldap_server_id = ls.id "
            f"WHERE r.id = :id AND r.tenant_id = :tenant_id"
        ),
        {"id": str(realm_id), "tenant_id": tenant_id},
    )
    return result.mappings().first()


async def lookup_proxy_state(
    db: AsyncSession, *, tenant_id: str, realm_id: UUID,
) -> Optional[Mapping[str, Any]]:
    """Used by update validation to pre-check existing proxy_host + secret."""
    result = await db.execute(
        text(
            "SELECT proxy_host, proxy_secret_encrypted FROM radius_realms "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(realm_id), "tenant_id": tenant_id},
    )
    return result.mappings().first()


async def lookup_realm_summary(
    db: AsyncSession, *, tenant_id: str, realm_id: UUID,
) -> Optional[Mapping[str, Any]]:
    """Light-touch lookup for delete audit context."""
    result = await db.execute(
        text(
            "SELECT id, name, realm_type FROM radius_realms "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(realm_id), "tenant_id": tenant_id},
    )
    return result.mappings().first()


async def ldap_server_exists(
    db: AsyncSession, *, tenant_id: str, ldap_server_id: str,
) -> bool:
    result = await db.execute(
        text(
            "SELECT 1 FROM ldap_servers "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": ldap_server_id, "tenant_id": tenant_id},
    )
    return result.first() is not None


async def realm_exists(
    db: AsyncSession, *, tenant_id: str, realm_id: str,
) -> bool:
    result = await db.execute(
        text(
            "SELECT 1 FROM radius_realms "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": realm_id, "tenant_id": tenant_id},
    )
    return result.first() is not None


async def count_fallback_references(
    db: AsyncSession, *, tenant_id: str, realm_id: UUID,
) -> int:
    """Used before delete — refuses if other realms point here as fallback."""
    result = await db.execute(
        text(
            "SELECT COUNT(*) FROM radius_realms "
            "WHERE fallback_realm_id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(realm_id), "tenant_id": tenant_id},
    )
    return int(result.scalar() or 0)


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

async def insert_realm(
    db: AsyncSession, *, tenant_id: str, fields: dict,
) -> Mapping[str, Any]:
    payload = dict(fields)
    payload["proxy_secret_encrypted"] = payload.pop("proxy_secret", None)
    payload["tenant_id"] = tenant_id
    result = await db.execute(
        text(
            "INSERT INTO radius_realms "
            "(name, description, realm_type, strip_username, "
            "proxy_host, proxy_port, proxy_secret_encrypted, proxy_nostrip, "
            "proxy_retry_count, proxy_retry_delay_seconds, proxy_dead_time_seconds, "
            "ldap_server_id, auth_types_allowed, "
            "default_vlan, default_filter_id, fallback_realm_id, "
            "priority, enabled, tenant_id) "
            "VALUES (:name, :description, :realm_type, :strip_username, "
            ":proxy_host, :proxy_port, :proxy_secret_encrypted, :proxy_nostrip, "
            ":proxy_retry_count, :proxy_retry_delay_seconds, :proxy_dead_time_seconds, "
            ":ldap_server_id, :auth_types_allowed, "
            ":default_vlan, :default_filter_id, :fallback_realm_id, "
            ":priority, :enabled, :tenant_id) "
            f"RETURNING {_PUBLIC_COLS_PLAIN}"
        ),
        payload,
    )
    row = result.mappings().first()
    if row is None:
        raise RuntimeError("INSERT radius_realms RETURNING produced no row")
    return row


async def update_realm(
    db: AsyncSession, *, tenant_id: str, realm_id: UUID, updates: dict,
) -> Optional[Mapping[str, Any]]:
    """Partial update with `proxy_secret` → `proxy_secret_encrypted` mapping."""
    set_clause, params = build_safe_set_clause(
        updates,
        REALM_UPDATE_COLUMNS,
        column_map={"proxy_secret": "proxy_secret_encrypted"},
    )
    params["id"] = str(realm_id)
    params["tenant_id"] = tenant_id
    result = await db.execute(
        text(
            f"UPDATE radius_realms SET {set_clause}, updated_at = NOW() "
            f"WHERE id = :id AND tenant_id = :tenant_id "
            f"RETURNING {_PUBLIC_COLS_PLAIN}"
        ),
        params,
    )
    return result.mappings().first()


async def delete_realm(
    db: AsyncSession, *, tenant_id: str, realm_id: UUID,
) -> None:
    await db.execute(
        text(
            "DELETE FROM radius_realms "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(realm_id), "tenant_id": tenant_id},
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _filter_clause(
    tenant_id: str, realm_type: Optional[str], enabled: Optional[bool],
) -> tuple[str, dict]:
    conditions = ["r.tenant_id = :tenant_id"]
    params: dict = {"tenant_id": tenant_id}
    if realm_type:
        conditions.append("r.realm_type = :realm_type")
        params["realm_type"] = realm_type
    if enabled is not None:
        conditions.append("r.enabled = :enabled")
        params["enabled"] = enabled
    return " AND ".join(conditions), params
