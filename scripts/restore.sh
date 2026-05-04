#!/bin/bash
# OpenRadiusWeb restore script — companion to backup.sh.
#
# Restores a deployment from an orw-backup-*.tar.gz[.gpg] to the
# current host. Auto-detects encryption from the file extension:
#   *.tar.gz.gpg → decrypt with ORW_BACKUP_PASSPHRASE then untar
#   *.tar.gz     → untar directly (legacy plaintext backups)
#
# Assumes:
#   - Docker + docker compose installed
#   - Repo cloned to /opt/openradiusweb (or wherever you cd into first)
#   - Postgres / freeradius containers will be (re-)created
#
# Usage:
#     sudo ./scripts/restore.sh backups/orw-backup-2026-05-01_023000.tar.gz.gpg
#     sudo ./scripts/restore.sh backups/orw-backup-2026-05-01_023000.tar.gz
#
# For encrypted backups the passphrase comes from .env.production
# (ORW_BACKUP_PASSPHRASE). If you're restoring on a fresh host where
# .env.production hasn't been written yet, export it manually:
#     ORW_BACKUP_PASSPHRASE=<paste> sudo -E ./scripts/restore.sh <archive>
#
# This is destructive: it WIPES the postgres data volume and restores
# from the backup. Use with care.

set -euo pipefail

if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
    echo "Usage: $0 <orw-backup-*.tar.gz[.gpg]>"
    exit 1
fi

ARCHIVE="$(readlink -f "$1")"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# Source .env.production for ORW_BACKUP_PASSPHRASE — needed for *.gpg
# inputs. OK if file doesn't exist (fresh host) — we'll fail later
# with a clearer message if the archive needs decryption.
if [ -f "$REPO_ROOT/.env.production" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$REPO_ROOT/.env.production"
    set +a
fi

WORK_DIR="$(mktemp -d)"
cleanup() { rm -rf "$WORK_DIR"; }
trap cleanup EXIT

echo "=== Restoring from $ARCHIVE ==="

# === 1. Decrypt (if .gpg) + unpack ===
case "$ARCHIVE" in
    *.tar.gz.gpg)
        if [ -z "${ORW_BACKUP_PASSPHRASE:-}" ]; then
            echo "ERROR: archive is encrypted (.tar.gz.gpg) but" >&2
            echo "       ORW_BACKUP_PASSPHRASE is not set." >&2
            echo "       On the original host: read it from .env.production." >&2
            echo "       Then: ORW_BACKUP_PASSPHRASE=<paste> sudo -E $0 $ARCHIVE" >&2
            exit 1
        fi
        if ! command -v gpg >/dev/null; then
            echo "ERROR: gpg not installed but archive is encrypted." >&2
            echo "       Install with: sudo apt install gnupg" >&2
            exit 1
        fi
        echo "[1/5] Decrypting + unpacking encrypted archive..."
        gpg --decrypt \
            --pinentry-mode loopback \
            --passphrase "$ORW_BACKUP_PASSPHRASE" \
            --batch --yes \
            --output "$WORK_DIR/archive.tar.gz" \
            "$ARCHIVE"
        tar xzf "$WORK_DIR/archive.tar.gz" -C "$WORK_DIR"
        rm "$WORK_DIR/archive.tar.gz"
        ;;
    *.tar.gz)
        echo "[1/5] Unpacking plaintext archive (no decryption needed)..."
        echo "      WARNING: this archive is not encrypted at rest." >&2
        tar xzf "$ARCHIVE" -C "$WORK_DIR"
        ;;
    *)
        echo "ERROR: unknown archive extension. Expected .tar.gz or .tar.gz.gpg." >&2
        exit 1
        ;;
esac
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
