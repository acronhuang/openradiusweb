# Rotation Runbook — `DB_PASSWORD`, `JWT_SECRET_KEY`, `REDIS_PASSWORD`

How to rotate the three credential env vars in `.env.production` that
PR #86's [key-rotation runbook](runbook-key-rotation.md) doesn't cover
(that one is for `ORW_SECRET_MASTER` + `ORW_SECRET_KDF_SALT` only —
the AES-256-GCM at-rest encryption key).

> Each of the three has its own sequencing constraints. Doing them in
> the wrong order will either lock services out (env updated before
> backend) or invalidate user sessions unexpectedly (JWT). Read the
> per-secret section before running the commands.

Plan budget per rotation: ~15 min (DB) / ~5 min (JWT) / ~10 min (Redis),
plus 5 min verification each.

---

## When to rotate any of these

Same triggers as the master rotation:
- **Suspected leak** of `.env.production` or any service container
- **Personnel change** with `.env.production` read access
- **Scheduled hygiene** — yearly at minimum
- **Post-incident** — any time the secret was typed in a chat /
  pasted into a script that was committed / accidentally logged

If the leak is confirmed, rotate the master FIRST (per
runbook-key-rotation.md), then the three below — `DB_PASSWORD` is in
the same file as `ORW_SECRET_MASTER`, so any leak that exposes one
exposes all of them.

---

## Pre-rotation checklist (applies to all three)

1. **Recent backup** — `./scripts/backup.sh` ran in the last 24 h
   (encrypted `.tar.gz.gpg`). Check `backups/`.
2. **Generate the new value** before touching anything:
   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
   `DB_PASSWORD` should avoid characters that need URL-encoding
   (`@`, `:`, `/`, `?`, `#`, `%`); `secrets.token_urlsafe` already
   uses only base64-url-safe chars.
3. **Maintenance window** — pick low-traffic time. RADIUS auth is
   unavailable for the recreate seconds (DB / Redis case). Mobile
   users see "Authenticating…" until the restart finishes.
4. **You have shell access to 192.168.0.250** as a user that can
   `sudo docker compose ...`.

---

## 1. Rotate `DB_PASSWORD` (most complex — ~15 min)

Postgres is live throughout — we use `ALTER USER` so the existing
password keeps working until the services restart with the new one.
Only the recreate window has auth-path downtime.

### Step 1 — Change postgres user's password while keeping old one valid

```bash
cd /opt/openradiusweb
NEW_PW="<paste new password from token_urlsafe>"

sudo docker exec -i orw-postgres psql -U orw -d orw -c \
    "ALTER USER orw WITH PASSWORD '$NEW_PW';"
```

Postgres now accepts EITHER the old or the new password until the
session caches expire (most clients reconnect on next query).

> Do NOT proceed to step 2 until the ALTER USER returns `ALTER ROLE`.
> If it errors, the rest of this runbook will brick auth.

### Step 2 — Update `.env.production`

```bash
sudo cp /opt/openradiusweb/.env.production \
        /opt/openradiusweb/.env.production.pre-rotation.$(date +%s)

sudo sed -i "s|^DB_PASSWORD=.*|DB_PASSWORD=$NEW_PW|" \
            /opt/openradiusweb/.env.production

sudo grep '^DB_PASSWORD=' /opt/openradiusweb/.env.production
# Should print the new value
```

### Step 3 — Recreate every service that connects to postgres

8 services: `gateway`, `discovery`, `device_inventory`, `switch_mgmt`,
`policy_engine`, `freeradius`, `freeradius_config_watcher`,
`coa_service`. (The `postgres` container itself doesn't need a
recreate — `POSTGRES_PASSWORD` is only consumed at first init; the
running postgres uses `ALTER USER` from step 1.)

```bash
sudo docker compose -f docker-compose.prod.yml --env-file .env.production \
    up -d --force-recreate --no-deps \
    gateway discovery device_inventory switch_mgmt policy_engine \
    freeradius freeradius_config_watcher coa_service
```

### Step 4 — Verify

```bash
# Every service that connects to postgres should be healthy
sudo docker compose -f docker-compose.prod.yml --env-file .env.production ps \
    gateway discovery device_inventory switch_mgmt policy_engine \
    freeradius freeradius_config_watcher coa_service

# No DB-connection errors in the last 30s
sudo docker logs --since 30s orw-gateway 2>&1 | grep -iE 'password authentication failed|connection refused'
sudo docker logs --since 30s orw-freeradius 2>&1 | grep -iE 'password authentication failed|connection refused'
```

Both grep blocks should print nothing. If you see
`password authentication failed for user "orw"`, the env file wasn't
picked up — re-run step 3 explicitly with `--env-file`.

Then run [runbook-post-deploy-verification.md](runbook-post-deploy-verification.md)
step 6 (phone reconnect MDS-01 + MAB).

### Rollback

If step 4 fails:

```bash
# Restore the old .env
LATEST=$(ls -t /opt/openradiusweb/.env.production.pre-rotation.* | head -1)
sudo cp "$LATEST" /opt/openradiusweb/.env.production

# Restore the old password in postgres (paste old pw)
sudo docker exec -i orw-postgres psql -U orw -d orw -c \
    "ALTER USER orw WITH PASSWORD '<OLD password>';"

# Recreate services again with old env
sudo docker compose -f docker-compose.prod.yml --env-file .env.production \
    up -d --force-recreate --no-deps \
    gateway discovery device_inventory switch_mgmt policy_engine \
    freeradius freeradius_config_watcher coa_service
```

---

## 2. Rotate `JWT_SECRET_KEY` (~5 min)

> ⚠️ **All currently logged-in users will be logged out immediately**
> when the gateway restarts with the new key. Their JWTs were signed
> with the old key and will fail verification.
>
> This is acceptable for incident response. For routine rotation,
> announce it ahead of time or do it after hours.

Only the `gateway` service uses `JWT_SECRET_KEY`. Other services
don't validate JWTs (they trust the gateway's authentication
middleware via internal NATS/HTTP).

### Procedure

```bash
cd /opt/openradiusweb
NEW_JWT="<paste new value from token_urlsafe>"

# Backup .env
sudo cp /opt/openradiusweb/.env.production \
        /opt/openradiusweb/.env.production.pre-rotation.$(date +%s)

# Swap
sudo sed -i "s|^JWT_SECRET_KEY=.*|JWT_SECRET_KEY=$NEW_JWT|" \
            /opt/openradiusweb/.env.production

# Recreate gateway only
sudo docker compose -f docker-compose.prod.yml --env-file .env.production \
    up -d --force-recreate --no-deps gateway

# Verify gateway healthy
sudo docker logs --since 30s orw-gateway 2>&1 | grep -iE 'error|uvicorn running'
```

Login to the UI fresh (old session no longer valid) → confirm new
session works → done.

### Rollback

If something goes wrong, restore the old `.env.production` and
recreate gateway. Same steps as the DB rollback, scoped to gateway.

### Why no zero-downtime version

Same answer as the master rotation: would need dual-key support in
the gateway's JWT validation code. Out of scope for a runbook; the
right fix if this becomes a constraint is a session store (Redis)
that can be migrated independently of the signing key.

---

## 3. Rotate `REDIS_PASSWORD` (~10 min)

Redis supports live `CONFIG SET requirepass` so the running instance
keeps serving until services reconnect. Same shape as DB.

### Step 1 — Set the new password on the live redis

```bash
cd /opt/openradiusweb
NEW_REDIS_PW="<paste new value>"

# Use the OLD password for AUTH (still required for the CONFIG SET
# command itself). Read it from the current .env.production:
OLD_REDIS_PW=$(sudo grep '^REDIS_PASSWORD=' /opt/openradiusweb/.env.production | cut -d= -f2-)

sudo docker exec orw-redis redis-cli -a "$OLD_REDIS_PW" \
    CONFIG SET requirepass "$NEW_REDIS_PW"
```

Redis now requires the new password for new connections. Existing
connections (held by gateway / switch_mgmt / etc) keep working until
they re-auth. Redis loses any cached data not yet persisted on a
restart, but that's already true for our usage — Redis is a session
cache, not a source of truth.

### Step 2 — Update `.env.production`

```bash
sudo cp /opt/openradiusweb/.env.production \
        /opt/openradiusweb/.env.production.pre-rotation.$(date +%s)

sudo sed -i "s|^REDIS_PASSWORD=.*|REDIS_PASSWORD=$NEW_REDIS_PW|" \
            /opt/openradiusweb/.env.production
```

### Step 3 — Recreate every service that connects to redis

5 services: `gateway`, `discovery`, `device_inventory`, `switch_mgmt`,
`policy_engine`. Plus `redis` itself — its startup command
references `${REDIS_PASSWORD}` so we want it recreated too so the
healthcheck uses the new password.

```bash
sudo docker compose -f docker-compose.prod.yml --env-file .env.production \
    up -d --force-recreate --no-deps \
    redis gateway discovery device_inventory switch_mgmt policy_engine
```

### Step 4 — Verify

```bash
# Healthcheck status
sudo docker compose -f docker-compose.prod.yml --env-file .env.production ps redis

# No Redis auth errors
sudo docker logs --since 30s orw-gateway 2>&1 | grep -iE 'NOAUTH|WRONGPASS|connection refused'
```

### Rollback

```bash
# Set the OLD password back on live redis (need NEW pw to auth)
sudo docker exec orw-redis redis-cli -a "$NEW_REDIS_PW" \
    CONFIG SET requirepass "$OLD_REDIS_PW"

# Restore .env
LATEST=$(ls -t /opt/openradiusweb/.env.production.pre-rotation.* | head -1)
sudo cp "$LATEST" /opt/openradiusweb/.env.production

# Recreate
sudo docker compose -f docker-compose.prod.yml --env-file .env.production \
    up -d --force-recreate --no-deps \
    redis gateway discovery device_inventory switch_mgmt policy_engine
```

---

## Post-rotation cleanup (applies to all three)

- **Update password manager / 1Password / Vault entry** with the new
  value, marked with the rotation date.
- **Delete `.env.production.pre-rotation.*`** from the prod host
  after 24 h of confidence in the new value. It's the old password
  in plaintext on disk.
- **Note the rotation** in `docs/security-audit-2026-05-02-secret-storage.md`
  (or your team's audit log) with date + trigger reason.
- **Don't rotate everything at once** unless responding to a confirmed
  leak. Spreading them out keeps the blast radius of "I typo'd the
  ALTER USER" small.

---

## What's NOT in this runbook

- **`ORW_SECRET_MASTER` + `ORW_SECRET_KDF_SALT`** —
  [runbook-key-rotation.md](runbook-key-rotation.md). Different
  procedure (re-encrypt every DB column).
- **`ORW_BACKUP_PASSPHRASE`** — only matters for restoring backups;
  rotate by generating a new value and updating `.env.production`.
  Old backups still need the old passphrase (keep it in escrow). No
  service restart required.
- **TLS server cert / CA private key** — these are in the
  `certificates` table, not `.env.production`. Rotate via the UI
  (Certificates → Generate / Import). The watcher picks up the new
  cert on its next reconcile.
- **AD service-account password** — managed in AD, not here. Update
  it in AD first, then update `ldap_servers.bind_password` via the
  UI (gets encrypted at write).
