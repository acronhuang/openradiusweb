#!/bin/bash
# OpenRadiusWeb restore script — companion to backup.sh.
#
# Restores a deployment from an orw-backup-*.tar.gz to the current host.
# Assumes:
#   - Docker + docker compose installed
#   - Repo cloned to /opt/openradiusweb (or wherever you cd into first)
#   - Postgres / freeradius containers will be (re-)created
#
# Usage:
#     sudo ./scripts/restore.sh backups/orw-backup-2026-05-01_023000.tar.gz
#
# This is destructive: it WIPES the postgres data volume and restores
# from the backup. Use with care.

set -euo pipefail

if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
    echo "Usage: $0 <orw-backup-*.tar.gz>"
    exit 1
fi

ARCHIVE="$(readlink -f "$1")"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

WORK_DIR="$(mktemp -d)"
cleanup() { rm -rf "$WORK_DIR"; }
trap cleanup EXIT

echo "=== Restoring from $ARCHIVE ==="

# === 1. Unpack archive ===
echo "[1/5] Unpacking..."
tar xzf "$ARCHIVE" -C "$WORK_DIR"
ls -la "$WORK_DIR"

# === 2. Stop application services ===
# Keep postgres running — we need it for psql restore.
echo "[2/5] Stopping app services (postgres stays up)..."
docker compose -f docker-compose.prod.yml --env-file .env.production stop \
    gateway frontend freeradius freeradius_config_watcher coa_service \
    discovery device_inventory policy_engine switch_mgmt

# === 3. Restore .env.production FIRST ===
# Restored postgres expects the original DB_PASSWORD. If we don't restore
# .env first, the restored data is unreadable by any service.
echo "[3/5] Restoring .env.production..."
cp "$REPO_ROOT/.env.production" "$REPO_ROOT/.env.production.pre-restore.$(date +%s)"
cp "$WORK_DIR/env.production" "$REPO_ROOT/.env.production"
chmod 600 "$REPO_ROOT/.env.production"

# === 4. Restore postgres data ===
echo "[4/5] Restoring postgres dump..."
# Wait for postgres to be ready
until docker exec orw-postgres pg_isready -U orw 2>/dev/null; do sleep 1; done
docker exec -i orw-postgres psql -U orw -d orw < "$WORK_DIR/orw-db.sql"

# === 5. Restore volumes (overwrites running container's view) ===
echo "[5/5] Restoring freeradius_certs + freeradius_config volumes..."
docker run --rm \
    -v openradiusweb_freeradius_certs:/data \
    -v "$WORK_DIR":/backup:ro \
    alpine sh -c 'cd /data && rm -rf ./* && tar xzf /backup/freeradius_certs.tar.gz'
docker run --rm \
    -v openradiusweb_freeradius_config:/data \
    -v "$WORK_DIR":/backup:ro \
    alpine sh -c 'cd /data && rm -rf ./* && tar xzf /backup/freeradius_config.tar.gz'

# === Bring everything back up ===
echo "=== Restoring services ==="
docker compose -f docker-compose.prod.yml --env-file .env.production up -d

echo ""
echo "=== Restore complete ==="
echo "Verify: docker compose -f docker-compose.prod.yml ps"
echo "Old .env.production saved as .env.production.pre-restore.*"
