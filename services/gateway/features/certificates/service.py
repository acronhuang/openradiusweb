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
import ipaddress
from datetime import datetime, timezone
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


# ---------------------------------------------------------------------------
# Auto-renewal
# ---------------------------------------------------------------------------

def _split_san_dns_ips(san_list: Optional[list[str]]) -> tuple[list[str], list[str]]:
    """SAN values are stored in one TEXT[] column; split back into the
    DNS / IP buckets that GenerateServerRequest expects. Anything that
    parses as an IPv4/IPv6 address goes to ips, everything else to dns.
    """
    if not san_list:
        return [], []
    dns: list[str] = []
    ips: list[str] = []
    for v in san_list:
        try:
            ipaddress.ip_address(v)
            ips.append(v)
        except ValueError:
            dns.append(v)
    return dns, ips


def _renewal_name(old_name: str, when: datetime) -> str:
    """`<old_name>-renewed-YYYYMMDD`. The schema enforces
    UNIQUE(name, tenant_id) so this disambiguates from the previous row.
    Truncate to fit the 255-char column.
    """
    suffix = f"-renewed-{when.strftime('%Y%m%d')}"
    base = old_name[: 255 - len(suffix)]
    return base + suffix


async def auto_renew_expiring_server_certs(
    db: AsyncSession,
    actor: dict,
    *,
    threshold_days: int,
) -> dict:
    """Background-task entrypoint: renew every active server cert
    expiring within `threshold_days`.

    For each candidate:
      1. Reconstruct GenerateServerRequest from the old cert's metadata
         (preserves CN, SAN, key_size, original validity_days = the
         delta between not_before and not_after).
      2. Generate a new server cert signed by the active CA.
      3. Activate the new cert (which deactivates the old one and
         publishes the freeradius reload event).

    Skips: imported=true rows (no original CSR/key — operator must
    handle those manually).

    Returns a summary dict for the caller to log:
      {"checked": int, "renewed": [name, ...], "errors": [str, ...]}
    """
    candidates = await repo.list_renewable_server_certs_within(
        db, tenant_id=actor["tenant_id"], threshold_days=threshold_days,
    )
    summary: dict = {"checked": len(candidates), "renewed": [], "errors": []}
    if not candidates:
        return summary

    now = datetime.now(timezone.utc)
    for old in candidates:
        try:
            san_dns, san_ips = _split_san_dns_ips(old.get("subject_alt_names"))
            # Original validity_days from the old cert; fall back to 730
            # (GenerateServerRequest default) if not_before is missing
            # for some imported-but-flagged-as-not-imported edge case.
            if old.get("not_before") and old.get("not_after"):
                validity_days = max(
                    1, (old["not_after"] - old["not_before"]).days,
                )
            else:
                validity_days = 730

            req = GenerateServerRequest(
                name=_renewal_name(old["name"], now),
                common_name=old["common_name"] or old["name"],
                san_dns=san_dns,
                san_ips=san_ips,
                validity_days=validity_days,
                key_size=old.get("key_size") or 2048,
            )
            new_row = await generate_server(db, actor, req=req)
            await activate_cert(db, actor, cert_id=UUID(str(new_row["id"])))
            summary["renewed"].append(req.name)
        except Exception as exc:
            summary["errors"].append(
                f"{old.get('name', old['id'])}: {type(exc).__name__}: {exc}"
            )
    return summary


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
