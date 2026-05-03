# Security Audit — Secret Storage at Rest (2026-05-02)

**Scope**: How OpenRadiusWeb stores credentials in PostgreSQL and how those
credentials surface in logs.

**Result**: 🔴 **6 columns store plaintext under names ending in `_encrypted`**.
The naming is aspirational — no encryption layer exists in the codebase.
This is misleading to future maintainers and DB admins, and any DB compromise
exposes every backend secret.

## Status of remediation (updated 2026-05-03 — Phase 1 COMPLETE; strict-mode flipped 2026-05-03)

> **2026-05-03 update**: After every production row was verified as
> ciphertext, `decrypt_secret()` was switched from permissive (return
> input on unrecognised format) to strict (raise `ValueError`). This
> closes the last attacker-substitution path — see
> shared/orw_common/secrets.py for the as-shipped implementation. The
> code blocks under §4 below are the original 2026-05-02 design
> proposal, kept as historical context.



| Column | Status | PR |
|---|---|---|
| `ldap_servers.bind_password_encrypted` | ✅ Deployed + verified end-to-end on production | #71 |
| `radius_nas_clients.secret_encrypted` | ✅ Deployed + verified end-to-end on production | #73 |
| `certificates.key_pem_encrypted` | ✅ Deployed + verified end-to-end on production | #74 |
| `radius_realms.proxy_secret_encrypted` | ✅ Deployed + verified end-to-end on production | #74 |
| `network_devices.snmp_community_encrypted` | ✅ Deployed + verified end-to-end on production | #74 |
| `network_devices.coa_secret_encrypted` | ✅ Deployed + verified end-to-end on production | #74 |

**All 6 originally-flagged columns now have encrypt-on-write at the
gateway boundary + decrypt-on-read at every consuming service.**
End-to-end verified on 2026-05-03 with both auth paths working under
strict-mode `decrypt_secret()`:

- 802.1X TTLS+PAP via LDAP: `Login OK: [ming@mds.local] via TLS tunnel`
- MAB: `Login OK: [3C-13-5A-CC-21-21] from Fortigate-60C`

See [session-2026-05-03 §Phase E](session-2026-05-03-encryption-rollout.md#phase-e--production-rollout-live-execution-log)
for the live rollout log.

### Hardening PRs that closed latent bugs found during deploy

| PR | What it fixed |
|---|---|
| #82 | Post-deploy verification runbook + pre-commit hook to block plaintext writes to `*_encrypted` columns |
| #83 | Strict-mode `decrypt_secret()` — raises on unrecognised input instead of silent passthrough |
| #84 | **Critical**: prod compose was missing `ORW_SECRET_MASTER` + `ORW_SECRET_KDF_SALT` on all 5 services that need them. PR #83 strict mode exposed this within minutes of deploy — the encryption had effectively been a no-op in prod since #71-#74. Test extended to cover both dev + prod compose files |
| #85 | Dev/prod compose service name parity (`coa` → `coa_service`) |
| #86 | Key rotation runbook + `scripts/rotate_secret_master.py` |
| #87 | Watcher SIGHUP storm — `next_msg()` timeout bug + missing idempotency guard + non-deterministic Jinja templates |
| #88 | `tenant_id NULL` defeats `ON CONFLICT` UPSERT — idempotency guard from #87 wasn't actually working at the DB level. Cleanup script removed ~945k bloat rows |
| #90 | Residual SIGHUP — Bug D (cert files always status=applied; needed input hash) + Bug E (`generate_clients_config` and `generate_proxy_config` are inline string-builders, not Jinja, and still embedded `# Generated at: <now>`). Steady-state HUP rate now **0** per 5 min |

### What's done elsewhere (no longer pending)

- **Backup encryption** — PR #81 added `gpg --symmetric AES-256` for
  every `scripts/backup.sh` output via `ORW_BACKUP_PASSPHRASE`.
- **DB connection error log scrubbing** — PR #77 added
  `shared/orw_common/db_url_safe.py` (`mask_db_url`, `format_db_error`,
  `scrub_message`); migration scripts use it on connect failure.

### Still pending (Phase 2 / future)

- **Vault or Cloud KMS** — current scheme stores `ORW_SECRET_MASTER`
  in `.env.production`; if `.env.production` leaks, the master +
  Argon2id KDF cost is the only thing between the attacker and
  decrypting every secret. Vault adds dynamic short-lived secrets +
  central audit log + zero-downtime rotation.
- **TLS server cert auto-rotation** — currently manual via the UI.
  Build a cron + watcher loop that triggers renewal before expiry.

---

## 1. Findings

### 1.1 Plaintext passwords with misleading column names

Database columns named `*_encrypted` actually contain **cleartext**:

| Schema | Column | What it stores | Risk if leaked |
|--------|--------|----------------|----------------|
| `ldap_servers` | `bind_password_encrypted` | AD service-account password | 🔴 **critical** — read entire AD as service account |
| `radius_nas_clients` | `secret_encrypted` | RADIUS NAS shared secret | 🔴 **critical** — forge NAS / decrypt captured RADIUS |
| `certificates` | `key_pem_encrypted` | TLS server private key (PEM) | 🔴 **critical** — impersonate RADIUS server |
| `radius_realms` | `proxy_secret_encrypted` | RADIUS proxy shared secret | 🟠 high — forge proxy |
| `network_devices` | `coa_secret_encrypted` | CoA RADIUS secret | 🟠 high — issue rogue Disconnect-Request |
| `network_devices` | `snmp_community_encrypted` | SNMP community string | 🟡 medium — read device state |

Verified live on 192.168.0.250:

```
mds=# SELECT host, bind_dn, bind_password_encrypted FROM ldap_servers WHERE enabled=true;
     host      |                bind_dn                 | bind_password_encrypted
---------------+----------------------------------------+-------------------------
 192.168.0.253 | CN=Radius_MGM,CN=Users,DC=mds,DC=local | !QAZxcvfr432wsde
```

### 1.2 No encryption layer exists

```bash
$ grep -rE "decrypt|encrypt|Fernet|cipher" services/gateway/ --include="*.py"
# Only matches: services/gateway/features/certificates/crypto.py
# (X.509 cert parsing — uses cryptography lib's classes named Encipherment/Decipherment;
#  not actually encrypting any password)
```

The `cryptography` Python package IS installed (used for X.509 in
`services/gateway/features/certificates/crypto.py` and freeradius certs),
but is NOT used to encrypt the 6 columns above.

### 1.3 Log leakage paths

| Leak | Severity | Status | Notes |
|------|----------|--------|-------|
| `auth_goodpass = yes` in radiusd.conf | 🔴 critical | ✅ **fixed PR #58** | Was logging cleartext passwords on every Auth: line |
| `auth_badpass = yes` in radiusd.conf | 🔴 critical | ✅ **fixed PR #58** | Same as above on rejects |
| DB connection error logs DB URL | 🟠 high | ⚠️ **open** | `psycopg2.connect()` exception includes the URL with password |
| LDAP bind error logs may include DSN | 🟠 high | ⚠️ **open** | rlm_ldap can dump DN + password on debug |
| Stack traces in container logs | 🟡 medium | ⚠️ **open** | Python tracebacks can include locals |

### 1.4 What IS secure

| Item | How |
|------|-----|
| OpenRadiusWeb admin login passwords (`users.password_hash`) | ✅ bcrypt with cost factor 12 — `middleware/auth.py:hash_password()` |
| JWT tokens | ✅ HS256 signed with `JWT_SECRET_KEY` from `.env` |
| LDAPS / RADIUS over TLS | ✅ TLS 1.2+ in transit |

So: **secrets at rest = bad, secrets in transit + user passwords = good.**

---

## 2. Comparison of remediation approaches

| Approach | How it works | Effort | Security | Ops cost | Key rotation | Audit trail |
|----------|-------------|--------|----------|----------|--------------|-------------|
| **A. Status quo** (plaintext) | Nothing — store as-is | 0 | ❌ none | 0 | N/A | ❌ none |
| **B. Fernet symmetric** | Python `cryptography.fernet.Fernet`. Master key in `.env`. Encrypt on write, decrypt on read in repository layer. | 4 hrs | ⭐⭐⭐ medium | ⭐ low | manual + reencrypt all rows | ⚠️ needs DB-level audit |
| **C. AES-GCM + KDF** | `cryptography.hazmat.primitives.ciphers.aead.AESGCM`. Per-record nonce, master key from env via Argon2/scrypt KDF. | 8 hrs | ⭐⭐⭐⭐ high | ⭐⭐ med | scriptable | ⚠️ same as B |
| **D. PostgreSQL `pgcrypto`** | DB-side `pgp_sym_encrypt(secret, key)`. Key in postgres conn string or session var. | 4 hrs | ⭐⭐ low-med | ⭐⭐ med | painful (re-encrypt all rows) | ✅ DB log |
| **E. Docker / K8s Secrets** | Mount secrets as files at deploy time. Move static creds (LDAP bind, NAS secret) out of DB into config files. | 6 hrs | ⭐⭐⭐ med | ⭐⭐ med | redeploy needed | ⚠️ orchestration log |
| **F. HashiCorp Vault** | Vault stores secrets, app fetches via API at startup. Dynamic creds, lease-based. | 2-3 days | ⭐⭐⭐⭐⭐ very high | ⭐⭐⭐⭐ high (Vault HA cluster) | automatic via lease | ✅ full audit log |
| **G. Cloud KMS / Secrets Manager** | AWS Secrets Manager / GCP Secret Manager / Azure Key Vault. App uses SDK to fetch. | 1-2 days | ⭐⭐⭐⭐⭐ very high | ⭐⭐⭐ med (cloud bill) | automatic | ✅ full audit log |

### 2.1 Detailed pros/cons

#### B. Fernet symmetric (recommended for OpenRadiusWeb)

```python
# services/gateway/utils/secrets.py
from cryptography.fernet import Fernet
import os

_KEY = os.environ["ORW_SECRET_KEY"].encode()  # base64-encoded 32 bytes
_F = Fernet(_KEY)

def encrypt_secret(plaintext: str) -> str:
    return _F.encrypt(plaintext.encode()).decode()

def decrypt_secret(ciphertext: str) -> str:
    return _F.decrypt(ciphertext.encode()).decode()
```

**Repository layer** uses these helpers when reading/writing secret columns:
```python
# Before
device.bind_password_encrypted = req.bind_password
# After
device.bind_password_encrypted = encrypt_secret(req.bind_password)
```

**Pros**:
- 4 hours work, ~150 lines code
- `cryptography` already a dep (just used for certs today)
- Master key in `.env` already (alongside `DB_PASSWORD`)
- Zero new infra

**Cons**:
- Master key lives in `.env` → if `.env` leaks, all secrets leak
- Manual key rotation (write a re-encrypt script)
- Audit trail = whatever PostgreSQL logs

#### F. HashiCorp Vault (the "right" answer for SaaS scale)

**Pros**:
- Industry standard, mature
- Short-lived dynamic credentials (Vault rotates AD service account every 24hrs)
- Full audit log of who accessed which secret when
- Approved by basically every security auditor

**Cons**:
- Vault cluster itself needs HA (3-node etcd or similar)
- Adds new operational burden — one more service to monitor/upgrade/backup
- Application code needs Vault client + token renewal logic
- Overkill for a sub-50-customer deployment

#### G. Cloud KMS

**Pros**:
- Same as Vault but managed (no cluster to run)
- Automatic key rotation
- Compliance-ready (FedRAMP, SOC2, etc.)

**Cons**:
- Cloud lock-in (no swap from AWS Secrets Manager → Vault easily)
- Monthly cost (~$0.40/secret/month on AWS, ~$0.06/secret/month on GCP)
- Requires cloud connectivity at startup — air-gapped deploys impossible

---

## 3. Recommendation

> **Updated 2026-05-03**: Recommendation moved from option B (Fernet) to
> option **C (AES-GCM + KDF)**. Cloud options (F, G) are off the table per
> stakeholder direction (no cloud dependency). Between the remaining
> non-cloud options (B, C, D, E), C gives the best
> security-per-implementation-effort trade-off and is the most defensible
> in a future security audit. See [§3.1 below](#31-why-c-over-b-d-or-e)
> for the comparison.

**Phase 1 (this sprint, ~1.5 day work)**:
- ✅ Implement option **C (AES-GCM + KDF)**
- Generate a master secret (high-entropy random string) via
  `python -c "import secrets; print(secrets.token_urlsafe(48))"`,
  stored in `.env.production` next to `DB_PASSWORD`
- Derive the actual encryption key via Argon2id (or scrypt as a fallback
  — both available in `cryptography` and `argon2-cffi`) using a
  per-deployment salt (also stored in `.env.production`). The KDF makes
  brute-force on a leaked `.env` cost orders of magnitude more than a
  raw key
- Each ciphertext stores: `version_byte || nonce(12B) || ciphertext || tag(16B)`,
  base64-encoded for the existing `*_encrypted` `text` columns
- Migrate all 6 columns: write encryption helpers, update repository
  layer, write a one-shot migration script that reads existing plaintext
  + writes ciphertext
- Tests: round-trip encrypt/decrypt unit test, tamper test (modified
  ciphertext must fail decryption with `InvalidTag`), integration test
  confirming gateway can still bind LDAP after migration

**Phase 2 (next quarter, when team capacity allows)**:
- Implement option **F (Vault)** if growing past 10 customers / SOC2
  requirement
- Cloud KMS (option G) explicitly de-scoped per stakeholder decision

### 3.1 Why C over B, D, or E

| Option | Why not |
|--------|---------|
| **B Fernet** | Functional but uses AES-128-CBC + HMAC-SHA256, an older AEAD construction. Master key from env is used directly with no KDF — a leaked `.env` is instantly the encryption key. Also no per-record nonce visibility (Fernet hides it but it's there). C is "Fernet done with modern primitives". |
| **D pgcrypto** | DB-side encryption requires passing the key on every query (in connection string, session var, or function arg). SQL injection at the application layer would expose the key. Audit becomes "trust the DB log to record key usage", which is weaker than "trust the application is the only place that ever touches plaintext". |
| **E Docker / K8s Secrets** | Doesn't fit our usage pattern. Docker Secrets is for **deploy-time static** values (e.g. one fixed DB password). Our 6 columns hold **user-managed dynamic** values (operators add new NAS clients / LDAP servers / certificates via the Web UI). Mounting changes per-row would require restart per change. |
| **C AES-GCM + KDF** ✅ | Modern AEAD (AES-256-GCM with per-record 96-bit nonce), authenticated encryption catches tampering at decryption time, KDF turns the env-stored secret into a key with significant brute-force cost (Argon2id memory-hard), works from a single env var, no DB-side key handling, no infra additions. Future migration path to Vault is just swapping the helper module — column shape stays the same. |

**Don't do Phase 1 before**:
- ✅ DB-level backup encryption (current pg_dump is plaintext too — same problem one layer up)
- ⚠️ `.env.production` access controls (currently anyone with shell on the RADIUS server can `cat` it)

### 3.1 What to also fix in parallel

- **DB connection error log**: wrap `psycopg2.connect()` in try/except that masks the password before logging
- **LDAP debug logs**: confirm `rlm_ldap` is not at debug level in production
- **freeradius `radiusd.conf`**: verify `auth_goodpass = no` after every deploy (PR #58 baked this in)
- **`.env.production` permissions**: `chmod 600 .env.production` and `chown radius-app:radius-app .env.production`

---

## 4. Sample implementation skeleton (Option C — AES-GCM + KDF)

### 4.1 New file `services/gateway/utils/secrets.py`

```python
"""
Symmetric encryption for secrets stored in DB. AES-256-GCM AEAD with
the encryption key derived from a master secret via Argon2id.

Why AES-GCM + KDF instead of Fernet:
- AES-256-GCM is a modern AEAD (vs Fernet's older AES-128-CBC + HMAC)
- 96-bit per-record nonce, no IV reuse risk for our throughput
- Argon2id memory-hard KDF makes a leaked .env still costly to attack
- Authentication tag catches any DB tampering at decryption time

Storage format per ciphertext (base64-encoded into the existing
`*_encrypted text` columns):

    version_byte(1) || nonce(12) || ciphertext(N) || auth_tag(16)

Version byte lets us migrate to a new scheme later without breaking
old rows.

Key material lifecycle:
- ORW_SECRET_MASTER         high-entropy random string, in .env
- ORW_SECRET_KDF_SALT       per-deployment random salt, in .env
- Derived encryption key    Argon2id(master, salt) → 32 bytes,
                            cached in process memory after first call
"""
import base64
import os
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from argon2.low_level import hash_secret_raw, Type as Argon2Type

_VERSION = 0x01  # bump if scheme changes
_NONCE_LEN = 12  # 96 bits, AES-GCM standard

# Argon2id parameters — RFC 9106 "second recommended" profile, tuned
# for ~100ms key derivation on a server-class CPU. Run once at startup,
# result is cached, so the cost is paid once per process lifetime.
_KDF_TIME_COST = 3
_KDF_MEMORY_COST_KB = 64 * 1024  # 64 MiB
_KDF_PARALLELISM = 4
_KEY_LEN = 32  # 256-bit AES key


def _derive_key() -> bytes:
    master = os.environ.get("ORW_SECRET_MASTER", "").encode()
    salt_b64 = os.environ.get("ORW_SECRET_KDF_SALT", "")
    if not master or not salt_b64:
        raise RuntimeError(
            "ORW_SECRET_MASTER + ORW_SECRET_KDF_SALT env vars required. "
            "Generate with:\n"
            "  python -c \"import secrets; print('ORW_SECRET_MASTER=' + "
            "secrets.token_urlsafe(48)); print('ORW_SECRET_KDF_SALT=' + "
            "secrets.token_urlsafe(16))\""
        )
    salt = base64.urlsafe_b64decode(salt_b64 + "=" * (-len(salt_b64) % 4))
    return hash_secret_raw(
        secret=master,
        salt=salt,
        time_cost=_KDF_TIME_COST,
        memory_cost=_KDF_MEMORY_COST_KB,
        parallelism=_KDF_PARALLELISM,
        hash_len=_KEY_LEN,
        type=Argon2Type.ID,
    )


class _Vault:
    def __init__(self):
        self._aead = AESGCM(_derive_key())

    def encrypt(self, plaintext: str | None) -> str | None:
        if plaintext is None:
            return None
        nonce = os.urandom(_NONCE_LEN)
        ct = self._aead.encrypt(nonce, plaintext.encode("utf-8"), None)
        # ct = ciphertext + 16-byte tag (AESGCM packs them together)
        blob = bytes([_VERSION]) + nonce + ct
        return base64.urlsafe_b64encode(blob).decode("ascii")

    def decrypt(self, ciphertext: str | None) -> str | None:
        if ciphertext is None:
            return None
        try:
            blob = base64.urlsafe_b64decode(
                ciphertext + "=" * (-len(ciphertext) % 4)
            )
        except Exception:
            # Not valid base64 → assume legacy plaintext row, return as-is.
            # Remove this branch after migration + verification window.
            return ciphertext

        if len(blob) < 1 + _NONCE_LEN + 16 or blob[0] != _VERSION:
            # Wrong version or too short → assume legacy plaintext.
            return ciphertext

        nonce = blob[1 : 1 + _NONCE_LEN]
        ct = blob[1 + _NONCE_LEN :]
        try:
            return self._aead.decrypt(nonce, ct, None).decode("utf-8")
        except InvalidTag:
            # Tag mismatch = ciphertext was tampered, OR wrong key.
            # Loud failure is correct here — don't silently fall back.
            raise


_vault = _Vault()
encrypt_secret = _vault.encrypt
decrypt_secret = _vault.decrypt
```

### 4.2 Add `argon2-cffi` to `services/gateway/requirements.txt`

```
argon2-cffi==23.1.0   # for Argon2id KDF in utils.secrets
```

`cryptography` is already a dep (used for X.509 cert parsing).

### 4.3 Repository update (LDAP example)

```python
# services/gateway/features/ldap_servers/repository.py
from utils.secrets import encrypt_secret, decrypt_secret

async def create_ldap_server(db, *, bind_password: str, ...):
    server = LdapServer(
        bind_password_encrypted=encrypt_secret(bind_password),
        ...
    )
    db.add(server)

async def get_for_freeradius_use(db, server_id):
    server = await db.get(LdapServer, server_id)
    return {
        ...,
        "bind_password": decrypt_secret(server.bind_password_encrypted),
    }
```

### 4.4 One-shot migration script

```python
# scripts/migrate_secrets_to_encrypted.py
"""
Run ONCE after deploying the encryption layer to convert existing
plaintext rows to ciphertext. Idempotent — re-running is safe.

How "is this row already encrypted?" works:
- New ciphertext is base64 starting with version byte 0x01
- Old plaintext is whatever the user originally typed (rarely valid b64
  with the right shape)
- decrypt_secret() returns the input unchanged if it can't recognise it
  as a valid ciphertext, so we can compare input vs output to detect
  "already migrated" rows
"""
import asyncio
from sqlalchemy import select
from orw_common.database import async_session
from orw_common.models import LdapServer, RadiusNasClient, ...
from utils.secrets import encrypt_secret, decrypt_secret

TABLES = [
    (LdapServer, "bind_password_encrypted"),
    (RadiusNasClient, "secret_encrypted"),
    # ... etc
]

async def migrate_one(model, column):
    async with async_session() as db:
        rows = (await db.execute(select(model))).scalars().all()
        migrated = skipped = 0
        for row in rows:
            current = getattr(row, column)
            if current is None:
                skipped += 1
                continue
            # If decrypt returns the same string back, it wasn't ciphertext
            # (the helper falls back to passthrough on unrecognised input).
            roundtrip = decrypt_secret(current)
            if roundtrip != current:
                # Already valid ciphertext — skip
                skipped += 1
                continue
            setattr(row, column, encrypt_secret(current))
            migrated += 1
        await db.commit()
        print(f"  {model.__name__}.{column}: {migrated} migrated, {skipped} already encrypted/null")

async def main():
    for model, column in TABLES:
        print(f"Migrating {model.__name__}.{column}...")
        await migrate_one(model, column)

if __name__ == "__main__":
    asyncio.run(main())
```

### 4.5 Generating the master secret + salt

Run **once per deployment** and store in `.env.production`:

```bash
python3 -c "
import secrets
print('ORW_SECRET_MASTER=' + secrets.token_urlsafe(48))
print('ORW_SECRET_KDF_SALT=' + secrets.token_urlsafe(16))
"
```

Append both lines to `.env.production`. Treat them like `DB_PASSWORD`:
- `chmod 600 .env.production`
- Never commit to git
- Back up to a secure offline location (escrow) — losing both means **all
  encrypted columns become unreadable forever**, no recovery possible

---

## 5. Acceptance criteria for Phase 1 PR

- [ ] `services/gateway/utils/secrets.py` with AES-256-GCM encrypt/decrypt + Argon2id KDF
- [ ] `argon2-cffi` added to `services/gateway/requirements.txt`
- [ ] `ORW_SECRET_MASTER` + `ORW_SECRET_KDF_SALT` added to `.env.example` with generation instructions (`python -c "import secrets; ..."`)
- [ ] Repository layer for all 6 affected tables uses encrypt/decrypt at boundary
- [ ] Migration script runnable safely on existing production DB (idempotent — re-running detects already-encrypted rows and skips)
- [ ] Unit tests:
  - encrypt → decrypt round-trip
  - tamper test (modify ciphertext byte → `InvalidTag` raises, not silent passthrough)
  - missing env vars raise clear error at module load
- [ ] Integration test: create LDAP server via API → verify gateway can still bind to AD
- [ ] DB connection failure no longer logs full URL with password
- [ ] Documentation: ops runbook for the key escrow process and key rotation
- [ ] CHANGELOG / release notes entry calling out the breaking-change requirement: `.env.production` MUST have both new env vars before deploy

---

## 6. Open questions

1. Where does `.env.production` live in production today and who has shell access?
2. Are pg_dump backups encrypted in transit or at rest?
3. Is there a key escrow process (what happens if the team loses `ORW_SECRET_KEY`)?
4. Compliance scope — any SOC2 / ISO 27001 / GDPR commitments that drive timeline?

These shape whether Phase 1 is sufficient or we need to fast-track to Vault.
