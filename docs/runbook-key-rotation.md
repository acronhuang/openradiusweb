# Key Rotation Runbook — `ORW_SECRET_MASTER` + `ORW_SECRET_KDF_SALT`

How to swap the master secret + KDF salt that protect every encrypted DB
column (LDAP bind passwords, RADIUS shared secrets, TLS private keys,
SNMP community strings, CoA secrets — see PRs #70-#74).

> Why a runbook for this: the encryption key and the data are stored
> separately on purpose, but rotating means **decrypt every row with
> the old key, re-encrypt with the new key, swap env vars, restart**.
> Get the order wrong and either (a) services read with the wrong key
> and crash, or (b) you end up with mixed-key rows that nothing can
> decrypt cleanly.

Plan budget ~30 min — the hands-on work is ~5 min, the rest is
verification + escrow handoff.

---

## When to rotate

- **Suspected leak**: `.env.production` was readable to someone outside
  the trusted set (a compromised SSH key, a leaked backup that's not
  GPG-encrypted, an accidental git commit).
- **Personnel change**: an engineer with read access to
  `.env.production` left the company. Rotate within their notice
  period.
- **Scheduled hygiene**: yearly is reasonable for a small team. More
  frequent doesn't buy much without a Vault / KMS upgrade — the cost
  is the migration window, not the rotation itself.
- **Post-incident**: any time the master secret was typed in a chat
  window, pasted into a script that was then committed, or otherwise
  left a paper trail.

If the leak is confirmed (not suspected), prioritise: rotate FIRST,
then audit access logs, then post-mortem.

---

## What this rotation does NOT cover

- **`DB_PASSWORD`, `JWT_SECRET_KEY`, `REDIS_PASSWORD`** — different
  rotation flow each. The DB password rotation in particular needs a
  postgres `ALTER USER` + service restart and is its own runbook (TBD).
- **`ORW_BACKUP_PASSPHRASE`** — independent of `ORW_SECRET_MASTER`
  (PR #81). Only matters for restoring backups; rotate separately.
- **TLS server certificate / CA** — those are X.509 keys stored
  encrypted in the `certificates` table. Rotating
  `ORW_SECRET_MASTER` re-encrypts the cert key WITHOUT changing the
  cert itself, which is what we want. Rotating the cert is a separate
  operation.
- **Old GPG-encrypted backups** — they contain a copy of the old
  `.env.production` (with the old master). Restoring an old backup =
  reverting to the old key. That's actually OK as a last-resort
  recovery; just keep the old key in escrow forever.

---

## Pre-rotation checklist

1. **Recent backup available** — `./scripts/backup.sh` ran
   successfully in the last 24 h (encrypted `.tar.gz.gpg`). If not,
   run one now and verify it shows up in `backups/`.
2. **All rows currently ciphertext** — run step 5 of
   [runbook-post-deploy-verification.md](runbook-post-deploy-verification.md).
   Every column should show `bad=0`. Plaintext rows would silently fail
   rotation.
3. **You have BOTH key sets in front of you**:
   - **OLD** master + salt — copied from `.env.production` on the
     prod host (`grep '^ORW_SECRET_' /opt/openradiusweb/.env.production`).
   - **NEW** master + salt — generated fresh:
     ```bash
     python3 -c "import secrets; print('ORW_SECRET_MASTER=' + secrets.token_urlsafe(48)); print('ORW_SECRET_KDF_SALT=' + secrets.token_urlsafe(16))"
     ```
4. **Maintenance window booked** — RADIUS auth is unavailable for the
   ~30 sec it takes to stop, rotate, and restart the auth services.
   Outside business hours preferred. Mobile users will get
   "Authenticating…" until the window closes.

---

## Rotation procedure

### Step 1 — Stop the services that hold the encryption key

These are the 5 services that import `orw_common.secrets`. Stopping
them prevents mid-rotation reads against rows that have just been
re-encrypted but haven't had their cached key updated.

```bash
cd /opt/openradiusweb
sudo docker compose -f docker-compose.prod.yml --env-file .env.production stop \
    gateway freeradius freeradius_config_watcher switch_mgmt coa_service
```

`postgres`, `redis`, `nats`, `discovery`, `device_inventory`,
`policy_engine`, `frontend` keep running — they don't touch encrypted
columns directly.

### Step 2 — Run the rotation script

The script lives at [scripts/rotate_secret_master.py](../scripts/rotate_secret_master.py).
It walks the 6 encrypted columns, decrypts each row with the OLD key,
re-encrypts with the NEW key, writes back per-row inside a per-table
transaction.

```bash
# Run inside the postgres container so we have psycopg2 + the orw_common
# module path. (The freeradius image works too but it's stopped right now.)
sudo docker exec -i orw-postgres bash -c '
  ORW_SECRET_MASTER_OLD="<paste OLD master>" \
  ORW_SECRET_KDF_SALT_OLD="<paste OLD salt>" \
  ORW_SECRET_MASTER_NEW="<paste NEW master>" \
  ORW_SECRET_KDF_SALT_NEW="<paste NEW salt>" \
  DB_PASSWORD="<paste from .env.production>" \
  PYTHONPATH=/opt/orw \
  python3 /opt/orw/scripts/rotate_secret_master.py
'
```

> **`docker exec -i` + heredoc-style env injection**: keys never appear
> in your shell history file (only in the docker exec invocation, which
> doesn't get persisted by bash). Don't put keys in `--env-file` or as
> CLI args — they'd show in `ps`.

> **`/opt/orw/scripts/`**: the freeradius image bakes the scripts in
> there (PR #79); the postgres image doesn't. If running from postgres,
> `docker cp scripts/rotate_secret_master.py orw-postgres:/tmp/` first
> and adjust the path.

> First do a dry run by adding `--dry-run` — this decrypts + re-encrypts
> in memory and prints what it would write. Useful to sanity-check the
> key material before committing.

Expected output:

```
=== ldap_servers.bind_password_encrypted — 1 row(s) ===
  id=...: rotated
=== radius_nas_clients.secret_encrypted — 2 row(s) ===
  id=...: rotated
  id=...: rotated
=== certificates.key_pem_encrypted — 2 row(s) ===
  id=...: rotated
=== radius_realms.proxy_secret_encrypted — 0 row(s) ===
=== network_devices.snmp_community_encrypted — 0 row(s) ===
=== network_devices.coa_secret_encrypted — 0 row(s) ===

============================================================
Rotated N row(s).
Skipped (null/empty/already-new): M
```

Exit 0 ⇒ proceed. Exit 2 ⇒ stop, read the failure list — most likely a
typo in the OLD key paste (every decrypt fails) or you forgot the
prerequisite that all rows be ciphertext.

### Step 3 — Update `.env.production`

Replace BOTH lines with the new key material. Keep a backup of the
file so you can rollback if Step 4 fails.

```bash
sudo cp /opt/openradiusweb/.env.production \
        /opt/openradiusweb/.env.production.pre-rotation.$(date +%s)

sudo sed -i 's|^ORW_SECRET_MASTER=.*|ORW_SECRET_MASTER=<NEW master>|' \
            /opt/openradiusweb/.env.production
sudo sed -i 's|^ORW_SECRET_KDF_SALT=.*|ORW_SECRET_KDF_SALT=<NEW salt>|' \
            /opt/openradiusweb/.env.production

# Verify the swap landed:
sudo grep '^ORW_SECRET_' /opt/openradiusweb/.env.production
```

### Step 4 — Restart with the new key

```bash
sudo docker compose -f docker-compose.prod.yml --env-file .env.production \
    up -d --force-recreate --no-deps \
    gateway freeradius freeradius_config_watcher switch_mgmt coa_service
```

`--force-recreate` is required so the containers re-read the env
(stopping doesn't drop the env from a still-defined container).

### Step 5 — Verify (use the post-deploy runbook)

Go run [docs/runbook-post-deploy-verification.md](runbook-post-deploy-verification.md)
in full. Specifically:

- Step 4 — env vars set on every service ⇒ each must print 2
- Step 5 — DB ciphertext shape ⇒ every column `bad=0`. The new
  ciphertext starts with `A` (version 0x01) just like the old format,
  so a shape check doesn't tell new from old — you're verifying
  "no row got mangled", which is the actual risk.
- Step 6 — phone reconnect MDS-01 + MAB_Auth ⇒ both `Login OK`. This
  is the only thing that proves the new key actually decrypts what
  freeradius needs.

If step 6 succeeds the rotation is complete.

---

## Rollback (if step 5 fails)

You have ~2 minutes before you need to roll back. The recovery is just
"swap the env file back, restart":

```bash
# Restore the pre-rotation .env
LATEST=$(ls -t /opt/openradiusweb/.env.production.pre-rotation.* | head -1)
sudo cp "$LATEST" /opt/openradiusweb/.env.production

# Restart with the OLD env
sudo docker compose -f docker-compose.prod.yml --env-file .env.production \
    up -d --force-recreate --no-deps \
    gateway freeradius freeradius_config_watcher switch_mgmt coa_service
```

But the DB now has rows encrypted under the NEW key, so this *will*
fail loudly (`InvalidTag` from strict-mode `decrypt_secret`). The
proper rollback is:

```bash
# 1. Restore .env (as above)
# 2. Re-run the rotation script in REVERSE — swap NEW/OLD env vars
sudo docker exec -i orw-postgres bash -c '
  ORW_SECRET_MASTER_OLD="<NEW master>" \
  ORW_SECRET_KDF_SALT_OLD="<NEW salt>" \
  ORW_SECRET_MASTER_NEW="<OLD master>" \
  ORW_SECRET_KDF_SALT_NEW="<OLD salt>" \
  DB_PASSWORD="<from .env.production>" \
  python3 /tmp/rotate_secret_master.py
'
# 3. Restart services as in step 4
```

If even that fails ⇒ restore from the backup taken in pre-checklist
step 1: `sudo ./scripts/restore.sh backups/orw-backup-...tar.gz.gpg`.

---

## Post-rotation cleanup

- **Escrow the OLD key set FOREVER**. Backups predating the rotation
  contain the old `.env.production` with the old master, and restoring
  one is the disaster-recovery path. Without the old master in escrow,
  those backups become unrecoverable.
- **Update password manager / 1Password / Vault entry** with the new
  master + salt, marked with the rotation date.
- **Delete the `.env.production.pre-rotation.*` backup file** from the
  prod host once you're confident in the new key (24 h is fine). It's
  the old master in plaintext on disk.
- **Rotate `ORW_BACKUP_PASSPHRASE` separately** if the leak was bad
  enough to also touch backups. New passphrase only affects future
  backups; old ones still need the old passphrase to restore.
- **Document the rotation** in the security-audit doc with the date
  and trigger reason. Future you will want to know.

---

## Why no zero-downtime version

A zero-downtime rotation needs `decrypt_secret()` to try multiple keys
(new first, then old; or old first, then new) for the duration of the
re-encryption window. That's a code change to `shared/orw_common/secrets.py`
plus a coordination protocol — out of scope for the runbook.

If RADIUS downtime ever becomes intolerable, the right fix is Vault /
Cloud KMS (which solves rotation for you), not bolting dual-key support
onto our own scheme. Open an issue if this becomes a real constraint.
