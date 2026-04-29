"""Use-case composition for the certificates feature (Layer 2).

Use cases:
  - list / get_detail / download
  - generate_ca / generate_server / import_cert
  - activate_cert (also publishes orw.config.freeradius.apply)
  - delete_cert (refuses if active)

Crypto helpers live in ``crypto.py`` so they can be unit-tested without
DB or HTTP fixtures. The service composes crypto + repo atoms + audit
+ NATS into use cases.

Domain exceptions raised:
  - NotFoundError when a cert_id is unknown
  - ValidationError on bad PEM, missing CA, or deleting an active cert
"""
from typing import Any, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.exceptions import NotFoundError, ValidationError
from orw_common.models.certificate import (
    GenerateCARequest,
    GenerateServerRequest,
    ImportCertRequest,
)
from utils.audit import log_audit

from . import crypto
from . import events
from . import repository as repo


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

async def list_certs(
    db: AsyncSession,
    *,
    tenant_id: str,
    cert_type: Optional[str],
    enabled: Optional[bool],
    page: int,
    page_size: int,
) -> dict[str, Any]:
    total = await repo.count_certs(
        db, tenant_id=tenant_id, cert_type=cert_type, enabled=enabled,
    )
    rows = await repo.list_certs(
        db, tenant_id=tenant_id,
        cert_type=cert_type, enabled=enabled,
        limit=page_size, offset=(page - 1) * page_size,
    )
    return {
        "items": [_decorate_with_status(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def get_cert_detail(
    db: AsyncSession, *, tenant_id: str, cert_id: UUID,
) -> dict:
    row = await repo.lookup_cert_full(
        db, tenant_id=tenant_id, cert_id=cert_id,
    )
    if not row:
        raise NotFoundError("Certificate", str(cert_id))
    return _decorate_with_status(row)


async def download_cert(
    db: AsyncSession,
    *,
    tenant_id: str,
    cert_id: UUID,
    include_key: bool,
    include_chain: bool,
) -> dict:
    """Returns assembled PEM + filename. Routes layer wraps in Response."""
    row = await repo.lookup_cert_for_download(
        db, tenant_id=tenant_id, cert_id=cert_id,
    )
    if not row:
        raise NotFoundError("Certificate", str(cert_id))
    if not row["pem_data"]:
        raise NotFoundError("Certificate PEM data", str(cert_id))

    pem_output = row["pem_data"]
    if include_chain and row["chain_pem"]:
        pem_output += "\n" + row["chain_pem"]
    if include_key and row["key_pem_encrypted"]:
        pem_output += "\n" + row["key_pem_encrypted"]

    return {
        "pem": pem_output,
        "filename": crypto.safe_filename(row["name"]),
    }


# ---------------------------------------------------------------------------
# Generate / import
# ---------------------------------------------------------------------------

async def generate_ca(
    db: AsyncSession,
    actor: dict,
    *,
    req: GenerateCARequest,
    client_ip: Optional[str] = None,
) -> dict:
    cert_pem, key_pem = crypto.generate_ca_keypair(
        common_name=req.common_name,
        organization=req.organization,
        country=req.country,
        validity_days=req.validity_days,
        key_size=req.key_size,
    )
    meta = crypto.parse_cert_metadata(cert_pem)
    crypto.write_cert_files(
        "ca", name=req.name, cert_pem=cert_pem, key_pem=key_pem,
    )
    row = await _insert(
        db, actor,
        cert_type="ca", name=req.name, cert_pem=cert_pem, key_pem=key_pem,
        meta=meta, is_self_signed=True, imported=False,
    )
    await log_audit(
        db, actor,
        action="create", resource_type="certificate",
        resource_id=str(row["id"]),
        details={
            "cert_type": "ca", "name": req.name,
            "common_name": req.common_name,
        },
        ip_address=client_ip,
    )
    return _decorate_with_status(row)


async def generate_server(
    db: AsyncSession,
    actor: dict,
    *,
    req: GenerateServerRequest,
    client_ip: Optional[str] = None,
) -> dict:
    ca_row = await repo.lookup_active_ca(db, tenant_id=actor["tenant_id"])
    if not ca_row:
        raise ValidationError(
            "No active CA certificate found. "
            "Generate or activate a CA first.",
        )

    cert_pem, key_pem = crypto.generate_server_keypair(
        common_name=req.common_name,
        san_dns=req.san_dns,
        san_ips=req.san_ips,
        validity_days=req.validity_days,
        key_size=req.key_size,
        ca_cert_pem=ca_row["pem_data"],
        ca_key_pem=ca_row["key_pem_encrypted"],
    )
    meta = crypto.parse_cert_metadata(cert_pem)
    crypto.write_cert_files(
        "server", name=req.name, cert_pem=cert_pem, key_pem=key_pem,
    )
    san_list = list(req.san_dns) + list(req.san_ips)
    row = await _insert(
        db, actor,
        cert_type="server", name=req.name,
        cert_pem=cert_pem, key_pem=key_pem,
        meta=meta, subject_alt_names=san_list or None,
        is_self_signed=False, imported=False,
    )
    await log_audit(
        db, actor,
        action="create", resource_type="certificate",
        resource_id=str(row["id"]),
        details={
            "cert_type": "server", "name": req.name,
            "common_name": req.common_name,
        },
        ip_address=client_ip,
    )
    return _decorate_with_status(row)


async def import_cert(
    db: AsyncSession,
    actor: dict,
    *,
    req: ImportCertRequest,
    client_ip: Optional[str] = None,
) -> dict:
    meta = crypto.parse_cert_metadata(req.cert_pem)
    if req.key_pem:
        crypto.validate_private_key_pem(req.key_pem)

    row = await _insert(
        db, actor,
        cert_type=req.cert_type, name=req.name,
        cert_pem=req.cert_pem, key_pem=req.key_pem,
        meta=meta, chain_pem=req.chain_pem,
        is_self_signed=meta["is_self_signed"], imported=True,
    )
    await log_audit(
        db, actor,
        action="create", resource_type="certificate",
        resource_id=str(row["id"]),
        details={
            "cert_type": req.cert_type, "name": req.name,
            "imported": True, "common_name": meta["common_name"],
        },
        ip_address=client_ip,
    )
    return _decorate_with_status(row)


# ---------------------------------------------------------------------------
# Activate / delete
# ---------------------------------------------------------------------------

async def activate_cert(
    db: AsyncSession,
    actor: dict,
    *,
    cert_id: UUID,
    client_ip: Optional[str] = None,
) -> dict:
    summary = await repo.lookup_cert_summary(
        db, tenant_id=actor["tenant_id"], cert_id=cert_id,
    )
    if not summary:
        raise NotFoundError("Certificate", str(cert_id))

    await repo.deactivate_certs_of_type(
        db, tenant_id=actor["tenant_id"], cert_type=summary["cert_type"],
    )
    row = await repo.set_cert_active(
        db, tenant_id=actor["tenant_id"], cert_id=cert_id,
    )
    if not row:
        # Vanishingly rare race — row was deleted between summary and update
        raise NotFoundError("Certificate", str(cert_id))

    await log_audit(
        db, actor,
        action="update", resource_type="certificate",
        resource_id=str(cert_id),
        details={
            "action": "activate",
            "cert_type": summary["cert_type"],
            "name": summary["name"],
        },
        ip_address=client_ip,
    )
    await events.publish_freeradius_apply_for_cert(
        cert_id=str(cert_id), cert_type=summary["cert_type"],
    )
    return _decorate_with_status(row)


async def delete_cert(
    db: AsyncSession,
    actor: dict,
    *,
    cert_id: UUID,
    client_ip: Optional[str] = None,
) -> None:
    summary = await repo.lookup_cert_summary(
        db, tenant_id=actor["tenant_id"], cert_id=cert_id,
    )
    if not summary:
        raise NotFoundError("Certificate", str(cert_id))
    if summary["is_active"]:
        raise ValidationError(
            "Cannot delete an active certificate. "
            "Deactivate it first by activating another certificate.",
        )
    await repo.delete_cert(
        db, tenant_id=actor["tenant_id"], cert_id=cert_id,
    )
    await log_audit(
        db, actor,
        action="delete", resource_type="certificate",
        resource_id=str(cert_id),
        details={
            "name": summary["name"],
            "cert_type": summary["cert_type"],
        },
        ip_address=client_ip,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decorate_with_status(row) -> dict:
    item = dict(row)
    item.update(crypto.compute_cert_status(row.get("not_after")))
    return item


async def _insert(
    db: AsyncSession,
    actor: dict,
    *,
    cert_type: str,
    name: str,
    cert_pem: str,
    key_pem: Optional[str],
    meta: dict,
    chain_pem: Optional[str] = None,
    subject_alt_names: Optional[list] = None,
    is_self_signed: bool = False,
    imported: bool = False,
):
    return await repo.insert_cert(
        db,
        tenant_id=actor["tenant_id"],
        created_by=actor["sub"],
        cert_type=cert_type,
        name=name,
        common_name=meta["common_name"],
        issuer=meta["issuer"],
        serial_number=meta["serial_number"],
        not_before=meta["not_before"],
        not_after=meta["not_after"],
        fingerprint_sha256=meta["fingerprint_sha256"],
        key_algorithm=meta["key_algorithm"],
        key_size=meta["key_size"],
        pem_data=cert_pem,
        key_pem_encrypted=key_pem,
        chain_pem=chain_pem,
        subject_alt_names=subject_alt_names,
        is_self_signed=is_self_signed,
        imported=imported,
    )
