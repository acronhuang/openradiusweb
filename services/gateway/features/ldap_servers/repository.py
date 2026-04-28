"""Database atoms for the ldap_servers feature.

The DB column is `bind_password_encrypted` but the request field is
`bind_password` — the column-mapping lives here so the route layer
doesn't carry SQL detail. `lookup_full_for_test` is the only atom that
returns the password, used by the route-layer LDAP connection test.
"""
from typing import Any, Mapping, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from utils.safe_sql import build_safe_set_clause, LDAP_SERVER_UPDATE_COLUMNS


# Columns safe to return to API callers (excludes bind_password_encrypted).
_PUBLIC_COLS = (
    "id, name, description, host, port, use_tls, use_starttls, "
    "bind_dn, base_dn, user_search_filter, user_search_base, "
    "group_search_filter, group_search_base, group_membership_attr, "
    "username_attr, display_name_attr, email_attr, "
    "connect_timeout_seconds, search_timeout_seconds, idle_timeout_seconds, "
    "tls_ca_cert, tls_require_cert, priority, enabled, "
    "last_test_at, last_test_result, last_test_message, tenant_id"
)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

async def count_ldap_servers(
    db: AsyncSession, *, tenant_id: str, enabled: Optional[bool] = None,
) -> int:
    where, params = _filter_clause(tenant_id, enabled)
    result = await db.execute(
        text(f"SELECT COUNT(*) FROM ldap_servers WHERE {where}"), params,
    )
    return int(result.scalar() or 0)


async def list_ldap_servers(
    db: AsyncSession,
    *,
    tenant_id: str,
    enabled: Optional[bool] = None,
    limit: int,
    offset: int,
) -> list[Mapping[str, Any]]:
    where, params = _filter_clause(tenant_id, enabled)
    params["limit"] = limit
    params["offset"] = offset
    result = await db.execute(
        text(
            f"SELECT {_PUBLIC_COLS} FROM ldap_servers WHERE {where} "
            f"ORDER BY priority ASC, name ASC "
            f"LIMIT :limit OFFSET :offset"
        ),
        params,
    )
    return list(result.mappings().all())


async def lookup_ldap_server(
    db: AsyncSession, *, tenant_id: str, server_id: UUID,
) -> Optional[Mapping[str, Any]]:
    result = await db.execute(
        text(
            f"SELECT {_PUBLIC_COLS} FROM ldap_servers "
            f"WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(server_id), "tenant_id": tenant_id},
    )
    return result.mappings().first()


async def lookup_ldap_server_summary(
    db: AsyncSession, *, tenant_id: str, server_id: UUID,
) -> Optional[Mapping[str, Any]]:
    """Light-touch lookup for delete audit context."""
    result = await db.execute(
        text(
            "SELECT id, name FROM ldap_servers "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(server_id), "tenant_id": tenant_id},
    )
    return result.mappings().first()


async def lookup_full_for_test(
    db: AsyncSession, *, tenant_id: str, server_id: UUID,
) -> Optional[Mapping[str, Any]]:
    """Returns ALL columns including bind_password_encrypted.

    Only used by the live LDAP connection test; never exposed via API.
    """
    result = await db.execute(
        text(
            "SELECT * FROM ldap_servers "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(server_id), "tenant_id": tenant_id},
    )
    return result.mappings().first()


async def count_realm_references(
    db: AsyncSession, *, server_id: UUID,
) -> int:
    """Used before delete to enforce the `radius_realms.ldap_server_id` foreign key."""
    result = await db.execute(
        text(
            "SELECT COUNT(*) FROM radius_realms WHERE ldap_server_id = :id"
        ),
        {"id": str(server_id)},
    )
    return int(result.scalar() or 0)


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

async def insert_ldap_server(
    db: AsyncSession, *, tenant_id: str, fields: dict,
) -> Mapping[str, Any]:
    """Insert all 25 LDAP columns. `fields` must come from the create schema
    plus the tenant. `bind_password` is mapped to `bind_password_encrypted`."""
    payload = dict(fields)
    payload["bind_password_encrypted"] = payload.pop("bind_password", None)
    payload["tenant_id"] = tenant_id
    result = await db.execute(
        text(
            "INSERT INTO ldap_servers "
            "(name, description, host, port, use_tls, use_starttls, "
            "bind_dn, bind_password_encrypted, base_dn, "
            "user_search_filter, user_search_base, "
            "group_search_filter, group_search_base, group_membership_attr, "
            "username_attr, display_name_attr, email_attr, "
            "connect_timeout_seconds, search_timeout_seconds, idle_timeout_seconds, "
            "tls_ca_cert, tls_require_cert, priority, enabled, tenant_id) "
            "VALUES (:name, :description, :host, :port, :use_tls, :use_starttls, "
            ":bind_dn, :bind_password_encrypted, :base_dn, "
            ":user_search_filter, :user_search_base, "
            ":group_search_filter, :group_search_base, :group_membership_attr, "
            ":username_attr, :display_name_attr, :email_attr, "
            ":connect_timeout_seconds, :search_timeout_seconds, :idle_timeout_seconds, "
            ":tls_ca_cert, :tls_require_cert, :priority, :enabled, :tenant_id) "
            f"RETURNING {_PUBLIC_COLS}"
        ),
        payload,
    )
    row = result.mappings().first()
    if row is None:
        raise RuntimeError("INSERT ldap_servers RETURNING produced no row")
    return row


async def update_ldap_server(
    db: AsyncSession, *, tenant_id: str, server_id: UUID, updates: dict,
) -> Optional[Mapping[str, Any]]:
    """Partial update with `bind_password` → `bind_password_encrypted` mapping."""
    set_clause, params = build_safe_set_clause(
        updates,
        LDAP_SERVER_UPDATE_COLUMNS,
        column_map={"bind_password": "bind_password_encrypted"},
    )
    params["id"] = str(server_id)
    params["tenant_id"] = tenant_id
    result = await db.execute(
        text(
            f"UPDATE ldap_servers SET {set_clause}, updated_at = NOW() "
            f"WHERE id = :id AND tenant_id = :tenant_id "
            f"RETURNING {_PUBLIC_COLS}"
        ),
        params,
    )
    return result.mappings().first()


async def delete_ldap_server(
    db: AsyncSession, *, tenant_id: str, server_id: UUID,
) -> None:
    await db.execute(
        text(
            "DELETE FROM ldap_servers "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(server_id), "tenant_id": tenant_id},
    )


async def update_test_result(
    db: AsyncSession,
    *,
    server_id: UUID,
    success: bool,
    message: str,
) -> None:
    """Record the outcome of a live LDAP connection test."""
    await db.execute(
        text(
            "UPDATE ldap_servers SET "
            "last_test_at = NOW(), "
            "last_test_result = :result, "
            "last_test_message = :message "
            "WHERE id = :id"
        ),
        {
            "id": str(server_id),
            "result": "success" if success else "failure",
            "message": message[:1000],
        },
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _filter_clause(
    tenant_id: str, enabled: Optional[bool],
) -> tuple[str, dict]:
    conditions = ["tenant_id = :tenant_id"]
    params: dict = {"tenant_id": tenant_id}
    if enabled is not None:
        conditions.append("enabled = :enabled")
        params["enabled"] = enabled
    return " AND ".join(conditions), params
