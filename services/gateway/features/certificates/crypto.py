"""Pure crypto helpers for the certificates feature.

These functions are standalone — no DB, no FastAPI, no NATS. The
service layer composes them into use cases. Splitting them out keeps
the service file small and lets us unit-test the crypto behaviour
without async fixtures.
"""
import ipaddress
import os
from datetime import datetime, timedelta, timezone
from typing import Iterable

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from orw_common.exceptions import ValidationError


CERT_BASE_DIR = "/opt/orw/certs"


# ---------------------------------------------------------------------------
# Parsing / status
# ---------------------------------------------------------------------------

def parse_cert_metadata(cert_pem: str) -> dict:
    """Extract metadata from a PEM-encoded certificate.

    Raises ValidationError if the PEM cannot be parsed (caller maps to 400).
    """
    try:
        cert = x509.load_pem_x509_certificate(cert_pem.encode())
    except Exception as exc:
        raise ValidationError(f"Invalid certificate PEM: {exc}") from exc

    cn_attrs = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    common_name = cn_attrs[0].value if cn_attrs else None

    issuer_cn_attrs = cert.issuer.get_attributes_for_oid(NameOID.COMMON_NAME)
    issuer = issuer_cn_attrs[0].value if issuer_cn_attrs else str(cert.issuer)

    fingerprint_sha256 = cert.fingerprint(hashes.SHA256()).hex(":")

    public_key = cert.public_key()
    key_algorithm = type(public_key).__name__.replace("_", "").replace("PublicKey", "")
    key_size = public_key.key_size if hasattr(public_key, "key_size") else None

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


def validate_private_key_pem(key_pem: str) -> None:
    """Round-trip the PEM through cryptography to confirm it parses.

    Raises ValidationError on failure.
    """
    try:
        serialization.load_pem_private_key(key_pem.encode(), password=None)
    except Exception as exc:
        raise ValidationError(f"Invalid private key PEM: {exc}") from exc


def compute_cert_status(not_after: datetime | None) -> dict:
    """days_until_expiry + status (valid / expiring_soon / expired / unknown)."""
    if not_after is None:
        return {"days_until_expiry": None, "status": "unknown"}
    if not_after.tzinfo is None:
        not_after = not_after.replace(tzinfo=timezone.utc)
    days = (not_after - datetime.now(timezone.utc)).days
    if days < 0:
        status = "expired"
    elif days <= 30:
        status = "expiring_soon"
    else:
        status = "valid"
    return {"days_until_expiry": days, "status": status}


# ---------------------------------------------------------------------------
# Key + certificate generation
# ---------------------------------------------------------------------------

def generate_ca_keypair(
    *,
    common_name: str,
    organization: str | None,
    country: str | None,
    validity_days: int,
    key_size: int,
) -> tuple[str, str]:
    """Self-signed CA with BasicConstraints CA=true + key cert/CRL signing.

    Returns (cert_pem, key_pem).
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)

    name_attrs = [x509.NameAttribute(NameOID.COMMON_NAME, common_name)]
    if organization:
        name_attrs.append(x509.NameAttribute(NameOID.ORGANIZATION_NAME, organization))
    if country:
        name_attrs.append(x509.NameAttribute(NameOID.COUNTRY_NAME, country))
    subject = issuer = x509.Name(name_attrs)
    now = datetime.now(timezone.utc)

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=validity_days))
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
    return _serialise(cert, key)


def generate_server_keypair(
    *,
    common_name: str,
    san_dns: Iterable[str],
    san_ips: Iterable[str],
    validity_days: int,
    key_size: int,
    ca_cert_pem: str,
    ca_key_pem: str,
) -> tuple[str, str]:
    """Server cert signed by the supplied CA. Always includes CN as a SAN."""
    ca_cert = x509.load_pem_x509_certificate(ca_cert_pem.encode())
    ca_key = serialization.load_pem_private_key(ca_key_pem.encode(), password=None)

    key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.now(timezone.utc)

    san_entries: list[x509.GeneralName] = []
    for dns_name in san_dns:
        san_entries.append(x509.DNSName(dns_name))
    for ip_str in san_ips:
        san_entries.append(x509.IPAddress(ipaddress.ip_address(ip_str)))
    san_entries.append(x509.DNSName(common_name))

    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=validity_days))
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
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .add_extension(
            x509.SubjectAlternativeName(san_entries),
            critical=False,
        )
    )
    cert = builder.sign(ca_key, hashes.SHA256())
    return _serialise(cert, key)


def _serialise(cert: x509.Certificate, key) -> tuple[str, str]:
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    return cert_pem, key_pem


# ---------------------------------------------------------------------------
# Filesystem
# ---------------------------------------------------------------------------

def write_cert_files(
    subdir: str, *, name: str, cert_pem: str, key_pem: str,
    base_dir: str = CERT_BASE_DIR,
) -> tuple[str, str]:
    """Write {name}.pem + {name}.key under base_dir/subdir with 0o600 perms."""
    directory = os.path.join(base_dir, subdir)
    os.makedirs(directory, exist_ok=True)
    safe_name = name.replace(" ", "_").lower()
    cert_path = os.path.join(directory, f"{safe_name}.pem")
    key_path = os.path.join(directory, f"{safe_name}.key")
    _write(cert_path, cert_pem)
    _write(key_path, key_pem)
    return cert_path, key_path


def _write(path: str, data: str) -> None:
    with open(path, "w") as f:
        f.write(data)
    os.chmod(path, 0o600)


def safe_filename(name: str, suffix: str = "pem") -> str:
    return f"{name.replace(' ', '_').lower()}.{suffix}"
