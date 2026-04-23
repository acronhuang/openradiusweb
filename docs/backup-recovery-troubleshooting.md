# OpenRadiusWeb Backup, Disaster Recovery & Troubleshooting

## Table of Contents

1. [Backup Procedures](#1-backup-procedures)
2. [Restore Procedures](#2-restore-procedures)
3. [Disaster Recovery](#3-disaster-recovery)
4. [Troubleshooting Guide](#4-troubleshooting-guide)

---

## 1. Backup Procedures

### 1.1 What to Back Up

| Component | Data Location | Priority | Method |
|-----------|--------------|----------|--------|
| PostgreSQL Database | Docker volume `postgres_data` | **Critical** | pg_dump |
| Environment Config | `.env.production` | **Critical** | File copy |
| FreeRADIUS Certificates | Docker volume `freeradius_certs` | **High** | Docker volume backup |
| FreeRADIUS Config | Docker volume `freeradius_config` | Medium | Regenerated from DB |
| Redis Data | Docker volume `redis_data` | Low | Transient cache |
| NATS Data | Docker volume `nats_data` | Low | Transient messages |
| Application Code | `/opt/openradiusweb/` | Medium | Git or file copy |

### 1.2 Database Backup

#### Full Database Dump

```bash
#!/bin/bash
# backup_db.sh - Full PostgreSQL backup
BACKUP_DIR="/opt/openradiusweb/backups"
DATE=$(date +%Y%m%d_%H%M%S)
mkdir -p "$BACKUP_DIR"

# Full dump (custom format for pg_restore)
docker exec orw-postgres pg_dump -U orw -d orw -Fc \
  > "$BACKUP_DIR/orw_full_${DATE}.dump"

# SQL text dump (human-readable, for emergencies)
docker exec orw-postgres pg_dump -U orw -d orw \
  > "$BACKUP_DIR/orw_full_${DATE}.sql"

# Compress SQL dump
gzip "$BACKUP_DIR/orw_full_${DATE}.sql"

echo "Backup complete: $BACKUP_DIR/orw_full_${DATE}.dump"
echo "Size: $(du -h "$BACKUP_DIR/orw_full_${DATE}.dump" | cut -f1)"

# Clean old backups (keep last 30 days)
find "$BACKUP_DIR" -name "orw_full_*.dump" -mtime +30 -delete
find "$BACKUP_DIR" -name "orw_full_*.sql.gz" -mtime +30 -delete
```

#### Schema-Only Backup

```bash
docker exec orw-postgres pg_dump -U orw -d orw --schema-only \
  > /opt/openradiusweb/backups/orw_schema_$(date +%Y%m%d).sql
```

#### Data-Only Backup (Specific Tables)

```bash
# Backup critical configuration tables only
docker exec orw-postgres pg_dump -U orw -d orw \
  --data-only \
  -t users -t tenants -t policies -t ldap_servers \
  -t radius_nas_clients -t radius_realms -t certificates \
  -t vlans -t mab_devices -t group_vlan_mappings \
  -t system_settings \
  > /opt/openradiusweb/backups/orw_config_$(date +%Y%m%d).sql
```

### 1.3 Certificate Backup

```bash
#!/bin/bash
# backup_certs.sh - Backup FreeRADIUS certificates
BACKUP_DIR="/opt/openradiusweb/backups"
DATE=$(date +%Y%m%d_%H%M%S)

# Copy certs from Docker volume
docker run --rm \
  -v openradiusweb_freeradius_certs:/certs \
  -v "$BACKUP_DIR":/backup \
  alpine tar czf "/backup/freeradius_certs_${DATE}.tar.gz" -C /certs .

echo "Certificate backup: $BACKUP_DIR/freeradius_certs_${DATE}.tar.gz"
```

### 1.4 Full System Backup

```bash
#!/bin/bash
# backup_all.sh - Complete system backup
BACKUP_DIR="/opt/openradiusweb/backups"
DATE=$(date +%Y%m%d_%H%M%S)
mkdir -p "$BACKUP_DIR"

echo "=== OpenRadiusWeb Full Backup ==="
echo "Date: $(date)"

# 1. Database
echo "1/4 Backing up database..."
docker exec orw-postgres pg_dump -U orw -d orw -Fc \
  > "$BACKUP_DIR/orw_db_${DATE}.dump"

# 2. Certificates
echo "2/4 Backing up certificates..."
docker run --rm \
  -v openradiusweb_freeradius_certs:/certs \
  -v "$BACKUP_DIR":/backup \
  alpine tar czf "/backup/orw_certs_${DATE}.tar.gz" -C /certs .

# 3. Configuration files
echo "3/4 Backing up configuration..."
tar czf "$BACKUP_DIR/orw_config_${DATE}.tar.gz" \
  -C /opt/openradiusweb \
  .env.production docker-compose.prod.yml

# 4. Application code (optional - skip if using git)
echo "4/4 Backing up application code..."
tar czf "$BACKUP_DIR/orw_code_${DATE}.tar.gz" \
  --exclude='node_modules' --exclude='__pycache__' \
  --exclude='.git' --exclude='backups' \
  -C /opt openradiusweb

echo ""
echo "=== Backup Complete ==="
ls -lh "$BACKUP_DIR"/orw_*_${DATE}*
echo ""
echo "Total size: $(du -sh "$BACKUP_DIR"/orw_*_${DATE}* | tail -1 | cut -f1)"
```

### 1.5 Automated Backup Schedule

Add to crontab (`crontab -e`):

```cron
# Daily database backup at 2:00 AM
0 2 * * * /opt/openradiusweb/scripts/backup_db.sh >> /var/log/orw-backup.log 2>&1

# Weekly full backup on Sunday at 3:00 AM
0 3 * * 0 /opt/openradiusweb/scripts/backup_all.sh >> /var/log/orw-backup.log 2>&1

# Clean backups older than 30 days
0 4 * * 0 find /opt/openradiusweb/backups -mtime +30 -delete
```

### 1.6 Offsite Backup

```bash
# Copy backups to remote server
rsync -avz /opt/openradiusweb/backups/ backup-server:/backups/openradiusweb/

# Or upload to S3-compatible storage
aws s3 sync /opt/openradiusweb/backups/ s3://your-bucket/openradiusweb-backups/
```

---

## 2. Restore Procedures

### 2.1 Restore Database from Backup

#### Using Custom Format (.dump)

```bash
# Stop application services (keep database running)
docker compose -f docker-compose.prod.yml stop gateway discovery \
  device_inventory policy_engine switch_mgmt freeradius \
  freeradius_config_watcher coa_service frontend

# Drop and recreate database
docker exec orw-postgres dropdb -U orw orw
docker exec orw-postgres createdb -U orw orw

# Restore from backup
docker exec -i orw-postgres pg_restore -U orw -d orw \
  < /opt/openradiusweb/backups/orw_db_YYYYMMDD_HHMMSS.dump

# Restart all services
docker compose -f docker-compose.prod.yml up -d
```

#### Using SQL Format (.sql or .sql.gz)

```bash
# Stop application services
docker compose -f docker-compose.prod.yml stop gateway discovery \
  device_inventory policy_engine switch_mgmt

# Drop and recreate database
docker exec orw-postgres dropdb -U orw orw
docker exec orw-postgres createdb -U orw orw

# Restore from SQL backup
gunzip -c /opt/openradiusweb/backups/orw_full_YYYYMMDD.sql.gz | \
  docker exec -i orw-postgres psql -U orw -d orw

# Restart all services
docker compose -f docker-compose.prod.yml up -d
```

### 2.2 Restore Certificates

```bash
# Stop FreeRADIUS
docker compose -f docker-compose.prod.yml stop freeradius freeradius_config_watcher

# Restore certificates to volume
docker run --rm \
  -v openradiusweb_freeradius_certs:/certs \
  -v /opt/openradiusweb/backups:/backup \
  alpine sh -c "rm -rf /certs/* && tar xzf /backup/orw_certs_YYYYMMDD.tar.gz -C /certs"

# Restart FreeRADIUS
docker compose -f docker-compose.prod.yml up -d freeradius freeradius_config_watcher
```

### 2.3 Restore Configuration

```bash
# Restore .env.production and docker-compose
tar xzf /opt/openradiusweb/backups/orw_config_YYYYMMDD.tar.gz \
  -C /opt/openradiusweb/
```

---

## 3. Disaster Recovery

### 3.1 Disaster Recovery Plan

| Scenario | RPO | RTO | Recovery Steps |
|----------|-----|-----|---------------|
| Service crash | 0 | 2 min | Docker auto-restart (restart: always) |
| Data corruption | Last backup | 15 min | Restore from pg_dump backup |
| Server failure | Last backup | 1 hour | Deploy to new server + restore |
| Volume loss | Last backup | 30 min | Restore volumes from backup |

### 3.2 Complete Server Recovery

If the entire server is lost:

**Step 1: Prepare New Server**
```bash
# Install Docker (see Deployment Guide Section 2.2)
sudo apt update && sudo apt install -y docker-ce docker-compose-plugin
sudo mkdir -p /opt/openradiusweb
```

**Step 2: Transfer Application Code**
```bash
# Option A: From Git
cd /opt && git clone https://github.com/YOUR_ORG/openradiusweb.git

# Option B: From backup
tar xzf orw_code_YYYYMMDD.tar.gz -C /opt/
```

**Step 3: Restore Configuration**
```bash
cp .env.production.backup /opt/openradiusweb/.env.production
chmod 600 /opt/openradiusweb/.env.production
```

**Step 4: Build and Start Infrastructure**
```bash
cd /opt/openradiusweb
docker compose -f docker-compose.prod.yml --env-file .env.production up -d postgres redis nats
sleep 15
```

**Step 5: Restore Database**
```bash
docker exec -i orw-postgres pg_restore -U orw -d orw < orw_db_backup.dump
```

**Step 6: Restore Certificates**
```bash
docker run --rm \
  -v openradiusweb_freeradius_certs:/certs \
  -v $(pwd):/backup \
  alpine tar xzf /backup/orw_certs_backup.tar.gz -C /certs
```

**Step 7: Start All Services**
```bash
docker compose -f docker-compose.prod.yml --env-file .env.production up -d
```

**Step 8: Verify**
```bash
curl http://localhost:8000/health
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"OpenNAC2026"}'
```

### 3.3 Rolling Back a Bad Deployment

```bash
# Stop current services
docker compose -f docker-compose.prod.yml down

# Restore previous code version
git checkout previous-version  # or restore from backup

# Restore database if needed
docker exec -i orw-postgres pg_restore -U orw -d orw --clean < previous_backup.dump

# Rebuild and restart
docker compose -f docker-compose.prod.yml up -d --build
```

---

## 4. Troubleshooting Guide

### 4.1 Container Won't Start

**Symptoms:** Container repeatedly restarts or exits immediately.

**Diagnosis:**
```bash
# Check container status
docker compose -f docker-compose.prod.yml ps

# Check logs
docker logs orw-gateway --tail=100

# Check container details
docker inspect orw-gateway --format '{{.State.ExitCode}}'
```

**Common Causes:**

| Cause | Symptom | Fix |
|-------|---------|-----|
| Port conflict | "Bind for port failed" | `sudo lsof -i :8000` and stop conflicting process |
| Missing env var | "KeyError" or "config error" | Check `.env.production` completeness |
| Build failed | Container image missing | `docker compose build --no-cache <service>` |
| Disk full | Various errors | `docker system prune -a` to free space |
| DB not ready | "Connection refused" | Wait for postgres healthcheck or restart |

### 4.2 API Returns 500 Internal Server Error

**Diagnosis:**
```bash
docker logs orw-gateway --tail=50
```

**Common Causes:**

| Error | Cause | Fix |
|-------|-------|-----|
| `asyncpg.ConnectionRefused` | PostgreSQL down | `docker restart orw-postgres` |
| `relation "xxx" does not exist` | Missing migration | Run migration SQL files |
| `column "xxx" does not exist` | Schema mismatch | Run latest migration |
| `permission denied` | Wrong DB credentials | Check DB_PASSWORD in .env |

### 4.3 Web UI Shows Blank Page or 502

**Diagnosis:**
```bash
docker logs orw-frontend --tail=50
docker logs orw-gateway --tail=50
```

**Fixes:**
```bash
# Rebuild frontend
docker compose -f docker-compose.prod.yml build --no-cache frontend
docker compose -f docker-compose.prod.yml up -d frontend
```

### 4.4 RADIUS Authentication Fails

**Diagnosis:**
```bash
# Check FreeRADIUS logs
docker logs orw-freeradius --tail=100

# Test from server
docker exec orw-freeradius radtest testuser testpass 127.0.0.1 0 testing123
```

**Common Issues:**

| Issue | Cause | Fix |
|-------|-------|-----|
| "No matching client" | NAS IP not registered | Add NAS client in Web UI |
| "Shared secret mismatch" | Wrong shared secret | Update NAS client secret |
| "LDAP bind failed" | Wrong LDAP credentials | Check LDAP server config |
| "Certificate error" | Missing/expired cert | Generate new certificate |
| "rlm_python" error | Python module crash | Check `docker logs orw-freeradius` |

### 4.5 LDAP Connection Errors

**Diagnosis:**
```bash
# Test LDAP from gateway container
docker exec orw-gateway python3 -c "
import ldap3
s = ldap3.Server('YOUR_LDAP_HOST', port=389)
c = ldap3.Connection(s, 'CN=svc,DC=corp,DC=local', 'password', auto_bind=True)
c.search('DC=corp,DC=local', '(sAMAccountName=testuser)', attributes=['memberOf'])
print(c.entries)
"
```

**Common Fixes:**
- Verify LDAP server is reachable: `docker exec orw-gateway ping LDAP_HOST`
- Check bind DN format (must be full DN, not UPN)
- Verify firewall allows port 389/636 outbound

### 4.6 Dynamic VLAN Not Working

**Symptoms:** Users authenticate successfully but get the default VLAN instead of group-based VLAN.

**Diagnosis:**
```bash
# Check if ldap3 is installed
docker exec orw-freeradius python3 -c "import ldap3; print(ldap3.__version__)"

# Check FreeRADIUS logs for VLAN assignment
docker logs orw-freeradius 2>&1 | grep "Dynamic VLAN"

# Check group_vlan_mappings table
docker exec orw-postgres psql -U orw -d orw -c \
  "SELECT group_name, vlan_id, priority, enabled FROM group_vlan_mappings ORDER BY priority;"
```

**Common Fixes:**
- Verify group names match exactly (case-sensitive)
- Ensure mappings are enabled
- Rebuild freeradius if ldap3 is missing: `docker compose build --no-cache freeradius`

### 4.7 NATS JetStream Consumer Errors

**Symptoms:** Services fail with "consumer already exists" or "stale consumer" errors.

**Fix:**
```bash
# Nuclear option: reset NATS data
docker compose -f docker-compose.prod.yml down
docker volume rm openradiusweb_nats_data
docker compose -f docker-compose.prod.yml up -d
```

### 4.8 High Memory Usage

**Diagnosis:**
```bash
docker stats --no-stream
```

**Fixes:**
- PostgreSQL: Tune `shared_buffers` and `work_mem`
- Redis: Already limited to 512MB via `--maxmemory`
- Gateway: Check for memory leaks in logs
- System: `docker system prune` to clean unused images/layers

### 4.9 Slow API Response

**Diagnosis:**
```bash
# Check database query performance
docker exec orw-postgres psql -U orw -d orw -c "
SELECT query, calls, mean_exec_time, total_exec_time
FROM pg_stat_statements
ORDER BY total_exec_time DESC LIMIT 10;"
```

**Fixes:**
- Add missing indexes for frequently queried columns
- Enable TimescaleDB retention policies for old data
- Increase PostgreSQL connection pool size

### 4.10 Recovering from Accidental Data Deletion

```bash
# If you have a recent backup:
# 1. Identify the backup closest to before the deletion
ls -la /opt/openradiusweb/backups/

# 2. Restore just the affected tables (example: policies)
docker exec -i orw-postgres pg_restore -U orw -d orw \
  --data-only -t policies < backup.dump

# If no backup exists:
# Check the audit log for the deleted records
docker exec orw-postgres psql -U orw -d orw -c "
SELECT details, timestamp FROM audit_log
WHERE action = 'delete' AND resource_type = 'policy'
ORDER BY timestamp DESC LIMIT 10;"
```

---

## Appendix A: Backup Checklist

Before any major change (upgrade, migration, config change):

- [ ] Database backup: `docker exec orw-postgres pg_dump -U orw -d orw -Fc > backup.dump`
- [ ] Certificate backup: volume export
- [ ] Config backup: `.env.production` copy
- [ ] Note current container versions: `docker compose ps`
- [ ] Verify backup integrity: `pg_restore --list backup.dump`

## Appendix B: Log File Reference

| Container | Log Command | Content |
|-----------|-------------|---------|
| orw-gateway | `docker logs orw-gateway` | API requests, errors, audit |
| orw-freeradius | `docker logs orw-freeradius` | RADIUS auth attempts, VLAN assignment |
| orw-postgres | `docker logs orw-postgres` | SQL errors, slow queries |
| orw-discovery | `docker logs orw-discovery` | Device scan results |
| orw-device-inventory | `docker logs orw-device-inventory` | Device upsert events |
| orw-policy-engine | `docker logs orw-policy-engine` | Policy evaluation results |
| orw-switch-mgmt | `docker logs orw-switch-mgmt` | SNMP/SSH operations |
| orw-coa | `docker logs orw-coa` | CoA request/response |
| orw-freeradius-config-watcher | `docker logs orw-freeradius-config-watcher` | Config changes, reloads |
| orw-frontend | `docker logs orw-frontend` | Nginx access/error logs |

## Appendix C: Health Check Commands

```bash
#!/bin/bash
# healthcheck.sh - Quick system health check
echo "=== OpenRadiusWeb Health Check ==="

# Infrastructure
docker exec orw-postgres pg_isready -U orw > /dev/null 2>&1 && echo "  [OK] PostgreSQL" || echo "  [FAIL] PostgreSQL"
docker exec orw-redis redis-cli ping > /dev/null 2>&1 && echo "  [OK] Redis" || echo "  [FAIL] Redis"
curl -sf http://127.0.0.1:8222/healthz > /dev/null 2>&1 && echo "  [OK] NATS" || echo "  [FAIL] NATS"

# Application
curl -sf http://127.0.0.1:8000/health > /dev/null 2>&1 && echo "  [OK] API Gateway" || echo "  [FAIL] API Gateway"
curl -sf -o /dev/null http://127.0.0.1:8888 2>/dev/null && echo "  [OK] Web UI" || echo "  [FAIL] Web UI"

# Container count
RUNNING=$(docker compose -f /opt/openradiusweb/docker-compose.prod.yml ps --status running -q 2>/dev/null | wc -l)
echo "  Containers running: $RUNNING/12"
```

---

> **Version:** 1.0
> **Last Updated:** 2026-04-23
