"""Database atoms for the certificates feature.

The PEM payloads (`pem_data`, `key_pem_encrypted`, `chain_pem`) live in
the `certificates` table. List queries project the *light* shape (no
PEM bytes); detail/download queries include them.
"""
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# Light projection — used by /list. Excludes the heavy PEM columns.
_LIST_COLS = (
    "id, cert_type, name, description, common_name, issuer, serial_number, "
    "not_before, not_after, fingerprint_sha256, key_algorithm, key_size, "
    "subject_alt_names, is_active, is_self_signed, imported, enabled, "
    "created_by, created_at, updated_at"
)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

async def count_certs(
    db: AsyncSession,
    *,
    tenant_id: str,
    cert_type: Optional[str] = None,
    enabled: Optional[bool] = None,
) -> int:
    where, params = _scope_where(
        tenant_id=tenant_id, cert_type=cert_type, enabled=enabled,
    )
    result = await db.execute(
        text(f"SELECT COUNT(*) FROM certificates WHERE {where}"), params,
    )
    return int(result.scalar() or 0)


async def list_certs(
    db: AsyncSession,
    *,
    tenant_id: str,
    cert_type: Optional[str],
    enabled: Optional[bool],
    limit: int,
    offset: int,
) -> list[Mapping[str, Any]]:
    where, params = _scope_where(
        tenant_id=tenant_id, cert_type=cert_type, enabled=enabled,
    )
    params["limit"] = limit
    params["offset"] = offset
    result = await db.execute(
        text(
            f"SELECT {_LIST_COLS} FROM certificates WHERE {where} "
            f"ORDER BY cert_type, name LIMIT :limit OFFSET :offset"
        ),
        params,
    )
    return list(result.mappings().all())


async def lookup_cert_full(
    db: AsyncSession, *, tenant_id: str, cert_id: UUID,
) -> Optional[Mapping[str, Any]]:
    """Full row including PEM payloads — used by /detail."""
    result = await db.execute(
        text(
            "SELECT * FROM certificates "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(cert_id), "tenant_id": tenant_id},
    )
    return result.mappings().first()


async def lookup_cert_summary(
    db: AsyncSession, *, tenant_id: str, cert_id: UUID,
) -> Optional[Mapping[str, Any]]:
    """Light lookup used by activate/delete — id/name/cert_type/is_active."""
    result = await db.execute(
        text(
            "SELECT id, cert_type, name, is_active FROM certificates "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(cert_id), "tenant_id": tenant_id},
    )
    return result.mappings().first()


async def lookup_active_ca(
    db: AsyncSession, *, tenant_id: str,
) -> Optional[Mapping[str, Any]]:
    """Returns pem_data + key_pem_encrypted for the active CA, or None."""
    result = await db.execute(
        text(
            "SELECT pem_data, key_pem_encrypted FROM certificates "
            "WHERE cert_type = 'ca' AND is_active = true "
            "AND tenant_id = :tenant_id LIMIT 1"
        ),
        {"tenant_id": tenant_id},
    )
    return result.mappings().first()


async def lookup_cert_for_download(
    db: AsyncSession, *, tenant_id: str, cert_id: UUID,
) -> Optional[Mapping[str, Any]]:
    result = await db.execute(
        text(
            "SELECT name, cert_type, pem_data, key_pem_encrypted, chain_pem "
            "FROM certificates "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(cert_id), "tenant_id": tenant_id},
    )
    return result.mappings().first()


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

async def insert_cert(
    db: AsyncSession,
    *,
    tenant_id: str,
    created_by: str,
    cert_type: str,
    name: str,
    common_name: Optional[str],
    issuer: Optional[str],
    serial_number: str,
    not_before: datetime,
    not_after: datetime,
    fingerprint_sha256: str,
    key_algorithm: Optional[str],
    key_size: Optional[int],
    pem_data: str,
    key_pem_encrypted: Optional[str],
    chain_pem: Optional[str] = None,
    subject_alt_names: Optional[Sequence[str]] = None,
    is_self_signed: bool = False,
    imported: bool = False,
) -> Mapping[str, Any]:
    """Single INSERT atom for CA / server / imported certs.

    Newly created certs always start ``is_active=false`` and ``enabled=true``;
    activation is a separate use case that performs the type-scoped flip.
    """
    result = await db.execute(
        text(
            "INSERT INTO certificates "
            "(cert_type, name, common_name, issuer, serial_number, "
            "not_before, not_after, fingerprint_sha256, key_algorithm, key_size, "
            "subject_alt_names, pem_data, key_pem_encrypted, chain_pem, "
            "is_active, is_self_signed, imported, enabled, "
            "tenant_id, created_by) "
            "VALUES (:cert_type, :name, :common_name, :issuer, :serial_number, "
            ":not_before, :not_after, :fingerprint_sha256, :key_algorithm, :key_size, "
            ":subject_alt_names, :pem_data, :key_pem_encrypted, :chain_pem, "
            "false, :is_self_signed, :imported, true, "
            ":tenant_id, :created_by) "
            "RETURNING *"
        ),
        {
            "cert_type": cert_type,
            "name": name,
            "common_name": common_name,
            "issuer": issuer,
            "serial_number": serial_number,
            "not_before": not_before,
            "not_after": not_after,
            "fingerprint_sha256": fingerprint_sha256,
            "key_algorithm": key_algorithm,
            "key_size": key_size,
            "subject_alt_names": list(subject_alt_names) if subject_alt_names else None,
            "pem_data": pem_data,
            "key_pem_encrypted": key_pem_encrypted,
            "chain_pem": chain_pem,
            "is_self_signed": is_self_signed,
            "imported": imported,
            "tenant_id": tenant_id,
            "created_by": created_by,
        },
    )
    row = result.mappings().first()
    if row is None:
        raise RuntimeError("INSERT certificates RETURNING produced no row")
    return row


async def deactivate_certs_of_type(
    db: AsyncSession, *, tenant_id: str, cert_type: str,
) -> None:
    await db.execute(
        text(
            "UPDATE certificates SET is_active = false, updated_at = NOW() "
            "WHERE cert_type = :cert_type AND tenant_id = :tenant_id"
        ),
        {"cert_type": cert_type, "tenant_id": tenant_id},
    )


async def set_cert_active(
    db: AsyncSession, *, tenant_id: str, cert_id: UUID,
) -> Optional[Mapping[str, Any]]:
    result = await db.execute(
        text(
            "UPDATE certificates SET is_active = true, updated_at = NOW() "
            "WHERE id = :id AND tenant_id = :tenant_id RETURNING *"
        ),
        {"id": str(cert_id), "tenant_id": tenant_id},
    )
    return result.mappings().first()


async def delete_cert(
    db: AsyncSession, *, tenant_id: str, cert_id: UUID,
) -> None:
    await db.execute(
        text(
            "DELETE FROM certificates "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(cert_id), "tenant_id": tenant_id},
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _scope_where(
    *,
    tenant_id: str,
    cert_type: Optional[str],
    enabled: Optional[bool],
) -> tuple[str, dict[str, Any]]:
    conditions = ["tenant_id = :tenant_id"]
    params: dict[str, Any] = {"tenant_id": tenant_id}
    if cert_type:
        conditions.append("cert_type = :cert_type")
        params["cert_type"] = cert_type
    if enabled is not None:
        conditions.append("enabled = :enabled")
        params["enabled"] = enabled
    return " AND ".join(conditions), params
