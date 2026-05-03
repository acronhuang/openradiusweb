# Post-Deploy Verification Runbook

Run this after every production deploy that touches `services/auth/`,
`shared/orw_common/`, `docker-compose.yml`, or `.env.production`.

The full pass takes ~5 minutes. Stop and rollback at the first failure
unless you can explain it.

> Why this exists: PRs #65–#76 deployed cleanly to CI but broke prod.
> The recurring shape: a container kept stale state across the upgrade
> (cached site config, missing env var, watcher overwrote the fix).
> These checks catch each known shape in turn.

---

## 0. Before you start

```bash
cd /opt/openradiusweb
git log -1 --pretty=format:'%h %s'           # what is currently checked out
docker compose -f docker-compose.prod.yml ps  # which containers are up
```

If `docker ps` shows containers older than the PR you just merged → they
weren't recreated. Fix with `--force-recreate --no-deps <service>` per
container, NOT `up --force-recreate` on its own (cascades into postgres
and tries to re-bind missing volumes — see Fix 6 in
`session-2026-05-02-production-debug-and-mab.md`).

---

## 1. Container freshness — every relevant service was actually recreated

```bash
docker inspect --format '{{.Name}} created={{.Created}}' \
  $(docker compose -f docker-compose.prod.yml ps -q)
```

Each container's `created` timestamp must be **after** the deploy
started. A container with an older timestamp is running the previous
build — it will pass smoke tests until something asks it to do new
work, then fail in production.

---

## 2. FreeRADIUS site config has the `orw` python module wired

```bash
docker exec orw-freeradius grep -c 'orw' \
  /etc/freeradius/3.0/sites-enabled/default
```

Expect **3** (one each in `authorize`, `authenticate`, `post-auth`).
If 0 → the watcher regenerated the site without rlm_python3 detected.
Check `ORW_HAS_PYTHON3=true` is set on both `freeradius` AND
`freeradius_config_watcher` in `docker-compose.yml` — the watcher is
the one that *writes* the file (see PR #76).

```bash
docker exec orw-freeradius-config-watcher env | grep ORW_HAS_PYTHON3
docker exec orw-freeradius env | grep ORW_HAS_PYTHON3
```

Both must print `ORW_HAS_PYTHON3=true`.

---

## 3. Watcher reload path uses Docker SDK (no exit=127)

```bash
docker logs orw-freeradius-config-watcher --tail 200 \
  | grep -E 'reload|SIGHUP|exit=127'
```

You want to see successful reload entries, not `exit=127` (which means
the `kill` binary is missing from the freeradius image — PR #78
switched to the Python docker SDK to avoid this). If you see `exit=127`,
the watcher is running an older build; recreate it.

## 3b. Watcher reconcile cadence is correct (~300s, not ~1s)

```bash
# After watcher has been running for >5 min:
docker logs --timestamps orw-freeradius-config-watcher --tail 100 2>&1 \
  | grep 'Periodic reconciliation'
```

Consecutive `Periodic reconciliation` entries must be ~300 seconds
apart (the value of `RECONCILE_INTERVAL`). If they're seconds apart,
PR #87's `next_msg(timeout=...)` fix isn't deployed — the watcher is
busy-spinning. Symptom downstream: freeradius spam-logs `Received HUP
signal` every few seconds.

```bash
# Steady-state HUP rate sanity check — over a 5-minute window
# without any UI mutations, expect 0 HUPs.
#   Pre-PR-#87:        ~hundreds (every-second SIGHUP storm)
#   PR #87 + #88:       1 (one per periodic reconcile)
#   PR #90 (current):   0 (cert files + inline-built configs now
#                          properly hash-skipped)
docker logs --since 5m orw-freeradius 2>&1 | grep -c 'Received HUP signal'
```

If the count is non-zero in steady state (no UI mutations in the
window), the watcher is rendering something whose hash differs from
the stored hash. The first place to look is
`services/auth/freeradius_config_manager.py` — any generator that
embeds a timestamp / random / unsorted-iteration in its output
defeats the idempotency guard. The unit tests under
`services/auth/tests/unit/test_inline_render_determinism.py` and
`test_template_determinism.py` are the pattern to follow when adding
a new generator.

---

## 4. Encryption env vars are present on every secrets-handling service

```bash
for svc in gateway freeradius freeradius_config_watcher switch_mgmt coa_service; do
  echo "=== $svc ==="
  docker exec "orw-${svc//_/-}" env 2>/dev/null \
    | grep -E '^ORW_SECRET_(MASTER|KDF_SALT)=' | wc -l
done
```

Each line must print **2**. Missing on any service means encrypted
columns will throw at first read — gateway will 500, freeradius will
reject auth.

---

## 5. DB columns actually contain ciphertext, not plaintext

Run from the postgres container directly (avoids needing `DB_PASSWORD`
in env — postgres trusts local socket connections). One query covering
all 6 columns; output is `column|total|nonnull|bad` per row, where
`bad` counts non-null/non-empty values that don't have the
ciphertext shape (length ≥ 28 chars + base64 prefix `A` = version
byte 0x01).

```bash
docker exec orw-postgres psql -U orw -d orw -A -t -c "
SELECT 'ldap_servers.bind_password_encrypted' AS col,
       COUNT(*) AS total,
       COUNT(bind_password_encrypted) AS nonnull,
       COUNT(*) FILTER (WHERE bind_password_encrypted IS NOT NULL
                          AND bind_password_encrypted <> ''
                          AND (LENGTH(bind_password_encrypted) < 28
                               OR LEFT(bind_password_encrypted,1) <> 'A')) AS bad
FROM ldap_servers
UNION ALL
SELECT 'radius_nas_clients.secret_encrypted',
       COUNT(*), COUNT(secret_encrypted),
       COUNT(*) FILTER (WHERE secret_encrypted IS NOT NULL
                          AND secret_encrypted <> ''
                          AND (LENGTH(secret_encrypted) < 28
                               OR LEFT(secret_encrypted,1) <> 'A'))
FROM radius_nas_clients
UNION ALL
SELECT 'certificates.key_pem_encrypted',
       COUNT(*), COUNT(key_pem_encrypted),
       COUNT(*) FILTER (WHERE key_pem_encrypted IS NOT NULL
                          AND key_pem_encrypted <> ''
                          AND (LENGTH(key_pem_encrypted) < 28
                               OR LEFT(key_pem_encrypted,1) <> 'A'))
FROM certificates
UNION ALL
SELECT 'radius_realms.proxy_secret_encrypted',
       COUNT(*), COUNT(proxy_secret_encrypted),
       COUNT(*) FILTER (WHERE proxy_secret_encrypted IS NOT NULL
                          AND proxy_secret_encrypted <> ''
                          AND (LENGTH(proxy_secret_encrypted) < 28
                               OR LEFT(proxy_secret_encrypted,1) <> 'A'))
FROM radius_realms
UNION ALL
SELECT 'network_devices.snmp_community_encrypted',
       COUNT(*), COUNT(snmp_community_encrypted),
       COUNT(*) FILTER (WHERE snmp_community_encrypted IS NOT NULL
                          AND snmp_community_encrypted <> ''
                          AND (LENGTH(snmp_community_encrypted) < 28
                               OR LEFT(snmp_community_encrypted,1) <> 'A'))
FROM network_devices
UNION ALL
SELECT 'network_devices.coa_secret_encrypted',
       COUNT(*), COUNT(coa_secret_encrypted),
       COUNT(*) FILTER (WHERE coa_secret_encrypted IS NOT NULL
                          AND coa_secret_encrypted <> ''
                          AND (LENGTH(coa_secret_encrypted) < 28
                               OR LEFT(coa_secret_encrypted,1) <> 'A'))
FROM network_devices;
"
```

Every row's `bad` count must be `0`. Any non-zero means a row contains
plaintext (or wrong-version ciphertext) — the migration script wasn't
run, or it ran with the wrong key, or someone bypassed `encrypt_secret()`
when writing. With strict-mode `decrypt_secret()` (PR #83), gateway /
freeradius will throw `ValueError` on first read of a bad row.

Check `docs/session-2026-05-03-encryption-rollout.md` for the rollback
procedure if recovery is needed.

> Earlier versions of this runbook used `ca_certificates` and
> `server_certificates` as table names. Those don't exist — the schema
> uses one `certificates` table with a `cert_type` discriminator
> (`'ca'` or `'server'`). Verify with
> `\d` in psql or `\dt` to list tables if you're unsure.

---

## 6. End-to-end auth — phone reconnect

These can't be automated; do them by hand. Both should succeed within
~10 seconds.

1. **MDS-01 (TTLS+PAP via LDAP)** — forget + reconnect from a phone.
   Tail freeradius logs:
   ```bash
   docker logs -f orw-freeradius 2>&1 | grep -i 'login\|reject'
   ```
   Expect `Login OK: [<user>@mds.local]`.
2. **MAB_Auth (open SSID, MAC allowlist)** — reconnect a whitelisted
   device. Same tail; expect `Login OK: [<mac>]` via the `mab` virtual
   server.

If MDS-01 logs `Login OK` but the phone says "Authenticating" forever,
the issue is supplicant-side (likely cached profile with wrong
EAP method) — see `employee-wifi-setup-guide.md`.

---

## 7. Backup script still works against the new key

Only after a key rotation. Skip otherwise.

```bash
sudo ./scripts/backup.sh
ls -la backups/ | tail -3
```

Output should be `*.tar.gz.gpg` (encrypted). A `*.tar.gz` (plaintext)
file means `ORW_BACKUP_PASSPHRASE` is unset in `.env.production` —
critical, fix before next backup window.

---

## What to do when one of these fails

- **Steps 1–4 fail** → recreate the affected service:
  ```bash
  docker compose -f docker-compose.prod.yml up -d --force-recreate --no-deps <service>
  ```
  Re-run the failing step.
- **Step 5 fails** → DO NOT roll forward. Capture the bad ciphertext
  shape, restore from the most recent good backup
  (`./scripts/restore.sh backups/<latest>.tar.gz.gpg`), then
  investigate which env var or migration script wrote the wrong value.
- **Step 6 fails but 1–5 pass** → check
  `docs/troubleshooting-8021x-ad.md`. Most likely a downstream
  config issue, not the deploy.
