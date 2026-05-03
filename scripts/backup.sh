#!/bin/bash
# OpenRadiusWeb backup script.
#
# Backs up everything you'd need to restore a deployment to its current
# state on a fresh host:
#   - postgres dump (orw db, schema + data)
#   - freeradius_certs volume (CA + server cert + key — DECRYPTED PEM
#     on disk, so the backup MUST be encrypted at rest)
#   - freeradius_config volume (generated radius configs)
#   - .env.production (DB / Redis / JWT secrets, plus ORW_SECRET_MASTER
#     which decrypts every encrypted DB column — leaking this defeats
#     all the column-level encryption from PRs #70-#74)
#
# Output:
#   - backups/orw-backup-YYYY-MM-DD_HHMMSS.tar.gz.gpg  (encrypted, default)
#   - backups/orw-backup-YYYY-MM-DD_HHMMSS.tar.gz       (plaintext, only
#                                                        if ORW_BACKUP_PASSPHRASE
#                                                        not set + you ack
#                                                        the loud warning)
#
# Encryption: AES-256 via `gpg --symmetric`, passphrase from
# ORW_BACKUP_PASSPHRASE env var (read from .env.production by default).
# Generate one via:
#     python3 -c "import secrets; print(secrets.token_urlsafe(32))"
# Add to .env.production as:
#     ORW_BACKUP_PASSPHRASE=<output>
# Treat with the same care as ORW_SECRET_MASTER — if you lose it,
# every encrypted backup becomes unrecoverable.
#
# Usage (from /opt/openradiusweb):
#     sudo ./scripts/backup.sh                  # encrypted .tar.gz.gpg
#     sudo ORW_BACKUP_ALLOW_PLAINTEXT=1 ./scripts/backup.sh
#                                               # plaintext (NOT for prod)
#
# Cron example (daily at 02:30):
#     30 2 * * *  cd /opt/openradiusweb && ./scripts/backup.sh >> /var/log/orw-backup.log 2>&1

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# Source .env.production so ORW_BACKUP_PASSPHRASE is available.
# `set -a` makes everything sourced into env; `set +a` resumes normal.
if [ -f "$REPO_ROOT/.env.production" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$REPO_ROOT/.env.production"
    set +a
fi

BACKUP_DIR="$REPO_ROOT/backups"
TIMESTAMP="$(date +%Y-%m-%d_%H%M%S)"
WORK_DIR="$(mktemp -d)"
TARBALL="$WORK_DIR/orw-backup-${TIMESTAMP}.tar.gz"
ARCHIVE_FINAL=""  # set after encryption decision below

mkdir -p "$BACKUP_DIR"

cleanup() {
    rm -rf "$WORK_DIR"
}
trap cleanup EXIT

echo "=== OpenRadiusWeb backup ==="

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
# Build the unencrypted tarball first (in WORK_DIR which we delete on
# exit) — encryption happens in a separate step so a gpg failure
# doesn't leave plaintext behind in BACKUP_DIR.
tar czf "$TARBALL" -C "$WORK_DIR" \
    --exclude="$(basename "$TARBALL")" \
    .

# === Encrypt (or refuse to leave plaintext) ===
if [ -n "${ORW_BACKUP_PASSPHRASE:-}" ]; then
    if ! command -v gpg >/dev/null; then
        echo "ERROR: ORW_BACKUP_PASSPHRASE is set but gpg is not installed." >&2
        echo "       Install with: sudo apt install gnupg" >&2
        exit 1
    fi
    ARCHIVE_FINAL="$BACKUP_DIR/orw-backup-${TIMESTAMP}.tar.gz.gpg"
    echo "[encrypt] gpg --symmetric AES256 -> $(basename "$ARCHIVE_FINAL")"
    gpg --symmetric --cipher-algo AES256 \
        --pinentry-mode loopback \
        --passphrase "$ORW_BACKUP_PASSPHRASE" \
        --batch --yes \
        --output "$ARCHIVE_FINAL" \
        "$TARBALL"
elif [ "${ORW_BACKUP_ALLOW_PLAINTEXT:-}" = "1" ]; then
    ARCHIVE_FINAL="$BACKUP_DIR/orw-backup-${TIMESTAMP}.tar.gz"
    echo "" >&2
    echo "================================================================" >&2
    echo "WARNING: writing PLAINTEXT backup ($(basename "$ARCHIVE_FINAL"))." >&2
    echo "         This file contains:" >&2
    echo "           - .env.production (incl. ORW_SECRET_MASTER)" >&2
    echo "           - freeradius_certs/server.key (TLS private key)" >&2
    echo "           - postgres dump (with encrypted columns intact" >&2
    echo "             but everything else readable)" >&2
    echo "         Anyone reading this file gets the keys to the kingdom." >&2
    echo "         Set ORW_BACKUP_PASSPHRASE in .env.production to encrypt." >&2
    echo "================================================================" >&2
    echo "" >&2
    cp "$TARBALL" "$ARCHIVE_FINAL"
else
    echo "" >&2
    echo "ERROR: ORW_BACKUP_PASSPHRASE not set in .env.production." >&2
    echo "       Generate one:" >&2
    echo '         python3 -c "import secrets; print(secrets.token_urlsafe(32))"' >&2
    echo "       Append to .env.production:" >&2
    echo "         ORW_BACKUP_PASSPHRASE=<output>" >&2
    echo "       Then re-run this script." >&2
    echo "" >&2
    echo "       To bypass for a one-shot dev backup (NOT for production):" >&2
    echo "         ORW_BACKUP_ALLOW_PLAINTEXT=1 ./scripts/backup.sh" >&2
    exit 1
fi

chmod 600 "$ARCHIVE_FINAL"   # contains secrets even when encrypted (file metadata)

SIZE="$(du -h "$ARCHIVE_FINAL" | cut -f1)"
echo "=== DONE: $ARCHIVE_FINAL ($SIZE) ==="
echo ""
echo "Restore on a fresh host: ./scripts/restore.sh $ARCHIVE_FINAL"
