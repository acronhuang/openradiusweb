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

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y ca-certificates curl gnupg lsb-release

# Add Docker GPG key
sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg

# Add Docker repository
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Install Docker Engine + Compose Plugin
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# Add current user to docker group (no sudo needed)
sudo usermod -aG docker $USER

# Verify
docker --version
docker compose version
```

> **Note:** Log out and back in after adding the docker group.

### 2.3 Create Deployment Directory

```bash
sudo mkdir -p /opt/openradiusweb
sudo chown $USER:$USER /opt/openradiusweb
```

---

## 3. File Transfer to Server

### 3.1 Option A: Git Clone (Recommended)

```bash
cd /opt
sudo git clone https://github.com/YOUR_ORG/openradiusweb.git
sudo chown -R $USER:$USER openradiusweb
```

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

### 4.5 Generate Secrets

```bash
# Generate all three secrets at once
echo "DB_PASSWORD=$(openssl rand -base64 24)"
echo "REDIS_PASSWORD=$(openssl rand -base64 24)"
echo "JWT_SECRET_KEY=$(openssl rand -hex 32)"
```

### 4.6 Identify Network Interface

```bash
ip addr show
# Update SCAN_INTERFACE if not eth0 (e.g., ens33, ens160)
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

### 9.4 Browser Access

| Item | URL |
|------|-----|
| Web UI | `http://SERVER_IP:8888` |
| API Swagger Docs | `http://SERVER_IP:8000/docs` |
| API Health | `http://SERVER_IP:8000/health` |

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

> **Version:** 2.0
> **Last Updated:** 2026-04-23
> **Applies to:** OpenRadiusWeb Docker Deployment (12-container architecture)
