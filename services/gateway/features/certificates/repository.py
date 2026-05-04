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

from orw_common.secrets import decrypt_secret, encrypt_secret


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


async def list_renewable_server_certs_within(
    db: AsyncSession, *, tenant_id: str, threshold_days: int,
) -> list[Mapping[str, Any]]:
    """Active server certs that expire within `threshold_days` AND were
    NOT imported (we have no original key/CSR for imported certs, so
    auto-renewal would need a fresh CSR from the operator).

    Used by the auto-renewal background task in gateway/main.py.
    Returns the columns needed to reconstruct a GenerateServerRequest:
    id, name, common_name, subject_alt_names, key_size,
    not_before, not_after.
    """
    result = await db.execute(
        text(
            "SELECT id, name, common_name, subject_alt_names, "
            "       key_size, not_before, not_after "
            "FROM certificates "
            "WHERE cert_type = 'server' "
            "  AND is_active = true "
            "  AND enabled = true "
            "  AND imported = false "
            "  AND tenant_id = :tenant_id "
            "  AND not_after IS NOT NULL "
            "  AND not_after < NOW() + (:days || ' days')::interval "
            "ORDER BY not_after"
        ),
        {"tenant_id": tenant_id, "days": threshold_days},
    )
    return list(result.mappings().all())


async def lookup_active_ca(
    db: AsyncSession, *, tenant_id: str,
) -> Optional[Mapping[str, Any]]:
    """Returns pem_data + decrypted key_pem for the active CA, or None.

    The `key_pem_encrypted` key in the returned mapping holds the
    decrypted PEM (despite the column name) — callers feed it directly
    to cert tooling that expects PEM text.
    """
    result = await db.execute(
        text(
            "SELECT pem_data, key_pem_encrypted FROM certificates "
            "WHERE cert_type = 'ca' AND is_active = true "
            "AND tenant_id = :tenant_id LIMIT 1"
        ),
        {"tenant_id": tenant_id},
    )
    row = result.mappings().first()
    if row is None:
        return None
    out = dict(row)
    out["key_pem_encrypted"] = decrypt_secret(out.get("key_pem_encrypted"))
    return out


async def lookup_cert_for_download(
    db: AsyncSession, *, tenant_id: str, cert_id: UUID,
) -> Optional[Mapping[str, Any]]:
    """Returns the full PEM bundle including decrypted private key.

    `key_pem_encrypted` returned as decrypted PEM text (or None if no
    key on the row, e.g. an imported chain-only cert).
    """
    result = await db.execute(
        text(
            "SELECT name, cert_type, pem_data, key_pem_encrypted, chain_pem "
            "FROM certificates "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(cert_id), "tenant_id": tenant_id},
    )
    row = result.mappings().first()
    if row is None:
        return None
    out = dict(row)
    out["key_pem_encrypted"] = decrypt_secret(out.get("key_pem_encrypted"))
    return out


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
            # Encrypt the private key PEM at this boundary — the param
            # is named `key_pem_encrypted` upstream but receives plaintext
            # from the cert generation / import code path.
            "key_pem_encrypted": encrypt_secret(key_pem_encrypted),
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
