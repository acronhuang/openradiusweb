"""Symmetric encryption for secrets stored in DB.

AES-256-GCM AEAD with the encryption key derived from a master secret
via Argon2id. Replaces the plaintext storage of bind passwords, RADIUS
shared secrets, TLS private keys, etc. — see
docs/security-audit-2026-05-02-secret-storage.md for the rationale.

Why this scheme:
- AES-256-GCM is a modern AEAD with built-in tamper detection (vs
  Fernet's older AES-128-CBC + HMAC composition).
- Per-record 96-bit nonce, no IV reuse risk for our throughput.
- Argon2id (RFC 9106 second profile) makes a leaked .env still costly
  to attack. A raw key in .env is "leak-once, lose-everything"; a
  password-derived key adds memory-hard cost per attempt.
- Authentication tag catches DB tampering at decryption time.

Storage format per ciphertext (base64-encoded into the existing
`*_encrypted text` columns):

    version_byte(1) || nonce(12) || aesgcm_output(N+16)

The version byte lets us migrate to a new scheme later without breaking
old rows.

Key material lifecycle:
- ORW_SECRET_MASTER         high-entropy random string, in .env
- ORW_SECRET_KDF_SALT       per-deployment random salt, in .env
- Derived encryption key    Argon2id(master, salt) -> 32 bytes,
                            cached in process memory after first call

Strictness: decrypt() raises ValueError on input that isn't
recognisable ciphertext (wrong base64, wrong version byte, wrong
length). Earlier versions silently returned the input unchanged so
unmigrated plaintext rows kept working — that fallback was removed
after the Phase 1 migration was verified end-to-end on production
(2026-05-03; see docs/security-audit-2026-05-02-secret-storage.md).

Empty strings are passed through unchanged (treated as "no value")
because empty cannot possibly be valid ciphertext (min ciphertext
length is 29 bytes after base64 = ~40 chars).

If you hit a ValueError after this point it means a stored row was
written without going through encrypt_secret() — that's the bug, not
the strictness. The pre-commit hook
scripts/check_encrypted_columns_wrapped.py exists to catch this at
review time.
"""
from __future__ import annotations

import base64
import os
from typing import Optional

from argon2.low_level import Type as Argon2Type, hash_secret_raw
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


_VERSION = 0x01
_NONCE_LEN = 12
_TAG_LEN = 16
_KEY_LEN = 32

# Argon2id parameters: RFC 9106 "second recommended" profile, tuned for
# ~100ms key derivation on a server-class CPU. Run once at startup, the
# result is cached, so the cost is paid once per process lifetime.
_KDF_TIME_COST = 3
_KDF_MEMORY_COST_KB = 64 * 1024  # 64 MiB
_KDF_PARALLELISM = 4

_ENV_MASTER = "ORW_SECRET_MASTER"
_ENV_SALT = "ORW_SECRET_KDF_SALT"

_KEYGEN_HINT = (
    "Generate both with:\n"
    '  python -c "import secrets; '
    'print(\'ORW_SECRET_MASTER=\' + secrets.token_urlsafe(48)); '
    'print(\'ORW_SECRET_KDF_SALT=\' + secrets.token_urlsafe(16))"'
)


def _b64url_decode(s: str) -> bytes:
    """Permissive urlsafe-base64 decode that fixes missing padding."""
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _derive_key() -> bytes:
    master = os.environ.get(_ENV_MASTER, "")
    salt_b64 = os.environ.get(_ENV_SALT, "")
    if not master or not salt_b64:
        missing = [
            name
            for name, val in ((_ENV_MASTER, master), (_ENV_SALT, salt_b64))
            if not val
        ]
        raise RuntimeError(
            f"Missing env var(s): {', '.join(missing)}.\n{_KEYGEN_HINT}"
        )
    try:
        salt = _b64url_decode(salt_b64)
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        raise RuntimeError(
            f"{_ENV_SALT} is not valid urlsafe-base64: {exc}"
        ) from exc
    if len(salt) < 8:
        raise RuntimeError(
            f"{_ENV_SALT} decodes to {len(salt)} bytes; need >= 8."
        )
    return hash_secret_raw(
        secret=master.encode("utf-8"),
        salt=salt,
        time_cost=_KDF_TIME_COST,
        memory_cost=_KDF_MEMORY_COST_KB,
        parallelism=_KDF_PARALLELISM,
        hash_len=_KEY_LEN,
        type=Argon2Type.ID,
    )


class _Vault:
    """Process-wide encryption helper; key derived once on first use."""

    def __init__(self) -> None:
        self._aead: Optional[AESGCM] = None

    def _aesgcm(self) -> AESGCM:
        if self._aead is None:
            self._aead = AESGCM(_derive_key())
        return self._aead

    def encrypt(self, plaintext: Optional[str]) -> Optional[str]:
        if plaintext is None:
            return None
        if not isinstance(plaintext, str):
            raise TypeError(
                f"encrypt_secret expects str, got {type(plaintext).__name__}"
            )
        nonce = os.urandom(_NONCE_LEN)
        ct_and_tag = self._aesgcm().encrypt(nonce, plaintext.encode("utf-8"), None)
        blob = bytes([_VERSION]) + nonce + ct_and_tag
        return base64.urlsafe_b64encode(blob).decode("ascii")

    def decrypt(self, ciphertext: Optional[str]) -> Optional[str]:
        if ciphertext is None:
            return None
        if not isinstance(ciphertext, str):
            raise TypeError(
                f"decrypt_secret expects str, got {type(ciphertext).__name__}"
            )
        # Empty string can never be valid ciphertext (minimum is
        # version byte + 12-byte nonce + 16-byte tag = 29 bytes, then
        # base64). Treat as "no value" to match the historical
        # behaviour for empty columns.
        if ciphertext == "":
            return ""
        try:
            blob = _b64url_decode(ciphertext)
        except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
            raise ValueError(
                "decrypt_secret: input is not valid urlsafe-base64. "
                "This usually means the column was written without going "
                "through encrypt_secret(). Check the migration history "
                "and the encrypted-columns-wrapped pre-commit hook."
            ) from exc

        if len(blob) < 1 + _NONCE_LEN + _TAG_LEN:
            raise ValueError(
                f"decrypt_secret: blob is {len(blob)} bytes; need at "
                f"least {1 + _NONCE_LEN + _TAG_LEN} (version + nonce + tag)."
            )
        if blob[0] != _VERSION:
            raise ValueError(
                f"decrypt_secret: unknown version byte 0x{blob[0]:02x} "
                f"(expected 0x{_VERSION:02x}). Either the row predates "
                f"the encryption migration, or a future version was "
                f"written by a newer build that this process can't read."
            )

        nonce = blob[1 : 1 + _NONCE_LEN]
        ct_and_tag = blob[1 + _NONCE_LEN :]
        try:
            return self._aesgcm().decrypt(nonce, ct_and_tag, None).decode("utf-8")
        except InvalidTag:
            # Tag mismatch = ciphertext was tampered, OR wrong key.
            # Loud failure is correct here — never silently fall back to
            # the input, that would let an attacker swap ciphertext for
            # plaintext and have it accepted.
            raise


_vault = _Vault()
encrypt_secret = _vault.encrypt
decrypt_secret = _vault.decrypt


def is_encrypted(value: Optional[str]) -> bool:
    """Cheap structural check — does this look like our ciphertext format?

    True iff `value` is non-None, valid urlsafe-base64, and decodes to
    a blob of the right shape (version byte + nonce + tag minimum).
    Used by the migration script to skip already-encrypted rows.
    Doesn't actually try to decrypt — that would need the key.
    """
    if value is None or not isinstance(value, str):
        return False
    try:
        blob = _b64url_decode(value)
    except (ValueError, base64.binascii.Error):  # type: ignore[attr-defined]
        return False
    return (
        len(blob) >= 1 + _NONCE_LEN + _TAG_LEN
        and blob[0] == _VERSION
    )
