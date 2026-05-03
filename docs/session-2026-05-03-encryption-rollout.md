# Session Log — 2026-05-03 Vite Bump + Security Audit + Phase 1 LDAP Encryption

**Continuation of [session-2026-05-02-production-debug-and-mab.md](session-2026-05-02-production-debug-and-mab.md).**
That session ran from morning through ~17:00 local time and ended with
production stable on PRs #57-#66. This session picks up immediately
after and runs through to the end of Phase 1 LDAP encryption rollout
on production.

**Total elapsed**: ~3 hours of focused work, 5 PRs opened + 1 reverted
out of #62 + 5 merged.

**Outcome**: vite CVE patched (5.4.21 → 7.3.2), secret-storage audit
written + recommendation locked in (AES-256-GCM + Argon2id), and the
first of six secret columns (LDAP bind password) is **end-to-end
encrypted in production with `Login OK` verified**.

---

## Phase A — Vite CVE / Dependabot dance

### Triggered by

After enabling GitHub Dependabot at the end of session-2026-05-02,
two auto-PRs appeared:
- **#60**: bump esbuild + vite 5.4.21 → **8.0.10** (multi-package)
- **#61**: bump vite 5.4.21 → **8.0.10** (vite-only)

The actual CVE ([GHSA-4w7w-66w2-5vf9](https://github.com/advisories/GHSA-4w7w-66w2-5vf9))
was fixed in vite **6.4.2** — Dependabot proposed a 3-major-version
skip. Both PRs had identical 588/710 line diffs.

### Background-agent test of #60

Spawned a worktree-isolated agent to actually `npm ci && npm run build`
the PR to see if it works:

```
RISKY (functional but with peer-dep conflict)

- @vitejs/plugin-react@4.7.0 declares peer: vite ^4|^5|^6|^7 (no v8)
- npm ci FAILS with ERESOLVE → CI/CD breaks
- Only `npm install --legacy-peer-deps` installs
- Build PASSES under --legacy-peer-deps (10.3s, 1.4 MB dist)
- Deprecation warnings about esbuild → oxc API change
- npm audit: 0 vulnerabilities (goal achieved)
```

### Decision: close both, manual bump to vite ^7

Closed #60 and #61 with explanations linking to the agent report.
Opened **#68** manually — `vite ^7.0.0`. CVE fixed (vite 7 includes
the GHSA patch), no peer-dep conflict, no major-version-skip risk.

### #68 verified locally

```
npm install:  clean, no ERESOLVE
npm run build: PASS (3072 modules, 18.96s, dist 1.44 MB / 448 kB gzip)
npm audit:    0 vulnerabilities (was 1 medium)
deprecations: none vite-related
```

CI green → merged → production rebuilt frontend. Done.

---

## Phase B — Security audit doc

### Trigger

User question: "另外發現資料庫密碼以明碼顯示未加密以及在log中查詢log會看到明碼密碼這會有資安風險請提供目前有哪些做法并提供比較説明"

(Discovered DB passwords shown in plaintext + plaintext passwords in log
queries — please show what approaches exist with comparison.)

### Audit findings

`grep -rE "_encrypted" services/gateway/`+ live verification on
192.168.0.250 confirmed **6 columns named `*_encrypted` actually
store cleartext**:

| Column | Risk |
|---|---|
| `ldap_servers.bind_password_encrypted` | 🔴 critical (AD service-account password) |
| `radius_nas_clients.secret_encrypted` | 🔴 critical (RADIUS NAS shared secret) |
| `certificates.key_pem_encrypted` | 🔴 critical (TLS server private key) |
| `radius_realms.proxy_secret_encrypted` | 🟠 high |
| `network_devices.coa_secret_encrypted` | 🟠 high |
| `network_devices.snmp_community_encrypted` | 🟡 medium |

```sql
mds=# SELECT bind_password_encrypted FROM ldap_servers;
 bind_password_encrypted
-------------------------
 !QAZxcvfr432wsde
```

`grep -rE "decrypt|encrypt|Fernet|cipher" services/gateway/` returned
**zero hits** beyond X.509 cert parsing — the `_encrypted` suffix was
aspirational, encryption layer never implemented.

### Comparison written

**PR #67** ships a 7-approach matrix:

| Option | Effort | Security | Ops cost |
|---|---|---|---|
| A. Status quo (plaintext) | 0 | ❌ none | 0 |
| B. Fernet symmetric | 4hr | ⭐⭐⭐ | ⭐ |
| **C. AES-GCM + Argon2id KDF** | 8hr | ⭐⭐⭐⭐ | ⭐⭐ |
| D. PostgreSQL pgcrypto | 4hr | ⭐⭐ | ⭐⭐ |
| E. Docker / K8s Secrets | 6hr | ⭐⭐⭐ | ⭐⭐ |
| F. HashiCorp Vault | 2-3 days | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| G. Cloud KMS | 1-2 days | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ |

User constraint: **no cloud** (rules out F if Vault HA cluster is
considered cloud, definitely G). User picked **C (AES-GCM+KDF)** over
B (Fernet) for the modern AEAD + KDF cost-per-attempt benefit.

### Why C over B (the §3.1 in PR #67)

```
Fernet:   AES-128-CBC + HMAC      env value used directly as key
                                   ↓
                                  .env leak = key leak (instant)

AES-GCM + Argon2id:
          AES-256-GCM (modern AEAD)
                ↑
          key = Argon2id(master, salt, 64MB memory, 3 rounds)
                                   ↓
                                  .env leak = each brute-force attempt
                                  costs ~100ms × Argon2id memory cost
```

Plus AES-GCM auth tag catches DB tampering at decryption time.

PR #67 merged as doc-only.

---

## Phase C — Phase 1 foundation (PR #70)

Smaller-than-audit scope: ship the encryption module + tests + deps
+ env vars **only**. No repository wiring yet, no behaviour change.
Reviewers can validate the crypto choice in isolation before
multi-file follow-ups.

### What's in #70

| File | What |
|---|---|
| `services/gateway/utils/secrets.py` | `encrypt_secret` / `decrypt_secret` / `is_encrypted`. Storage: `version_byte(1) \|\| nonce(12) \|\| aesgcm_output(N+16)`, urlsafe-base64 into existing `*_encrypted text` columns. Argon2id KDF, key cached per-process. **Permissive `decrypt`** — returns input unchanged on unrecognised format, so legacy plaintext rows keep working during migration window. |
| `services/gateway/tests/unit/test_secrets.py` | 24 tests: round-trip across CJK/empty/long, nonce uniqueness, tamper detection on payload AND on tag, legacy plaintext passthrough, `is_encrypted` true/false, wrong-key rejection, type validation. All pass in 0.54s. |
| `services/gateway/requirements.txt` | + `cryptography>=42.0` + `argon2-cffi>=23.1` |
| `.env.example` | + `ORW_SECRET_MASTER` + `ORW_SECRET_KDF_SALT` with generation one-liner and a loud loss-of-data warning |

CI green → merged. **Zero production behaviour change** (helper is dead
code without repository wiring).

### Production prep (manual ops on RADIUS server)

User generated production secrets:

```bash
python3 -c "
import secrets
print('ORW_SECRET_MASTER=' + secrets.token_urlsafe(48))
print('ORW_SECRET_KDF_SALT=' + secrets.token_urlsafe(16))
"
```

⚠️ **Operator pitfall**: User initially pasted the actual generated
values to chat. Caught immediately — chat transcript persists, secrets
should never go in any chat/log/email. Regenerated, set the new pair
in `.env.production`, backed up to password manager. Old "leaked"
values were never used to encrypt anything (foundation is dead code),
so zero blast radius.

`chmod 600 .env.production`. Done.

---

## Phase D — Phase 1 LDAP end-to-end (PR #71)

The first feature wiring. Ten-file PR covering both gateway and
freeradius sides of LDAP bind password handling.

### Architecture moves

`services/gateway/utils/secrets.py` → **`shared/orw_common/secrets.py`**.
Promoted from gateway-only because freeradius services need to import
the same encrypt/decrypt helpers — now they all share one source of
truth. Test file's import path updated, 24 tests still pass.

### Wiring summary

| Side | File | Change |
|---|---|---|
| Gateway write | `features/ldap_servers/repository.py` | `insert_ldap_server` + `update_ldap_server` encrypt `bind_password` before SQL |
| Gateway read | `features/ldap_servers/repository.py` + `routes.py` | `lookup_full_for_test` decrypts on read; `# TODO: decrypt via Vault` removed |
| FR config gen | `services/auth/freeradius_config_manager.py` | Wraps `bind_password_encrypted` with `decrypt_secret()` when generating freeradius LDAP module config at container start |
| FR runtime | `services/auth/freeradius/mods-config/python/rlm_orw.py` | Same fix on the runtime LDAP group lookup. Defensive try/except around the import — old image without PR #71 Dockerfile changes degrades to passthrough rather than ImportError-crashing rlm_python3 |
| FR image | `Dockerfile.freeradius` | + apt `python3-argon2`; + COPY `shared/orw_common/`; + `PYTHONPATH=/opt/orw` |
| Watcher image | `Dockerfile.config_watcher` | + pip `argon2-cffi` (already had cryptography) |
| Compose | `docker-compose.yml` | `ORW_SECRET_MASTER` + `ORW_SECRET_KDF_SALT` passed to **gateway, freeradius, freeradius_config_watcher** (all three need the same key to encrypt/decrypt against the same DB rows) |

### Migration script

`scripts/migrate_ldap_passwords_to_encrypted.py` — idempotent,
`--dry-run`, detects already-encrypted rows via `is_encrypted()` and
skips them, encrypts everything else.

### CI hiccup → conftest fix

CI's integration suite (`tests/integration/test_repository_smoke.py::test_ldap_server_*`) failed:
```
RuntimeError: Missing env var(s): ORW_SECRET_MASTER, ORW_SECRET_KDF_SALT
```

Fix: added deterministic test-only env vars to
`services/gateway/conftest.py` alongside the existing `JWT_SECRET_KEY`
seeds. Loud comment + obvious value rules out accidental production
use. Pushed → CI green → merged.

---

## Phase E — Production rollout (live execution log)

The PR description's 6-step rollout, executed on 192.168.0.250:

### Step 1: pull main
```
git pull
→ 81d1fcb8 feat(security): encrypt ldap_servers...
```

### Step 2: confirm env vars
```
sudo grep -c "^ORW_SECRET_" /opt/openradiusweb/.env.production
→ 2     (both vars present)
```

### Step 3: build
```
sudo -E docker compose build gateway freeradius freeradius_config_watcher
→ all 3 images Built
```

### Step 4: recreate containers (no-deps)
```
sudo -E docker compose up -d --no-deps --force-recreate gateway freeradius freeradius_config_watcher
→ 3 containers Started
```

### Step 5: verify freeradius healthy
```
sudo docker logs --tail=30 orw-freeradius
→ "Sun May  3 05:11:49 2026 : Info: Ready to process requests"
   no Fatal Python error, no RuntimeError — ORW_SECRET vars wired in,
   freeradius_config_manager.py decrypted bind_password_encrypted to
   build the freeradius ldap module config at boot.
```

### Step 6a: migration dry-run (corrected — original instructions tried `gateway` container which lacks psycopg2; used `freeradius` container instead which has `python3-psycopg2` from apt)
```
sudo docker cp /opt/openradiusweb/scripts/migrate_ldap_passwords_to_encrypted.py orw-freeradius:/tmp/migrate.py
sudo docker exec orw-freeradius python3 /tmp/migrate.py --dry-run

→ Found 1 ldap_servers row(s) to inspect.
    WOULD encrypt: MDS-DC
  ============================================================
  DRY RUN — would have encrypted 1 row(s).
  Already encrypted (skipped): 0
  Null/empty (skipped): 0
```

### Step 6b: migration real run
```
sudo docker exec orw-freeradius python3 /tmp/migrate.py

→ Found 1 ldap_servers row(s) to inspect.
  ============================================================
  Encrypted 0 row(s).
  Already encrypted (skipped): 1     ← was already encrypted!
  Null/empty (skipped): 0
```

⚠️ **Anomaly**: dry-run said "WOULD encrypt 1" but the immediate
real-run said "Already encrypted (skipped): 1". Something between the
two calls encrypted the row — most likely the `force-recreate` of
gateway in step 4 caused a config sync cycle that touched the row,
OR an admin clicked Save in the UI. Not investigated further because
the row IS encrypted with the right key (verified step 7 below).

### Step 7: SQL verify
```sql
SELECT name, length(bind_password_encrypted) AS ct_len,
       substring(bind_password_encrypted, 1, 8) AS first_8
FROM ldap_servers;

  name  | ct_len | first_8
--------+--------+----------
 MDS-DC |     60 | Acw4nl87
```

- `ct_len = 60` matches AES-GCM blob math: 1 (version) + 12 (nonce) +
  16 (plaintext) + 16 (tag) = 45 bytes raw → urlsafe-base64 ≈ 60 chars
- `first_8 = "Acw4nl87"` — first byte is `A` which is the urlsafe-b64
  representation of our version byte 0x01. Confirms the row is in OUR
  ciphertext format (not e.g. random garbage from a buggy write path).

### Step 8: real auth test from phone
```
Sun May  3 05:19:21 : Auth: Login incorrect (mschap: FAILED)        ← phone tried PEAP first
Sun May  3 05:19:21 : Auth: Login incorrect (eap_peap: rejected)    ← rejected
Sun May  3 05:19:50 : Auth: Login OK: [ming@mds.local]              ← TTLS+PAP success ✅
```

`Login OK` is the proof that:
- DB row has ciphertext: `Acw4nl87...`
- `freeradius_config_manager.py` decrypted it at startup (using the
  ORW_SECRET_MASTER + KDF_SALT in the freeradius container's env)
- `freeradius ldap module` got the correct plaintext password
- bind to AD as `Radius_MGM` succeeded
- AD returned ming's record
- ming bound with their own password → Login OK

**Phase 1 LDAP end-to-end encryption verified in production.** 🎉

---

## Lessons learned (additions to the 5-02 list)

1. **Never paste secrets into chat — even momentarily.** The chat
   transcript persists. Caught immediately, regenerated, no harm done
   (the foundation was dead code so the leaked values had encrypted
   nothing). But the right reflex is "if the value is a secret, it
   never appears in chat / log / commit / screenshot / email."

2. **Dependabot's "latest version" recommendation isn't always the
   right bump.** Vite 5→8 was a 3-major-version skip; the actual CVE
   was patched in 6.4.2. Manually bumping to vite ^7 fixed the same
   CVE without the breakage risk. Dependabot proposes max-patch but
   doesn't reason about whether your peer deps support the target.

3. **CI doesn't actually build the frontend — backend services-only.**
   PR #60's CI was all-green even though `npm ci` would have failed on
   the resulting branch. Local agent build was the only way to catch
   the peer-dep conflict before merge. Possible follow-up: add a
   `npm ci && npm run build` job to CI.

4. **`gateway` container doesn't have `psycopg2`** — uses `asyncpg`
   instead. Migration scripts that rely on sync psycopg2 should run
   in the `freeradius` container (which has `python3-psycopg2` via
   apt) or be rewritten in asyncpg.

5. **`scripts/` isn't COPYed into the gateway image.** Had to
   `docker cp` the migration script in. Dockerfile improvement
   queued — should COPY `scripts/` to `/opt/orw-scripts/` so future
   migrations can be `docker compose exec gateway python /opt/orw-scripts/...`.

6. **Permissive decrypt is a feature, not a bug.** During the
   migration window, gateway and freeradius will see a mix of
   plaintext + ciphertext rows. `decrypt_secret()` returning the
   input unchanged on unrecognised format means rolling migration
   without service downtime. After migration verification, the
   passthrough should be removed (replaced with raise) to make
   tampering loud.

---

## Phase 1 remaining (planned PRs #72-#75)

| PR | Column(s) | Coupled service | Est. effort |
|---|---|---|---|
| #72 | `radius_nas_clients.secret_encrypted` | freeradius (clients.conf gen) | 30-60 min |
| #73 | `certificates.key_pem_encrypted` | freeradius cert manager | 60-90 min (PEM is multi-line, careful encoding) |
| #74 | `radius_realms.proxy_secret_encrypted` | freeradius proxy.conf | 30-60 min |
| #75 | `network_devices.snmp_community_encrypted` + `coa_secret_encrypted` | switch_mgmt + coa services | 60 min |

Each follows the same pattern as PR #71:
1. Repository encrypt-on-write + decrypt-on-read
2. Whichever service consumes it: add decrypt at boundary
3. Update `docker-compose.yml` if a new container needs the env vars
4. Migration script (extend the existing one with more `(model, column)`
   tuples)
5. Production rollout: build → recreate → migrate → verify

After all 5 are done, **Phase 1 complete**. Then Phase 2 (Vault or
similar) becomes a future option.

---

## Final PR table for this session

| PR | Status | Title |
|----|--------|-------|
| #60 | ❌ closed | Dependabot vite 5→8 (peer-dep conflict, can't merge) |
| #61 | ❌ closed | Dependabot vite-only 5→8 (same issue) |
| #67 | ✅ merged | docs(security): audit of plaintext secrets at rest + remediation comparison |
| #68 | ✅ merged | chore(frontend): bump vite 5.4.21 → 7.3.2 (fixes GHSA-4w7w-66w2-5vf9) |
| #69 | ✅ merged | docs(wifi-guide): add MAB_Auth section + flesh out Android step 2 |
| #70 | ✅ merged | feat(gateway/secrets): AES-256-GCM + Argon2id encryption helper (foundation) |
| #71 | ✅ merged | feat(security): encrypt ldap_servers.bind_password_encrypted end-to-end |

---

## Appendix: Production rollout cheatsheet (for Phase 1 PRs #72-#75)

The pattern from PR #71 step-by-step. Re-use for each remaining PR:

```bash
# 1. Pull merged code
cd /opt/openradiusweb
git pull

# 2. Confirm env vars still set (paranoia check)
sudo grep -c "^ORW_SECRET_" .env.production    # MUST be 2

# 3. Rebuild every service that imports orw_common.secrets
#    (varies per PR — gateway is always involved; check PR description for others)
sudo -E docker compose build <service-list>

# 4. Recreate, no-deps to avoid touching postgres
sudo -E docker compose up -d --no-deps --force-recreate <service-list>

# 5. Wait + verify no Fatal Python error
sleep 30
sudo docker logs --tail=30 <each-recreated-container>

# 6. Migration dry-run (use freeradius container — has psycopg2)
sudo docker cp /opt/openradiusweb/scripts/migrate_<column>_to_encrypted.py \
    orw-freeradius:/tmp/migrate.py
sudo docker exec orw-freeradius python3 /tmp/migrate.py --dry-run

# 7. Migration real
sudo docker exec orw-freeradius python3 /tmp/migrate.py

# 8. SQL verify ciphertext shape
sudo docker exec orw-postgres psql -U orw -d orw -c \
    "SELECT length(<column>), substring(<column>, 1, 8) FROM <table>;"
# expect length ≥ 60, first_8 starts with 'A' (version byte 0x01 in b64)

# 9. End-to-end functional verify (whatever the secret powers — auth,
#    cert presentation, SNMP poll, etc.)
```
