#!/bin/bash
# OpenRadiusWeb backup script.
#
# Backs up everything you'd need to restore a deployment to its current
# state on a fresh host:
#   - postgres dump (orw db, schema + data)
#   - freeradius_certs volume (CA + server cert + key)
#   - freeradius_config volume (generated radius configs)
#   - .env.production (DB / Redis / JWT secrets — without these the
#     restored DB password won't match what services try to use)
#
# Output: backups/orw-backup-YYYY-MM-DD_HHMMSS.tar.gz
#
# Usage (from /opt/openradiusweb):
#     sudo ./scripts/backup.sh
#
# Cron example (daily at 02:30):
#     30 2 * * *  cd /opt/openradiusweb && ./scripts/backup.sh >> /var/log/orw-backup.log 2>&1

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

BACKUP_DIR="$REPO_ROOT/backups"
TIMESTAMP="$(date +%Y-%m-%d_%H%M%S)"
WORK_DIR="$(mktemp -d)"
ARCHIVE="$BACKUP_DIR/orw-backup-${TIMESTAMP}.tar.gz"

mkdir -p "$BACKUP_DIR"

cleanup() {
    rm -rf "$WORK_DIR"
}
trap cleanup EXIT

echo "=== OpenRadiusWeb backup -> $ARCHIVE ==="

# === 1. PostgreSQL dump ===
# pg_dump > .sql preserves DB across postgres major versions and host moves.
# Raw volume copy would be tied to the postgres binary version.
echo "[1/4] pg_dump orw database..."
docker exec orw-postgres pg_dump -U orw -d orw --clean --if-exists \
    > "$WORK_DIR/orw-db.sql"
echo "      $(wc -l < "$WORK_DIR/orw-db.sql") lines"

# === 2. freeradius_certs volume (active CA + server cert + key) ===
# These have to survive verbatim — pg_dump won't capture them since the
# UI-generated certs live in DB but the watcher writes physical files
# that freeradius reads.
echo "[2/4] freeradius_certs volume..."
docker run --rm \
    -v openradiusweb_freeradius_certs:/data:ro \
    -v "$WORK_DIR":/backup \
    alpine tar czf /backup/freeradius_certs.tar.gz -C /data .

# === 3. freeradius_config volume (generated configs) ===
# Strictly regenerable from DB at next watcher reconciliation, but
# backing up means you can restore without waiting for the watcher
# to re-render everything from scratch.
echo "[3/4] freeradius_config volume..."
docker run --rm \
    -v openradiusweb_freeradius_config:/data:ro \
    -v "$WORK_DIR":/backup \
    alpine tar czf /backup/freeradius_config.tar.gz -C /data .

# === 4. .env.production (the secrets that everything else depends on) ===
# WITHOUT this, restored postgres expects $DB_PASSWORD but services try
# to connect with whatever's in the new .env — auth fails, nothing works.
echo "[4/4] .env.production..."
cp "$REPO_ROOT/.env.production" "$WORK_DIR/env.production"

# === Bundle it up ===
tar czf "$ARCHIVE" -C "$WORK_DIR" .
chmod 600 "$ARCHIVE"   # contains secrets

SIZE="$(du -h "$ARCHIVE" | cut -f1)"
echo "=== DONE: $ARCHIVE ($SIZE) ==="
echo ""
echo "Restore on a fresh host: see scripts/restore.sh"
