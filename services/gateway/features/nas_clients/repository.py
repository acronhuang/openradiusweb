"""Database atoms for the nas_clients feature.

The DB column is `secret_encrypted` but the request field is
`shared_secret` — the column-mapping lives here so the route layer
doesn't carry SQL detail. The `ip_address` column is VARCHAR(50) (per
migrations/002) and accepts CIDR strings like "10.0.0.0/24" directly,
so no INET cast is needed.
"""
from typing import Any, Mapping, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.secrets import encrypt_secret
from utils.safe_sql import build_safe_set_clause, NAS_CLIENT_UPDATE_COLUMNS


# Columns safe to return to the API (excludes secret_encrypted).
_PUBLIC_COLS = (
    "id, name, ip_address, shortname, nas_type, "
    "virtual_server, enabled, description, tenant_id"
)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

async def list_nas_clients(
    db: AsyncSession, *, tenant_id: str,
) -> list[Mapping[str, Any]]:
    result = await db.execute(
        text(
            f"SELECT {_PUBLIC_COLS} FROM radius_nas_clients "
            f"WHERE tenant_id = :tenant_id ORDER BY name"
        ),
        {"tenant_id": tenant_id},
    )
    return list(result.mappings().all())


async def lookup_nas_client(
    db: AsyncSession, *, tenant_id: str, nas_id: UUID,
) -> Optional[Mapping[str, Any]]:
    result = await db.execute(
        text(
            f"SELECT {_PUBLIC_COLS} FROM radius_nas_clients "
            f"WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(nas_id), "tenant_id": tenant_id},
    )
    return result.mappings().first()


async def lookup_nas_client_summary(
    db: AsyncSession, *, tenant_id: str, nas_id: UUID,
) -> Optional[Mapping[str, Any]]:
    """Light-touch lookup used by update/delete to fetch audit-context."""
    result = await db.execute(
        text(
            "SELECT id, name FROM radius_nas_clients "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(nas_id), "tenant_id": tenant_id},
    )
    return result.mappings().first()


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

async def insert_nas_client(
    db: AsyncSession,
    *,
    tenant_id: str,
    name: str,
    ip_address: str,
    shared_secret: str,
    shortname: Optional[str],
    nas_type: str,
    description: Optional[str],
) -> Mapping[str, Any]:
    result = await db.execute(
        text(
            "INSERT INTO radius_nas_clients "
            "(name, ip_address, secret_encrypted, shortname, "
            "nas_type, description, tenant_id) "
            "VALUES (:name, :ip_address, :shared_secret, "
            ":shortname, :nas_type, :description, :tenant_id) "
            f"RETURNING {_PUBLIC_COLS}"
        ),
        {
            "name": name,
            "ip_address": ip_address,
            # `shared_secret` (request field) → `secret_encrypted` column,
            # AES-256-GCM via orw_common.secrets. The plaintext never lands
            # on disk — gateway encrypts at this boundary, freeradius
            # decrypts when generating clients.conf.
            "shared_secret": encrypt_secret(shared_secret),
            "shortname": shortname or name[:31],
            "nas_type": nas_type,
            "description": description,
            "tenant_id": tenant_id,
        },
    )
    row = result.mappings().first()
    if row is None:
        raise RuntimeError("INSERT radius_nas_clients RETURNING produced no row")
    return row


async def update_nas_client(
    db: AsyncSession, *, tenant_id: str, nas_id: UUID, updates: dict,
) -> Optional[Mapping[str, Any]]:
    """Partial update with one SQL detail encapsulated:

    - `shared_secret` (request field, plaintext) is encrypted via
      orw_common.secrets and mapped to `secret_encrypted` (DB column)

    `ip_address` is VARCHAR(50) so no INET cast is needed.

    Returns the updated row, or None if (id, tenant) didn't match.
    Raises ValueError if `updates` contains no allowed columns.
    """
    if updates.get("shared_secret") is not None:
        # Copy so we don't mutate the caller's dict.
        updates = dict(updates)
        updates["shared_secret"] = encrypt_secret(updates["shared_secret"])
    set_clause, params = build_safe_set_clause(
        updates,
        NAS_CLIENT_UPDATE_COLUMNS,
        column_map={"shared_secret": "secret_encrypted"},
    )
    params["id"] = str(nas_id)
    params["tenant_id"] = tenant_id
    result = await db.execute(
        text(
            f"UPDATE radius_nas_clients SET {set_clause} "
            f"WHERE id = :id AND tenant_id = :tenant_id "
            f"RETURNING {_PUBLIC_COLS}"
        ),
        params,
    )
    return result.mappings().first()


async def delete_nas_client(
    db: AsyncSession, *, tenant_id: str, nas_id: UUID,
) -> None:
    await db.execute(
        text(
            "DELETE FROM radius_nas_clients "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(nas_id), "tenant_id": tenant_id},
    )
