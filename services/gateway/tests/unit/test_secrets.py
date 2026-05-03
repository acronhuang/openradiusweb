"""Unit tests for utils/secrets.py — AES-GCM + Argon2id encryption."""
from __future__ import annotations

import base64
import os
import secrets

import pytest


# Fix env vars BEFORE importing the module — _Vault derives the key
# lazily on first use, but we want all tests in this file to share a
# single deterministic key for speed (Argon2id key derivation is
# intentionally slow, ~100ms per call). Using a separate set of vars
# from production avoids any chance of leaking real keys into test logs.
_TEST_MASTER = "test-only-master-DO-NOT-USE-IN-PROD-" + secrets.token_urlsafe(16)
_TEST_SALT = base64.urlsafe_b64encode(b"test-salt-16byte").rstrip(b"=").decode("ascii")
os.environ["ORW_SECRET_MASTER"] = _TEST_MASTER
os.environ["ORW_SECRET_KDF_SALT"] = _TEST_SALT


from orw_common.secrets import (  # noqa: E402  — env must be set before import
    _Vault,
    decrypt_secret,
    encrypt_secret,
    is_encrypted,
)
from cryptography.exceptions import InvalidTag  # noqa: E402


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "plaintext",
    [
        "simple",
        "with spaces and !@#$%^&*() symbols",
        "中文密碼測試",
        "very long " * 100,
        "",  # empty string is not None — valid input
        "x",
    ],
)
def test_round_trip(plaintext):
    ct = encrypt_secret(plaintext)
    assert ct != plaintext, "ciphertext must differ from plaintext"
    pt = decrypt_secret(ct)
    assert pt == plaintext


def test_none_passes_through():
    assert encrypt_secret(None) is None
    assert decrypt_secret(None) is None


# ---------------------------------------------------------------------------
# Nonce uniqueness — same plaintext encrypted twice produces different ct
# ---------------------------------------------------------------------------

def test_distinct_nonces_per_encrypt():
    pt = "same input"
    ct1 = encrypt_secret(pt)
    ct2 = encrypt_secret(pt)
    assert ct1 != ct2, "AES-GCM nonce must be fresh per call"
    # But both decrypt to the original
    assert decrypt_secret(ct1) == pt
    assert decrypt_secret(ct2) == pt


# ---------------------------------------------------------------------------
# Tamper detection
# ---------------------------------------------------------------------------

def test_tampered_ciphertext_raises_invalid_tag():
    ct = encrypt_secret("sensitive-payload")
    # Flip a byte in the middle (where the actual ciphertext bytes live)
    raw = base64.urlsafe_b64decode(ct + "=" * (-len(ct) % 4))
    tampered = bytearray(raw)
    # Flip a byte in the encrypted payload (after version+nonce, before tag).
    # Index 15 is safely inside the payload portion for any non-empty input.
    tampered[15] ^= 0x01
    tampered_b64 = base64.urlsafe_b64encode(bytes(tampered)).decode("ascii")

    with pytest.raises(InvalidTag):
        decrypt_secret(tampered_b64)


def test_tampered_tag_raises_invalid_tag():
    ct = encrypt_secret("sensitive-payload")
    raw = base64.urlsafe_b64decode(ct + "=" * (-len(ct) % 4))
    tampered = bytearray(raw)
    # Last byte is part of the auth tag; flip it.
    tampered[-1] ^= 0x01
    tampered_b64 = base64.urlsafe_b64encode(bytes(tampered)).decode("ascii")

    with pytest.raises(InvalidTag):
        decrypt_secret(tampered_b64)


# ---------------------------------------------------------------------------
# Strict mode: unrecognised input raises (no plaintext passthrough)
# ---------------------------------------------------------------------------
#
# Phase 1 migration completed 2026-05-03 — every encrypted column on prod
# is now real ciphertext, so the historical "return input unchanged on
# unrecognised format" fallback was removed. Unrecognised input now means
# the column was bypassed (bug); fail loudly so it surfaces.

@pytest.mark.parametrize(
    "garbage",
    [
        "MyOldPlaintextPassword!",
        "123456",
        "with spaces",
        # Looks like base64 but the version byte is wrong:
        base64.urlsafe_b64encode(bytes([0x99]) + b"\x00" * 28).decode("ascii"),
        # Valid base64 but too short for a nonce + tag:
        base64.urlsafe_b64encode(bytes([0x01, 0x02, 0x03])).decode("ascii"),
        # Not base64 at all:
        "this~has@invalid#chars",
    ],
)
def test_decrypt_unrecognised_input_raises(garbage):
    with pytest.raises(ValueError):
        decrypt_secret(garbage)


def test_decrypt_empty_string_returns_empty():
    """Empty string is treated as 'no value' — matches the historical
    NULL/empty-column behaviour and avoids breaking callers that store
    empty defaults. Real ciphertext can never be empty (min 29 bytes)."""
    assert decrypt_secret("") == ""


# ---------------------------------------------------------------------------
# is_encrypted helper
# ---------------------------------------------------------------------------

def test_is_encrypted_true_for_real_ciphertext():
    ct = encrypt_secret("hello")
    assert is_encrypted(ct) is True


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "MyOldPlaintextPassword!",
        "not~base64@#$",
        # Valid base64 but wrong version byte:
        base64.urlsafe_b64encode(bytes([0xFF]) + b"\x00" * 28).decode("ascii"),
        # Too short:
        base64.urlsafe_b64encode(bytes([0x01, 0x02])).decode("ascii"),
    ],
)
def test_is_encrypted_false_for_non_ciphertext(value):
    assert is_encrypted(value) is False


# ---------------------------------------------------------------------------
# Wrong-key rejection
# ---------------------------------------------------------------------------

def test_decrypt_with_different_key_raises():
    """A blob encrypted under one key must not decrypt under another."""
    ct = encrypt_secret("payload")
    # Build a second vault with a different derived key by directly
    # constructing its AESGCM with random bytes.
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    other_vault = _Vault()
    other_vault._aead = AESGCM(os.urandom(32))

    raw = base64.urlsafe_b64decode(ct + "=" * (-len(ct) % 4))
    nonce = raw[1 : 1 + 12]
    ct_bytes = raw[1 + 12 :]

    with pytest.raises(InvalidTag):
        other_vault._aesgcm().decrypt(nonce, ct_bytes, None)


# ---------------------------------------------------------------------------
# Type validation
# ---------------------------------------------------------------------------

def test_encrypt_rejects_non_str():
    with pytest.raises(TypeError):
        encrypt_secret(b"bytes-not-str")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        encrypt_secret(123)  # type: ignore[arg-type]


def test_decrypt_rejects_non_str():
    with pytest.raises(TypeError):
        decrypt_secret(b"bytes-not-str")  # type: ignore[arg-type]
