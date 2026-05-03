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

```bash
docker exec orw-freeradius python3 -c '
import os, psycopg2
conn = psycopg2.connect(
    host="postgres", dbname="orw", user="orw",
    password=os.environ["DB_PASSWORD"])
cur = conn.cursor()
checks = [
    ("ldap_servers",    "bind_password_encrypted"),
    ("radius_nas_clients", "secret_encrypted"),
    ("ca_certificates", "key_pem_encrypted"),
    ("server_certificates", "key_pem_encrypted"),
    ("radius_realms",   "proxy_secret_encrypted"),
    ("network_devices", "snmp_community_encrypted"),
]
for tbl, col in checks:
    cur.execute(f"SELECT {col} FROM {tbl} WHERE {col} IS NOT NULL LIMIT 1")
    row = cur.fetchone()
    if not row:
        print(f"{tbl}.{col}: (no rows)")
        continue
    val = row[0]
    # Ciphertext is base64 of (1 version byte || 12 nonce || ciphertext).
    # Min 29 chars after base64; current schema starts with "A" (version 0x01).
    ok = len(val) >= 28 and val[0] == "A"
    print(f"{tbl}.{col}: len={len(val)} prefix={val[:4]!r} {'OK' if ok else \"FAIL — looks like plaintext\"}")
'
```

Any `FAIL — looks like plaintext` means the migration script wasn't
run, or it ran with the wrong key and you have unrecoverable data.
Check `docs/session-2026-05-03-encryption-rollout.md` for the rollback
procedure.

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
