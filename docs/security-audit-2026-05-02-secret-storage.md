# Security Audit — Secret Storage at Rest (2026-05-02)

**Scope**: How OpenRadiusWeb stores credentials in PostgreSQL and how those
credentials surface in logs.

**Result**: 🔴 **6 columns store plaintext under names ending in `_encrypted`**.
The naming is aspirational — no encryption layer exists in the codebase.
This is misleading to future maintainers and DB admins, and any DB compromise
exposes every backend secret.

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

**Phase 1 (this sprint, ~1 day work)**:
- ✅ Implement option **B (Fernet symmetric)**
- Master key generated by `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`, stored in `.env.production` next to `DB_PASSWORD`
- Migrate all 6 columns: write encryption helpers, update repository layer, write a one-shot migration script that reads existing plaintext + writes ciphertext
- Rename columns from `_encrypted` to keep current name (the suffix becomes accurate for the first time)
- Tests: round-trip encrypt/decrypt unit test, integration test confirming gateway can still bind LDAP after migration

**Phase 2 (next quarter, when team capacity allows)**:
- Implement option **F (Vault)** if growing past 10 customers / SOC2 requirement
- Or option **G (Cloud KMS)** if deploying on AWS/GCP/Azure

**Don't do Phase 1 before**:
- ✅ DB-level backup encryption (current pg_dump is plaintext too — same problem one layer up)
- ⚠️ `.env.production` access controls (currently anyone with shell on the RADIUS server can `cat` it)

### 3.1 What to also fix in parallel

- **DB connection error log**: wrap `psycopg2.connect()` in try/except that masks the password before logging
- **LDAP debug logs**: confirm `rlm_ldap` is not at debug level in production
- **freeradius `radiusd.conf`**: verify `auth_goodpass = no` after every deploy (PR #58 baked this in)
- **`.env.production` permissions**: `chmod 600 .env.production` and `chown radius-app:radius-app .env.production`

---

## 4. Sample implementation skeleton (Option B)

### 4.1 New file `services/gateway/utils/secrets.py`

```python
"""
Symmetric encryption for secrets stored in DB.

Master key (base64-encoded 32 bytes) loaded once at process start from
ORW_SECRET_KEY env var. Generated via:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

The key MUST be stable across rolling deploys (rotating it requires a
one-shot re-encrypt of every secret column). Treat it like the DB
password — store in .env, mode 0600, never in git.
"""
import os
from cryptography.fernet import Fernet, InvalidToken


class _Vault:
    def __init__(self):
        key = os.environ.get("ORW_SECRET_KEY")
        if not key:
            raise RuntimeError(
                "ORW_SECRET_KEY env var not set. Generate with: "
                "python -c 'from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())'"
            )
        self._fernet = Fernet(key.encode())

    def encrypt(self, plaintext: str) -> str:
        if plaintext is None:
            return None
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        if ciphertext is None:
            return None
        try:
            return self._fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        except InvalidToken:
            # Migration path: old plaintext rows can't decrypt — return as-is
            # so app keeps working during migration window.
            # Remove this fallback after full migration + verification.
            return ciphertext


_vault = _Vault()
encrypt_secret = _vault.encrypt
decrypt_secret = _vault.decrypt
```

### 4.2 Repository update (LDAP example)

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

### 4.3 One-shot migration script

```python
# scripts/migrate_secrets_to_encrypted.py
"""
Run ONCE after deploying the encryption layer to convert existing
plaintext rows to ciphertext. Idempotent — re-running is safe.
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
        for row in rows:
            current = getattr(row, column)
            if current is None:
                continue
            # Try to decrypt — if it's already ciphertext, skip
            try:
                decrypt_secret(current)
                continue  # already encrypted
            except Exception:
                pass
            # It's plaintext — encrypt it
            setattr(row, column, encrypt_secret(current))
        await db.commit()

async def main():
    for model, column in TABLES:
        print(f"Migrating {model.__name__}.{column}...")
        await migrate_one(model, column)

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 5. Acceptance criteria for Phase 1 PR

- [ ] `services/gateway/utils/secrets.py` with encrypt/decrypt + master key from env
- [ ] `ORW_SECRET_KEY` added to `.env.example` with generation instructions
- [ ] Repository layer for all 6 affected tables uses encrypt/decrypt at boundary
- [ ] Migration script runnable safely on existing production DB
- [ ] Unit tests for encrypt → decrypt round-trip
- [ ] Integration test: create LDAP server via API → verify gateway can still bind
- [ ] DB connection failure no longer logs full URL with password
- [ ] Documentation: ops runbook for key rotation
- [ ] CHANGELOG / release notes entry

---

## 6. Open questions

1. Where does `.env.production` live in production today and who has shell access?
2. Are pg_dump backups encrypted in transit or at rest?
3. Is there a key escrow process (what happens if the team loses `ORW_SECRET_KEY`)?
4. Compliance scope — any SOC2 / ISO 27001 / GDPR commitments that drive timeline?

These shape whether Phase 1 is sufficient or we need to fast-track to Vault.
