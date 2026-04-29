# OpenRadiusWeb Deployment Guide

> Complete guide for deploying OpenRadiusWeb from a development machine to an Ubuntu 22.04 production server using Docker Compose (12 containers).

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Prerequisites](#2-prerequisites)
3. [File Transfer to Server](#3-file-transfer-to-server)
4. [Environment Configuration](#4-environment-configuration)
5. [Fresh Deployment](#5-fresh-deployment)
6. [Database](#6-database)
7. [Services and Ports](#7-services-and-ports)
8. [Firewall Configuration](#8-firewall-configuration)
9. [Post-Deployment Verification](#9-post-deployment-verification)
10. [Common Operations](#10-common-operations)

---

## 1. System Overview

**OpenRadiusWeb (ORW)** is a web-based Network Access Control (NAC) system providing:

- **Device Discovery**: Automatic network device detection via ARP/SNMP/Nmap
- **Device Inventory**: Centralized endpoint management
- **802.1X Authentication**: Enterprise-grade wired/wireless auth via FreeRADIUS
- **Policy Engine**: Automatic access policy enforcement based on device properties
- **Switch Management**: Centralized switch configuration (SNMP/SSH)
- **Dynamic Authorization (CoA)**: Real-time authorization changes
- **Dynamic VLAN Assignment**: AD/LDAP group-based VLAN assignment
- **Configuration Sync**: Automatic FreeRADIUS config reload on changes

**Architecture: 12 Docker Containers**

| Container | Description |
|-----------|-------------|
| `postgres` | PostgreSQL 15 + TimescaleDB |
| `redis` | Redis 7 cache and session store |
| `nats` | NATS JetStream message bus |
| `gateway` | FastAPI REST API Gateway |
| `frontend` | React + Nginx Web UI |
| `discovery` | Network device discovery service |
| `device_inventory` | Device asset management service |
| `policy_engine` | Policy evaluation and action execution |
| `switch_mgmt` | Switch management (SNMP/SSH) |
| `freeradius` | FreeRADIUS 802.1X authentication |
| `freeradius_config_watcher` | FreeRADIUS configuration sync |
| `coa_service` | RADIUS Change of Authorization |

---

## 2. Prerequisites

### 2.1 Hardware Requirements

| Item | Minimum | Recommended |
|------|---------|-------------|
| OS | Ubuntu 22.04 LTS | Ubuntu 22.04 LTS |
| CPU | 2 cores | 4+ cores |
| RAM | 4 GB | 8+ GB |
| Disk | 20 GB | 50+ GB (includes logs and database) |
| Network | 1 NIC | 2 NICs (management + scanning) |

### 2.2 Install Docker and Docker Compose

> **Run each block sequentially; verify the output of each before proceeding.**
> A copy-paste of the entire script can fail silently — the GPG-key step in particular has been seen to leave both `docker.gpg` and `docker.list` missing without an obvious error.

```bash
# === 1. Pre-requisites ===
sudo apt update
sudo apt install -y ca-certificates curl gnupg lsb-release

# === 2. Confirm Docker registry is reachable (catch network/proxy issues early) ===
curl -sI https://download.docker.com/ | head -3
# Expect: HTTP/2 200 (or 301). If it hangs or returns nothing, fix outbound network first.

# === 3. Install GPG key — verify the file exists at the end ===
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /tmp/docker.gpg
sudo gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg /tmp/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
ls -la /etc/apt/keyrings/docker.gpg
# Expect: -rw-r--r-- ... ~2.7K bytes

# === 4. Add Docker repository — verify the file exists at the end ===
ARCH=$(dpkg --print-architecture)
CODENAME=$(lsb_release -cs)
echo "deb [arch=$ARCH signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $CODENAME stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list

cat /etc/apt/sources.list.d/docker.list
# Expect a single line: deb [arch=amd64 signed-by=...] https://download.docker.com/linux/ubuntu jammy stable

# === 5. Update package list — Docker repo MUST appear in output ===
sudo apt update 2>&1 | grep -E "docker|Hit|Get" | head
# Expect at least one line containing "https://download.docker.com/linux/ubuntu jammy InRelease"

# === 6. Install Docker Engine + Compose + Buildx ===
sudo apt install -y \
  docker-ce \
  docker-ce-cli \
  containerd.io \
  docker-buildx-plugin \
  docker-compose-plugin

# === 7. Add current user to docker group ===
sudo usermod -aG docker $USER

# === 8. Verify (must run in a NEW shell — see note below) ===
newgrp docker     # picks up the docker group in the current shell
docker --version
docker compose version
docker run --rm hello-world
```

> **Two common gotchas:**
>
> 1. **`sudo apt install -y docker-ce` fails with "Package docker-ce has no installation candidate"** — step 4 didn't actually create `/etc/apt/sources.list.d/docker.list`. Re-run step 4 (the multi-line `echo` is the most fragile part — keep it on one line as written) and confirm `cat` shows the expected content.
> 2. **`docker compose ... build` later fails with "permission denied while trying to connect to the docker API at unix:///var/run/docker.sock"** — `newgrp docker` only affects the current shell. After SSH reconnects you may lose the membership again. Two safe workarounds: (a) `exit` and SSH back in (the membership is then permanent for that login), or (b) prefix all docker commands with `sudo`.

### 2.3 Create Deployment Directory

```bash
sudo mkdir -p /opt/openradiusweb
sudo chown $USER:$USER /opt/openradiusweb
```

---

## 3. File Transfer to Server

### 3.1 Option A: Git Clone (Recommended)

```bash
sudo mkdir -p /opt && sudo chown $USER:$USER /opt
cd /opt
git clone https://github.com/acronhuang/openradiusweb.git
cd openradiusweb
git log --oneline -3      # Confirm you have the latest main HEAD
```

> **If the repo is private**, GitHub no longer accepts password authentication for HTTPS git operations. You must use one of:
>
> - **Personal Access Token (PAT)** — generate a fine-grained token at https://github.com/settings/personal-access-tokens/new with `Repository access: Only select repositories → openradiusweb` and `Permissions → Repository → Contents: Read-only`. Embed it directly in the clone URL **for the clone only**, then immediately scrub it from `git config`:
>   ```bash
>   git clone https://<TOKEN>@github.com/acronhuang/openradiusweb.git
>   cd openradiusweb
>   git remote set-url origin https://github.com/acronhuang/openradiusweb.git   # remove token from .git/config
>   ```
>   ⚠️ Never paste the PAT into chat, screenshots, or shared logs. If it leaks, revoke it at https://github.com/settings/personal-access-tokens immediately.
> - **SSH key** — `ssh-keygen -t ed25519 -C "deploy@$(hostname)"`, add the `.pub` key at https://github.com/settings/keys, then clone with `git clone git@github.com:acronhuang/openradiusweb.git`.
>
> **If the repo is public**, no auth needed — `git clone https://github.com/acronhuang/openradiusweb.git` just works.

### 3.2 Option B: SCP/SFTP Upload

```bash
# From your development machine
tar czf openradiusweb.tar.gz \
  --exclude=node_modules --exclude=.git --exclude=__pycache__ \
  -C /path/to openradiusweb

scp openradiusweb.tar.gz user@SERVER_IP:/tmp/
ssh user@SERVER_IP "cd /opt && sudo tar xzf /tmp/openradiusweb.tar.gz && sudo chown -R \$USER:\$USER /opt/openradiusweb"
```

### 3.3 Option C: Rsync

```bash
rsync -avz --exclude='node_modules' --exclude='.git' --exclude='__pycache__' \
  ./openradiusweb/ user@SERVER_IP:/opt/openradiusweb/
```

---

## 4. Environment Configuration

### 4.1 Create Production Environment File

```bash
cp .env.example .env.production
nano .env.production
```

### 4.2 Required Credentials

**You MUST set these three secrets before deployment.** The application will not start without them.

| Secret | Env Variable | How to Generate | Used By |
|--------|-------------|-----------------|---------|
| Database Password | `DB_PASSWORD` | `openssl rand -base64 24` | PostgreSQL, all services |
| Redis Password | `REDIS_PASSWORD` | `openssl rand -base64 24` | Redis, gateway, discovery, device_inventory, policy_engine, switch_mgmt |
| JWT Secret Key | `JWT_SECRET_KEY` | `openssl rand -hex 32` | API Gateway (token signing) |

#### Where Credentials Are Used

The following table shows every file and environment variable that references credentials. All credentials are injected via environment variables defined in `.env.production` and passed through `docker-compose.prod.yml`:

| Credential | Env Variable | Files That Read It | Notes |
|------------|-------------|-------------------|-------|
| DB Password | `DB_PASSWORD` | `docker-compose.prod.yml` (all services) | Injected as `DATABASE_URL` or `ORW_DB_URL` |
| | `DATABASE_URL` | `shared/orw_common/config.py` | Format: `postgresql+asyncpg://orw:PASSWORD@postgres:5432/orw` |
| | `ORW_DB_URL` | `services/auth/freeradius/mods-config/python/rlm_orw.py` | Format: `postgresql://orw:PASSWORD@postgres:5432/orw` |
| | `ORW_DB_URL` | `services/auth/freeradius_config_watcher.py` | Same format as rlm_orw.py |
| | `ORW_DB_URL` | `services/auth/freeradius_config_manager.py` | Same format as rlm_orw.py |
| Redis Password | `REDIS_PASSWORD` | `docker-compose.prod.yml` (redis command + services) | Injected as `REDIS_URL` |
| | `REDIS_URL` | `shared/orw_common/config.py` | Format: `redis://:PASSWORD@redis:6379/0` |
| JWT Secret | `JWT_SECRET_KEY` | `docker-compose.prod.yml` (gateway) | Passed directly to gateway |
| | `JWT_SECRET_KEY` | `shared/orw_common/config.py` | Used for token signing/verification |

### 4.3 Default Admin Account

| Field | Value |
|-------|-------|
| Username | `admin` |
| Password | `OpenNAC2026` |
| Role | `admin` |

> **IMPORTANT:** Change the admin password immediately after first login via **Settings > Users** in the Web UI.

The default password is set in `migrations/seed.sql` as a bcrypt hash. To change the default before deployment, generate a new hash:

```bash
python3 -c "import bcrypt; print(bcrypt.hashpw(b'YOUR_NEW_PASSWORD', bcrypt.gensalt()).decode())"
```

Then update the `password_hash` value in `migrations/seed.sql`.

### 4.4 Full Configuration Reference

```env
# ==========================================================
# REQUIRED — Must set before deployment
# ==========================================================
DB_PASSWORD=<generated-db-password>
REDIS_PASSWORD=<generated-redis-password>
JWT_SECRET_KEY=<generated-jwt-secret>

# ==========================================================
# Network Configuration
# ==========================================================
# Web UI port
ORW_WEB_PORT=8888
# API port
ORW_API_PORT=8000
# Network interface for device discovery
SCAN_INTERFACE=eth0

# RADIUS ports
RADIUS_AUTH_PORT=1812
RADIUS_ACCT_PORT=1813
RADIUS_COA_PORT=3799

# CORS (set to your server URL)
CORS_ORIGINS=http://YOUR_SERVER_IP:8888

# ==========================================================
# Optional
# ==========================================================
# Logging level: DEBUG, INFO, WARNING, ERROR
LOG_LEVEL=INFO

# Data retention (days)
AUTH_LOG_RETENTION_DAYS=365
EVENT_LOG_RETENTION_DAYS=180
```

### 4.5 Generate Secrets and Write Them Into `.env.production`

> **Do not use `openssl rand -base64` directly with `sed` — base64 output can contain `+`, `/`, `|`, or `=`, and even after `tr -d` removes them the remaining string can still break a `sed` command if the delimiter clashes.** Use the alphanumeric-only generator below; it's guaranteed sed-safe regardless of the delimiter you pick.

```bash
cd /opt/openradiusweb

# === 1. Copy the template ===
cp .env.example .env.production

# === 2. Generate three alphanumeric secrets (sed-safe) ===
DBP=$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 32)
RDP=$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 32)
JWP=$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 64)

# Use ~ as the sed delimiter (won't appear in alphanumeric output)
sed -i "s~^DB_PASSWORD=.*~DB_PASSWORD=$DBP~"           .env.production
sed -i "s~^REDIS_PASSWORD=.*~REDIS_PASSWORD=$RDP~"     .env.production
sed -i "s~^JWT_SECRET_KEY=.*~JWT_SECRET_KEY=$JWP~"     .env.production

# === 3. Verify all three were actually written ===
grep -E "^(DB_PASSWORD|REDIS_PASSWORD|JWT_SECRET_KEY)=" .env.production | sed 's|=.*|=*** (set)|'
# Expect three "*** (set)" lines. If any line is missing or shows CHANGE_ME, re-run the corresponding sed.
```

> **If a `sed` step prints `unterminated 's' command`** — the value contained the delimiter character. Switch the delimiter (try `~`, `#`, or `@`) or use the `tr -dc 'A-Za-z0-9'` form above which never contains punctuation.

### 4.6 Identify Network Interface

```bash
ip -br a
# Pick the interface with your management IP (typically eno1 / ens33 / ens160 / enp0s3),
# NOT lo or docker0 or any DOWN interface.
```

Then write it into `.env.production`:

```bash
# Replace eno1 with whatever you saw above
sed -i 's|^SCAN_INTERFACE=.*|SCAN_INTERFACE=eno1|' .env.production
grep '^SCAN_INTERFACE' .env.production
# Expect: SCAN_INTERFACE=eno1
```

### 4.7 Secure the Configuration

```bash
chmod 600 /opt/openradiusweb/.env.production
```

---

## 5. Fresh Deployment

### 5.1 Pre-Flight Check

```bash
cd /opt/openradiusweb
ls -la docker-compose.prod.yml .env.production
ls -la migrations/init.sql migrations/seed.sql
```

### 5.2 Stop Old Containers (If Any)

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production down
```

### 5.3 Clean Volumes (Fresh Install Only)

> **WARNING:** This deletes ALL data. Only do this for fresh installs.

```bash
docker volume rm openradiusweb_postgres_data openradiusweb_redis_data \
  openradiusweb_nats_data openradiusweb_freeradius_config \
  openradiusweb_freeradius_certs 2>/dev/null || true
```

### 5.4 Build All Images

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production build --no-cache
```

> First build takes 5-15 minutes (downloading base images, pip install, npm install).

### 5.5 Start All Services

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production up -d
```

Startup order (handled automatically by `depends_on`):
1. **Infrastructure**: postgres -> redis -> nats (wait for healthcheck)
2. **Core**: gateway (waits for postgres/redis/nats)
3. **Application**: discovery, device_inventory, policy_engine, switch_mgmt
4. **Authentication**: freeradius -> config_watcher, coa_service
5. **Frontend**: frontend (waits for gateway)

### 5.6 Wait and Verify

```bash
sleep 30
docker compose -f docker-compose.prod.yml --env-file .env.production ps
```

All 12 containers should show `Up` or `Up (healthy)`.

---

## 6. Database

### 6.1 Engine

PostgreSQL 15 with TimescaleDB extension for time-series data (auth logs, events, audit).

### 6.2 Auto-Initialization

On first startup, PostgreSQL runs these migration scripts:

| Order | File | Description |
|-------|------|-------------|
| 01 | `migrations/init.sql` | Core schema (all tables, indexes, hypertables, triggers) |
| 02 | `migrations/002_settings_radius_features.sql` | System settings and RADIUS feature tables |
| 03 | `migrations/seed.sql` | Default admin user, tenant, sample data |

### 6.3 Post-Init Migrations

Additional migrations must be run manually:

```bash
docker cp migrations/003_vlans_mab.sql orw-postgres:/tmp/
docker exec orw-postgres psql -U orw -d orw -f /tmp/003_vlans_mab.sql

docker cp migrations/004_group_vlan_mappings.sql orw-postgres:/tmp/
docker exec orw-postgres psql -U orw -d orw -f /tmp/004_group_vlan_mappings.sql
```

### 6.4 Default Admin Credentials

See [Section 4.3 — Default Admin Account](#43-default-admin-account).

### 6.5 Database Access

```bash
# Interactive PostgreSQL shell
docker exec -it orw-postgres psql -U orw -d orw

# Run a single query
docker exec orw-postgres psql -U orw -d orw -c "SELECT count(*) FROM devices;"

# List all tables
docker exec orw-postgres psql -U orw -d orw -c "\dt"
```

---

## 7. Services and Ports

### 7.1 Externally Accessible Services

| Service | Container | Port | Protocol | Description |
|---------|-----------|------|----------|-------------|
| Web UI | orw-frontend | 8888 | TCP | React SPA + Nginx reverse proxy |
| API | orw-gateway | 8000 | TCP | FastAPI REST API + Swagger docs |
| RADIUS Auth | orw-freeradius | 1812 | UDP | 802.1X authentication |
| RADIUS Acct | orw-freeradius | 1813 | UDP | RADIUS accounting |
| CoA | orw-coa | 3799 | UDP | Change of Authorization |

### 7.2 Localhost-Only Services

| Service | Container | Port | Description |
|---------|-----------|------|-------------|
| PostgreSQL | orw-postgres | 5432 | Database |
| Redis | orw-redis | 6379 | Cache |
| NATS | orw-nats | 4222 | Message bus |
| NATS Monitor | orw-nats | 8222 | NATS dashboard |

### 7.3 Internal Services (No External Ports)

| Service | Container | Description |
|---------|-----------|-------------|
| Discovery | orw-discovery | Device discovery (host network mode) |
| Device Inventory | orw-device-inventory | Device management |
| Policy Engine | orw-policy-engine | Policy evaluation |
| Switch Mgmt | orw-switch-mgmt | Switch control (SNMP/SSH) |
| Config Watcher | orw-freeradius-config-watcher | FreeRADIUS config sync |

---

## 8. Firewall Configuration

```bash
sudo ufw allow 22/tcp comment 'SSH'
sudo ufw allow 8888/tcp comment 'OpenRadiusWeb Web UI'
sudo ufw allow 8000/tcp comment 'OpenRadiusWeb API'
sudo ufw allow 1812/udp comment 'RADIUS Authentication'
sudo ufw allow 1813/udp comment 'RADIUS Accounting'
sudo ufw allow 3799/udp comment 'RADIUS CoA'
sudo ufw --force enable
sudo ufw status verbose
```

### Security Recommendation

Restrict RADIUS ports to known NAS/switch subnets:

```bash
sudo ufw allow from 10.0.0.0/8 to any port 1812 proto udp
sudo ufw allow from 172.16.0.0/12 to any port 1812 proto udp
```

---

## 9. Post-Deployment Verification

### 9.1 Container Status

```bash
docker compose -f docker-compose.prod.yml ps
```

### 9.2 Health Checks

```bash
# PostgreSQL
docker exec orw-postgres pg_isready -U orw

# Redis
docker exec orw-redis redis-cli ping

# NATS
curl -sf http://127.0.0.1:8222/healthz

# API Gateway
curl -s http://localhost:8000/health

# Web UI
curl -s -o /dev/null -w '%{http_code}' http://localhost:8888
```

### 9.3 Login Test

```bash
curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"OpenNAC2026"}'
```

Expect a JSON response containing `access_token`. **If you get `{"detail":"Invalid credentials"}` (HTTP 401)**, the bcrypt hash in `migrations/seed.sql` was generated against a different bcrypt version than the one inside the gateway container. Reset it from the gateway container's own bcrypt — see [§9.5](#95-reset-admin-password-when-seed-hash-is-incompatible).

### 9.4 Browser Access

| Item | URL |
|------|-----|
| Web UI | `http://SERVER_IP:8888` |
| API Swagger Docs | `http://SERVER_IP:8000/docs` |
| API Health | `http://SERVER_IP:8000/health` |

### 9.5 Reset admin password when seed hash is incompatible

This is needed once per fresh deployment when the seed bcrypt hash and the gateway's bcrypt version disagree (typical on first deploys against a newer Python image). Generating the hash inside the gateway container guarantees it uses the same bcrypt build as the verifier.

```bash
# === 1. Generate a fresh bcrypt hash using the gateway container's own bcrypt ===
NEWHASH=$(docker exec orw-gateway python -c "import bcrypt; print(bcrypt.hashpw(b'OpenNAC2026', bcrypt.gensalt()).decode())")
echo "Length: ${#NEWHASH}"     # Expect 60

# === 2. Write it into the users table ===
docker exec orw-postgres psql -U orw -d orw -c \
  "UPDATE users SET password_hash='$NEWHASH' WHERE username='admin';"
# Expect: UPDATE 1

# === 3. Clear any rate-limit / lockout state in Redis ===
REDIS_PW=$(grep ^REDIS_PASSWORD /opt/openradiusweb/.env.production | cut -d= -f2)
docker exec orw-redis redis-cli -a "$REDIS_PW" FLUSHDB
# Expect: OK

# === 4. Retry login — should now return access_token ===
curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"OpenNAC2026"}'
```

> **Do this immediately on first deploy, before exposing the system to anyone**, because the default `OpenNAC2026` password is documented and assumed by the smoke tests below. Change it via **Profile → Change Password** in the Web UI as soon as you've logged in once.

### 9.6 Smoke a migrated endpoint (verifies the 19/19 feature migration is live)

```bash
# Login and stash the token
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"OpenNAC2026"}' \
  | python3 -c "import json,sys; print(json.load(sys.stdin).get('access_token',''))")
echo "Token len: ${#TOKEN}"     # Expect 200+

# Hit the dot1x_overview endpoint (the most aggregate of all migrated features —
# composes 10 atomic queries across 9 tables; exercises a wide swath of code)
curl -sf -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/dot1x/overview \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('keys:', sorted(d.keys()))"
```

Expect exactly:
```
keys: ['auth_stats_24h', 'certificates', 'eap_methods', 'group_vlan_mappings', 'mab_devices', 'nas_clients', 'policies', 'realms', 'vlans']
```

If you see a different key set or an HTTP error, the gateway image is older than `e4be823` (post-migration HEAD) — rebuild and restart it:
```bash
docker compose -f docker-compose.prod.yml --env-file .env.production build --no-cache gateway
docker compose -f docker-compose.prod.yml --env-file .env.production up -d gateway
```

---

## 10. Common Operations

### 10.1 View Logs

```bash
# All services (live)
docker compose -f docker-compose.prod.yml logs -f

# Specific service
docker logs orw-gateway --tail=50 -f
docker logs orw-freeradius --tail=50 -f

# Last 100 lines
docker logs orw-gateway --tail=100
```

### 10.2 Restart Services

```bash
# Single service
docker compose -f docker-compose.prod.yml restart gateway

# All services
docker compose -f docker-compose.prod.yml restart
```

### 10.3 Rebuild and Redeploy

```bash
# Rebuild specific service after code changes
docker compose -f docker-compose.prod.yml build --no-cache gateway
docker compose -f docker-compose.prod.yml up -d gateway

# Rebuild all
docker compose -f docker-compose.prod.yml up -d --build
```

### 10.4 Stop All Services

```bash
# Stop (keep data)
docker compose -f docker-compose.prod.yml down

# Stop and DELETE all data
docker compose -f docker-compose.prod.yml down -v
```

### 10.5 Monitor Resource Usage

```bash
docker stats --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}"
```

---

## Appendix: Quick Reference

| Service | URL | Credentials |
|---------|-----|-------------|
| Web UI | http://SERVER_IP:8888 | admin / OpenNAC2026 |
| API Swagger | http://SERVER_IP:8000/docs | - |
| API Health | http://SERVER_IP:8000/health | - |
| PostgreSQL | 127.0.0.1:5432 | orw / (DB_PASSWORD) |
| Redis | 127.0.0.1:6379 | (REDIS_PASSWORD) |
| NATS Monitor | http://127.0.0.1:8222 | - |

---

> **Version:** 2.1
> **Last Updated:** 2026-04-30
> **Applies to:** OpenRadiusWeb Docker Deployment (12-container architecture)
>
> **Changes in 2.1** (real-world fixes from a fresh deployment on 2026-04-30):
> - §2.2 — split into 8 explicit verification steps; added `docker-buildx-plugin` (was missing); added `newgrp docker` / SSH-reconnect note for the most common "permission denied on docker.sock" failure.
> - §3.1 — replaced `YOUR_ORG` placeholder; added explicit PAT-vs-SSH-vs-public guidance for HTTPS auth (GitHub no longer accepts password auth); added token-leak warning and one-liner to scrub the token from `.git/config` after clone.
> - §4.5 — replaced fragile `openssl rand -base64` with sed-safe alphanumeric `tr -dc` generator; switched sed delimiter to `~`; added per-step verification.
> - §4.6 — switched `ip addr show` to `ip -br a` (cleaner output) and added the `sed` line to write `SCAN_INTERFACE` directly.
> - §9.5 (new) — bcrypt seed-hash incompatibility workaround (generate the hash inside the gateway container so it uses the same bcrypt build as the verifier).
> - §9.6 (new) — smoke test against `/api/v1/dot1x/overview` to confirm the 19/19 feature migration is live.
