"""Pure unit tests for the certificates crypto helpers.

These tests run real RSA-2048 generation against the cryptography
library. ``key_size=2048`` keeps generation fast (~0.1s per cert) so
the suite stays under a couple of seconds.
"""
from datetime import datetime, timedelta, timezone

import pytest

from features.certificates import crypto
from orw_common.exceptions import ValidationError


# ---------------------------------------------------------------------------
# parse_cert_metadata
# ---------------------------------------------------------------------------

class TestParseCertMetadata:
    def test_round_trips_a_self_signed_ca(self):
        cert_pem, _ = crypto.generate_ca_keypair(
            common_name="orw-test-ca",
            organization="OpenRadiusWeb",
            country="TW",
            validity_days=365,
            key_size=2048,
        )
        meta = crypto.parse_cert_metadata(cert_pem)
        assert meta["common_name"] == "orw-test-ca"
        assert meta["issuer"] == "orw-test-ca"  # self-signed
        assert meta["is_self_signed"] is True
        assert meta["key_algorithm"].startswith("RSA")
        assert meta["key_size"] == 2048
        assert ":" in meta["fingerprint_sha256"]

    def test_invalid_pem_raises_validation_error(self):
        with pytest.raises(ValidationError):
            crypto.parse_cert_metadata("not a valid pem")


# ---------------------------------------------------------------------------
# validate_private_key_pem
# ---------------------------------------------------------------------------

class TestValidatePrivateKeyPem:
    def test_valid_key_passes(self):
        _, key_pem = crypto.generate_ca_keypair(
            common_name="x", organization=None, country=None,
            validity_days=1, key_size=2048,
        )
        crypto.validate_private_key_pem(key_pem)  # no raise

    def test_bad_key_raises_validation_error(self):
        with pytest.raises(ValidationError):
            crypto.validate_private_key_pem("garbage")


# ---------------------------------------------------------------------------
# compute_cert_status
# ---------------------------------------------------------------------------

class TestComputeCertStatus:
    def test_none_returns_unknown(self):
        out = crypto.compute_cert_status(None)
        assert out == {"days_until_expiry": None, "status": "unknown"}

    def test_expired(self):
        out = crypto.compute_cert_status(
            datetime.now(timezone.utc) - timedelta(days=5),
        )
        assert out["status"] == "expired"
        assert out["days_until_expiry"] < 0

    def test_expiring_soon_at_30_days(self):
        out = crypto.compute_cert_status(
            datetime.now(timezone.utc) + timedelta(days=15),
        )
        assert out["status"] == "expiring_soon"

    def test_valid_beyond_30_days(self):
        out = crypto.compute_cert_status(
            datetime.now(timezone.utc) + timedelta(days=90),
        )
        assert out["status"] == "valid"

    def test_naive_datetime_treated_as_utc(self):
        # not_after coming back from PG without tzinfo shouldn't crash
        naive = datetime.utcnow() + timedelta(days=10)
        out = crypto.compute_cert_status(naive)
        assert out["status"] == "expiring_soon"


# ---------------------------------------------------------------------------
# generate_server_keypair (chain validation)
# ---------------------------------------------------------------------------

class TestGenerateServerKeypair:
    def test_server_cert_is_signed_by_supplied_ca(self):
        ca_cert_pem, ca_key_pem = crypto.generate_ca_keypair(
            common_name="orw-ca",
            organization=None, country=None,
            validity_days=365, key_size=2048,
        )
        server_pem, _ = crypto.generate_server_keypair(
            common_name="auth.orw.local",
            san_dns=["auth.orw.local"],
            san_ips=["10.0.0.1"],
            validity_days=90,
            key_size=2048,
            ca_cert_pem=ca_cert_pem,
            ca_key_pem=ca_key_pem,
        )
        meta = crypto.parse_cert_metadata(server_pem)
        assert meta["common_name"] == "auth.orw.local"
        assert meta["issuer"] == "orw-ca"  # signed by the CA
        assert meta["is_self_signed"] is False


# ---------------------------------------------------------------------------
# safe_filename
# ---------------------------------------------------------------------------

class TestSafeFilename:
    def test_lowercases_and_replaces_spaces(self):
        assert crypto.safe_filename("My Cert Name") == "my_cert_name.pem"

    def test_custom_suffix(self):
        assert crypto.safe_filename("Foo Bar", "key") == "foo_bar.key"
