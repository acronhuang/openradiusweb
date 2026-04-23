"""Certificate management routes - CA/server cert generation, import, download."""

import hashlib
import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from orw_common.database import get_db
from orw_common.models.certificate import GenerateCARequest, GenerateServerRequest, ImportCertRequest
from orw_common import nats_client
from middleware.auth import get_current_user, require_admin
from utils.audit import log_audit

router = APIRouter(prefix="/certificates")

CERT_BASE_DIR = "/opt/orw/certs"


# ============================================================
# Helpers
# ============================================================

def _parse_cert_metadata(cert_pem: str) -> dict:
    """Extract metadata from a PEM-encoded certificate."""
    cert = x509.load_pem_x509_certificate(cert_pem.encode())

    # Common name
    cn_attrs = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    common_name = cn_attrs[0].value if cn_attrs else None

    # Issuer common name
    issuer_cn_attrs = cert.issuer.get_attributes_for_oid(NameOID.COMMON_NAME)
    issuer = issuer_cn_attrs[0].value if issuer_cn_attrs else str(cert.issuer)

    # Fingerprint
    fingerprint_sha256 = cert.fingerprint(hashes.SHA256()).hex(":")

    # Key info
    public_key = cert.public_key()
    key_algorithm = type(public_key).__name__.replace("_", "").replace("PublicKey", "")
    key_size = public_key.key_size if hasattr(public_key, "key_size") else None

    # Self-signed check
    is_self_signed = cert.issuer == cert.subject

    return {
        "common_name": common_name,
        "issuer": issuer,
        "serial_number": str(cert.serial_number),
        "not_before": cert.not_valid_before_utc,
        "not_after": cert.not_valid_after_utc,
        "fingerprint_sha256": fingerprint_sha256,
        "key_algorithm": key_algorithm,
        "key_size": key_size,
        "is_self_signed": is_self_signed,
    }


def _compute_cert_status(not_after: datetime) -> dict:
    """Compute days_until_expiry and status for a certificate."""
    now = datetime.now(timezone.utc)
    if not_after.tzinfo is None:
        not_after = not_after.replace(tzinfo=timezone.utc)
    delta = not_after - now
    days = delta.days

    if days < 0:
        status = "expired"
    elif days <= 30:
        status = "expiring_soon"
    else:
        status = "valid"

    return {"days_until_expiry": days, "status": status}


def _write_cert_file(directory: str, filename: str, data: str):
    """Write PEM data to a file, creating directories as needed."""
    os.makedirs(directory, exist_ok=True)
    filepath = os.path.join(directory, filename)
    with open(filepath, "w") as f:
        f.write(data)
    os.chmod(filepath, 0o600)
    return filepath


# ============================================================
# Endpoints
# ============================================================

@router.get("")
async def list_certificates(
    cert_type: str | None = None,
    enabled: bool | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """List certificates. Returns metadata only (no PEM data)."""
    conditions = ["tenant_id = :tenant_id"]
    params: dict = {"tenant_id": user["tenant_id"]}

    if cert_type:
        conditions.append("cert_type = :cert_type")
        params["cert_type"] = cert_type
    if enabled is not None:
        conditions.append("enabled = :enabled")
        params["enabled"] = enabled

    where = " AND ".join(conditions)
    offset = (page - 1) * page_size
    params["limit"] = page_size
    params["offset"] = offset

    count_result = await db.execute(
        text(f"SELECT COUNT(*) FROM certificates WHERE {where}"), params
    )
    total = count_result.scalar()

    result = await db.execute(
        text(
            f"SELECT id, cert_type, name, description, common_name, issuer, serial_number, "
            f"not_before, not_after, fingerprint_sha256, key_algorithm, key_size, "
            f"subject_alt_names, is_active, is_self_signed, imported, enabled, "
            f"created_by, created_at, updated_at "
            f"FROM certificates WHERE {where} "
            f"ORDER BY cert_type, name "
            f"LIMIT :limit OFFSET :offset"
        ),
        params,
    )
    rows = result.mappings().all()

    items = []
    for r in rows:
        item = dict(r)
        if r["not_after"]:
            item.update(_compute_cert_status(r["not_after"]))
        else:
            item["days_until_expiry"] = None
            item["status"] = "unknown"
        items.append(item)

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/{cert_id}")
async def get_certificate(
    cert_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Get full certificate detail including PEM data."""
    result = await db.execute(
        text(
            "SELECT * FROM certificates "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(cert_id), "tenant_id": user["tenant_id"]},
    )
    cert = result.mappings().first()
    if not cert:
        raise HTTPException(status_code=404, detail="Certificate not found")

    item = dict(cert)
    if cert["not_after"]:
        item.update(_compute_cert_status(cert["not_after"]))
    else:
        item["days_until_expiry"] = None
        item["status"] = "unknown"

    return item


@router.post("/generate-ca", status_code=201)
async def generate_ca_cert(
    req: GenerateCARequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Generate a self-signed CA certificate."""
    # Generate RSA private key
    key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=req.key_size,
    )

    # Build subject
    name_attrs = [x509.NameAttribute(NameOID.COMMON_NAME, req.common_name)]
    if req.organization:
        name_attrs.append(x509.NameAttribute(NameOID.ORGANIZATION_NAME, req.organization))
    if req.country:
        name_attrs.append(x509.NameAttribute(NameOID.COUNTRY_NAME, req.country))

    subject = issuer = x509.Name(name_attrs)
    now = datetime.now(timezone.utc)

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=req.validity_days))
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=None),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=True,
                crl_sign=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    # Serialize to PEM
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()

    # Parse metadata
    meta = _parse_cert_metadata(cert_pem)

    # Write files
    ca_dir = os.path.join(CERT_BASE_DIR, "ca")
    safe_name = req.name.replace(" ", "_").lower()
    cert_file = _write_cert_file(ca_dir, f"{safe_name}.pem", cert_pem)
    key_file = _write_cert_file(ca_dir, f"{safe_name}.key", key_pem)

    # Store in DB
    result = await db.execute(
        text(
            "INSERT INTO certificates "
            "(cert_type, name, common_name, issuer, serial_number, "
            "not_before, not_after, fingerprint_sha256, key_algorithm, key_size, "
            "pem_data, key_pem_encrypted, "
            "is_active, is_self_signed, imported, enabled, "
            "tenant_id, created_by) "
            "VALUES ('ca', :name, :common_name, :issuer, :serial_number, "
            ":not_before, :not_after, :fingerprint_sha256, :key_algorithm, :key_size, "
            ":pem_data, :key_pem_encrypted, "
            "false, true, false, true, "
            ":tenant_id, :created_by) "
            "RETURNING *"
        ),
        {
            "name": req.name,
            "common_name": meta["common_name"],
            "issuer": meta["issuer"],
            "serial_number": meta["serial_number"],
            "not_before": meta["not_before"],
            "not_after": meta["not_after"],
            "fingerprint_sha256": meta["fingerprint_sha256"],
            "key_algorithm": meta["key_algorithm"],
            "key_size": meta["key_size"],
            "pem_data": cert_pem,
            "key_pem_encrypted": key_pem,  # TODO: encrypt via Vault
            "tenant_id": user["tenant_id"],
            "created_by": user["sub"],
        },
    )
    row = result.mappings().first()

    await log_audit(
        db, user, "create", "certificate",
        resource_id=str(row["id"]),
        details={"cert_type": "ca", "name": req.name, "common_name": req.common_name},
    )

    item = dict(row)
    item.update(_compute_cert_status(row["not_after"]))
    return item


@router.post("/generate-server", status_code=201)
async def generate_server_cert(
    req: GenerateServerRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Generate a server certificate signed by the active CA."""
    # Load active CA cert + key from DB
    ca_result = await db.execute(
        text(
            "SELECT pem_data, key_pem_encrypted FROM certificates "
            "WHERE cert_type = 'ca' AND is_active = true "
            "AND tenant_id = :tenant_id LIMIT 1"
        ),
        {"tenant_id": user["tenant_id"]},
    )
    ca_row = ca_result.mappings().first()
    if not ca_row:
        raise HTTPException(
            status_code=400,
            detail="No active CA certificate found. Generate or activate a CA first.",
        )

    ca_cert = x509.load_pem_x509_certificate(ca_row["pem_data"].encode())
    ca_key = serialization.load_pem_private_key(
        ca_row["key_pem_encrypted"].encode(), password=None
    )

    # Generate server key
    key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=req.key_size,
    )

    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, req.common_name),
    ])

    now = datetime.now(timezone.utc)

    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=req.validity_days))
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=True,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([
                x509.oid.ExtendedKeyUsageOID.SERVER_AUTH,
            ]),
            critical=False,
        )
    )

    # Subject Alternative Names
    san_entries = []
    for dns_name in req.san_dns:
        san_entries.append(x509.DNSName(dns_name))
    for ip_str in req.san_ips:
        import ipaddress
        san_entries.append(x509.IPAddress(ipaddress.ip_address(ip_str)))
    # Always include the CN as a SAN
    san_entries.append(x509.DNSName(req.common_name))

    if san_entries:
        builder = builder.add_extension(
            x509.SubjectAlternativeName(san_entries),
            critical=False,
        )

    cert = builder.sign(ca_key, hashes.SHA256())

    # Serialize to PEM
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()

    # Parse metadata
    meta = _parse_cert_metadata(cert_pem)

    # Write files
    server_dir = os.path.join(CERT_BASE_DIR, "server")
    safe_name = req.name.replace(" ", "_").lower()
    cert_file = _write_cert_file(server_dir, f"{safe_name}.pem", cert_pem)
    key_file = _write_cert_file(server_dir, f"{safe_name}.key", key_pem)

    # Build SAN list for DB (subject_alt_names is TEXT[] array)
    san_list = list(req.san_dns) + list(req.san_ips)
    subject_alt_names = san_list if san_list else None

    # Store in DB
    result = await db.execute(
        text(
            "INSERT INTO certificates "
            "(cert_type, name, common_name, issuer, serial_number, "
            "not_before, not_after, fingerprint_sha256, key_algorithm, key_size, "
            "subject_alt_names, pem_data, key_pem_encrypted, "
            "is_active, is_self_signed, imported, enabled, "
            "tenant_id, created_by) "
            "VALUES ('server', :name, :common_name, :issuer, :serial_number, "
            ":not_before, :not_after, :fingerprint_sha256, :key_algorithm, :key_size, "
            ":subject_alt_names, :pem_data, :key_pem_encrypted, "
            "false, false, false, true, "
            ":tenant_id, :created_by) "
            "RETURNING *"
        ),
        {
            "name": req.name,
            "common_name": meta["common_name"],
            "issuer": meta["issuer"],
            "serial_number": meta["serial_number"],
            "not_before": meta["not_before"],
            "not_after": meta["not_after"],
            "fingerprint_sha256": meta["fingerprint_sha256"],
            "key_algorithm": meta["key_algorithm"],
            "key_size": meta["key_size"],
            "subject_alt_names": subject_alt_names,
            "pem_data": cert_pem,
            "key_pem_encrypted": key_pem,  # TODO: encrypt via Vault
            "tenant_id": user["tenant_id"],
            "created_by": user["sub"],
        },
    )
    row = result.mappings().first()

    await log_audit(
        db, user, "create", "certificate",
        resource_id=str(row["id"]),
        details={"cert_type": "server", "name": req.name, "common_name": req.common_name},
    )

    item = dict(row)
    item.update(_compute_cert_status(row["not_after"]))
    return item


@router.post("/import", status_code=201)
async def import_certificate(
    req: ImportCertRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Import an external certificate from PEM data."""
    # Parse cert to extract metadata
    try:
        meta = _parse_cert_metadata(req.cert_pem)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid certificate PEM: {e}",
        )

    # Validate key if provided
    if req.key_pem:
        try:
            serialization.load_pem_private_key(req.key_pem.encode(), password=None)
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid private key PEM: {e}",
            )

    result = await db.execute(
        text(
            "INSERT INTO certificates "
            "(cert_type, name, common_name, issuer, serial_number, "
            "not_before, not_after, fingerprint_sha256, key_algorithm, key_size, "
            "pem_data, key_pem_encrypted, chain_pem, "
            "is_active, is_self_signed, imported, enabled, "
            "tenant_id, created_by) "
            "VALUES (:cert_type, :name, :common_name, :issuer, :serial_number, "
            ":not_before, :not_after, :fingerprint_sha256, :key_algorithm, :key_size, "
            ":pem_data, :key_pem_encrypted, :chain_pem, "
            "false, :is_self_signed, true, true, "
            ":tenant_id, :created_by) "
            "RETURNING *"
        ),
        {
            "cert_type": req.cert_type,
            "name": req.name,
            "common_name": meta["common_name"],
            "issuer": meta["issuer"],
            "serial_number": meta["serial_number"],
            "not_before": meta["not_before"],
            "not_after": meta["not_after"],
            "fingerprint_sha256": meta["fingerprint_sha256"],
            "key_algorithm": meta["key_algorithm"],
            "key_size": meta["key_size"],
            "pem_data": req.cert_pem,
            "key_pem_encrypted": req.key_pem,  # TODO: encrypt via Vault
            "chain_pem": req.chain_pem,
            "is_self_signed": meta["is_self_signed"],
            "tenant_id": user["tenant_id"],
            "created_by": user["sub"],
        },
    )
    row = result.mappings().first()

    await log_audit(
        db, user, "create", "certificate",
        resource_id=str(row["id"]),
        details={
            "cert_type": req.cert_type, "name": req.name,
            "imported": True, "common_name": meta["common_name"],
        },
    )

    item = dict(row)
    item.update(_compute_cert_status(row["not_after"]))
    return item


@router.put("/{cert_id}/activate")
async def activate_certificate(
    cert_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Set a certificate as active. Deactivates other certs of the same type."""
    # Get the cert to find its type
    result = await db.execute(
        text(
            "SELECT id, cert_type, name FROM certificates "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(cert_id), "tenant_id": user["tenant_id"]},
    )
    cert = result.mappings().first()
    if not cert:
        raise HTTPException(status_code=404, detail="Certificate not found")

    # Deactivate all certs of the same type for this tenant
    await db.execute(
        text(
            "UPDATE certificates SET is_active = false, updated_at = NOW() "
            "WHERE cert_type = :cert_type AND tenant_id = :tenant_id"
        ),
        {"cert_type": cert["cert_type"], "tenant_id": user["tenant_id"]},
    )

    # Activate the selected cert
    result = await db.execute(
        text(
            "UPDATE certificates SET is_active = true, updated_at = NOW() "
            "WHERE id = :id AND tenant_id = :tenant_id "
            "RETURNING *"
        ),
        {"id": str(cert_id), "tenant_id": user["tenant_id"]},
    )
    row = result.mappings().first()

    await log_audit(
        db, user, "update", "certificate",
        resource_id=str(cert_id),
        details={
            "action": "activate",
            "cert_type": cert["cert_type"],
            "name": cert["name"],
        },
    )

    # Notify FreeRADIUS to reload certificates
    await nats_client.publish("orw.config.freeradius.apply", {
        "reason": "certificate_activated",
        "cert_id": str(cert_id),
        "cert_type": cert["cert_type"],
    })

    item = dict(row)
    if row["not_after"]:
        item.update(_compute_cert_status(row["not_after"]))
    return item


@router.delete("/{cert_id}", status_code=204)
async def delete_certificate(
    cert_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Delete a certificate. Cannot delete an active certificate."""
    # Check if active
    result = await db.execute(
        text(
            "SELECT id, name, is_active, cert_type FROM certificates "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(cert_id), "tenant_id": user["tenant_id"]},
    )
    cert = result.mappings().first()
    if not cert:
        raise HTTPException(status_code=404, detail="Certificate not found")

    if cert["is_active"]:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete an active certificate. Deactivate it first by activating another certificate.",
        )

    await db.execute(
        text(
            "DELETE FROM certificates WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(cert_id), "tenant_id": user["tenant_id"]},
    )

    await log_audit(
        db, user, "delete", "certificate",
        resource_id=str(cert_id),
        details={"name": cert["name"], "cert_type": cert["cert_type"]},
    )


@router.get("/{cert_id}/download")
async def download_certificate(
    cert_id: UUID,
    include_key: bool = Query(False, description="Include private key in download"),
    include_chain: bool = Query(False, description="Include certificate chain"),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Download certificate PEM file."""
    result = await db.execute(
        text(
            "SELECT name, cert_type, pem_data, key_pem_encrypted, chain_pem "
            "FROM certificates "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(cert_id), "tenant_id": user["tenant_id"]},
    )
    cert = result.mappings().first()
    if not cert:
        raise HTTPException(status_code=404, detail="Certificate not found")

    if not cert["pem_data"]:
        raise HTTPException(status_code=404, detail="Certificate PEM data not available")

    # Build PEM output
    pem_output = cert["pem_data"]

    if include_chain and cert["chain_pem"]:
        pem_output += "\n" + cert["chain_pem"]

    if include_key and cert["key_pem_encrypted"]:
        pem_output += "\n" + cert["key_pem_encrypted"]

    safe_name = cert["name"].replace(" ", "_").lower()
    filename = f"{safe_name}.pem"

    return Response(
        content=pem_output,
        media_type="application/x-pem-file",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
