#!/bin/bash
# OpenRadiusWeb scheduled backup wrapper.
#
# Wraps scripts/backup.sh with three operational concerns the bare
# script doesn't handle:
#
#   1. Offsite replication via SSH+rsync, controlled by env var
#      ORW_BACKUP_OFFSITE_TARGET (e.g. "user@nas.local:/srv/orw-backups").
#      Empty / unset → skip; backup is local-only.
#   2. Local retention: prune backups older than ORW_BACKUP_KEEP_DAYS
#      (default 7) so the disk doesn't fill.
#   3. Health metadata: write the latest run's status to a JSON file
#      that the gateway's /api/v1/health/backup endpoint can read.
#
# Designed to be called by a systemd timer (see systemd/orw-backup.*).
# Always exits 0 from the local-backup phase if the tarball was
# created — offsite + prune failures are reported in the metadata
# but don't cascade into systemd "service failed" red. This keeps the
# health endpoint as the single source of truth for "is backup OK".
#
# Logs go to stdout/stderr → journalctl when run from systemd.
#
# Usage (from /opt/openradiusweb):
#     sudo ./scripts/backup-and-rotate.sh

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

BACKUP_DIR="$REPO_ROOT/backups"
STATUS_FILE="$BACKUP_DIR/.last-status.json"
KEEP_DAYS="${ORW_BACKUP_KEEP_DAYS:-7}"
OFFSITE_TARGET="${ORW_BACKUP_OFFSITE_TARGET:-}"

mkdir -p "$BACKUP_DIR"

START_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
START_EPOCH="$(date -u +%s)"

# Capture status pieces we'll merge into the JSON at the end.
LOCAL_STATUS="unknown"
LOCAL_ERROR=""
ARCHIVE_PATH=""
ARCHIVE_SIZE=0
OFFSITE_STATUS="skipped"
OFFSITE_ERROR=""
PRUNE_DELETED=0

# ---------------------------------------------------------------------
# Step 1: local backup (delegates to existing backup.sh)
# ---------------------------------------------------------------------
echo "=== Step 1/3: local backup ==="
if "$REPO_ROOT/scripts/backup.sh" 2>&1; then
    # backup.sh prints "DONE: <path>" on success — extract the path.
    # Fall back to the most-recent file in BACKUP_DIR if the parse fails.
    ARCHIVE_PATH="$(ls -t "$BACKUP_DIR"/orw-backup-*.tar.gz* 2>/dev/null | head -1 || true)"
    if [ -n "$ARCHIVE_PATH" ] && [ -f "$ARCHIVE_PATH" ]; then
        LOCAL_STATUS="ok"
        ARCHIVE_SIZE="$(stat -c%s "$ARCHIVE_PATH" 2>/dev/null || stat -f%z "$ARCHIVE_PATH" 2>/dev/null || echo 0)"
        echo "  → $ARCHIVE_PATH ($ARCHIVE_SIZE bytes)"
    else
        LOCAL_STATUS="error"
        LOCAL_ERROR="backup.sh succeeded but no .tar.gz* found in $BACKUP_DIR"
    fi
else
    rc=$?
    LOCAL_STATUS="error"
    LOCAL_ERROR="backup.sh exited $rc — see journalctl for details"
fi

# ---------------------------------------------------------------------
# Step 2: offsite replication (only if local backup succeeded AND
# ORW_BACKUP_OFFSITE_TARGET is set)
# ---------------------------------------------------------------------
echo ""
echo "=== Step 2/3: offsite replication ==="
if [ "$LOCAL_STATUS" != "ok" ]; then
    OFFSITE_STATUS="skipped"
    OFFSITE_ERROR="local backup did not succeed"
    echo "  skipped (local backup failed)"
elif [ -z "$OFFSITE_TARGET" ]; then
    OFFSITE_STATUS="skipped"
    OFFSITE_ERROR=""
    echo "  skipped (ORW_BACKUP_OFFSITE_TARGET not set)"
else
    echo "  rsync $ARCHIVE_PATH → $OFFSITE_TARGET"
    if rsync -a --partial --timeout=600 \
            -e "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new" \
            "$ARCHIVE_PATH" "$OFFSITE_TARGET" 2>&1; then
        OFFSITE_STATUS="ok"
    else
        rc=$?
        OFFSITE_STATUS="error"
        OFFSITE_ERROR="rsync exited $rc — check SSH key + remote path"
        echo "  rsync failed (exit $rc)"
    fi
fi

# ---------------------------------------------------------------------
# Step 3: prune old local backups (>= KEEP_DAYS)
# ---------------------------------------------------------------------
echo ""
echo "=== Step 3/3: prune local backups older than $KEEP_DAYS days ==="
# Use -mtime +N (older than N*24h). +0 = older than 24h, +6 = older
# than 7d, etc. Use mtime+(KEEP_DAYS-1) so "keep 7 days" really keeps
# everything from the last 7 calendar days.
PRUNE_OLDER_THAN=$((KEEP_DAYS - 1))
if [ "$PRUNE_OLDER_THAN" -lt 0 ]; then PRUNE_OLDER_THAN=0; fi

while IFS= read -r f; do
    rm -f "$f" && PRUNE_DELETED=$((PRUNE_DELETED + 1))
done < <(find "$BACKUP_DIR" -maxdepth 1 -name 'orw-backup-*.tar.gz*' \
            -mtime +"$PRUNE_OLDER_THAN" -type f 2>/dev/null || true)

echo "  deleted $PRUNE_DELETED file(s)"

# ---------------------------------------------------------------------
# Write health metadata. The gateway's /api/v1/health/backup endpoint
# reads this file via the read-only volume mount in docker-compose.
# ---------------------------------------------------------------------
END_EPOCH="$(date -u +%s)"
DURATION=$((END_EPOCH - START_EPOCH))

cat > "$STATUS_FILE" <<EOF
{
  "started_at": "$START_TS",
  "duration_seconds": $DURATION,
  "local": {
    "status": "$LOCAL_STATUS",
    "error": $(printf '%s' "$LOCAL_ERROR" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'),
    "archive_path": $(printf '%s' "$ARCHIVE_PATH" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'),
    "archive_size_bytes": $ARCHIVE_SIZE
  },
  "offsite": {
    "status": "$OFFSITE_STATUS",
    "error": $(printf '%s' "$OFFSITE_ERROR" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'),
    "target": $(printf '%s' "$OFFSITE_TARGET" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')
  },
  "prune": {
    "keep_days": $KEEP_DAYS,
    "deleted_count": $PRUNE_DELETED
  }
}
EOF

echo ""
echo "=== Status written to $STATUS_FILE ==="
cat "$STATUS_FILE"

# Exit 0 unless the local backup itself failed. Offsite + prune
# failures are surfaced via the health endpoint, not via systemd.
if [ "$LOCAL_STATUS" = "ok" ]; then
    exit 0
else
    exit 1
fi
