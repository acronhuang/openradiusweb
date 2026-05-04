# Runbook — Scheduled backup install + verification

Wires `scripts/backup-and-rotate.sh` into a systemd timer that runs
nightly at 02:30. Optionally pushes the resulting tarball to a remote
host via SSH+rsync. Surfaces freshness via the gateway's
`/api/v1/health/backup` endpoint so monitoring can poll it.

This is the Phase 1 deliverable from
[`design-backup-ui.md`](design-backup-ui.md). Phase 2 (Web UI for
configuring schedule + destination + history) is a separate sprint.

## Prerequisites

- prod host has `systemd` (any reasonable Linux distro)
- `rsync` installed (apt: `rsync`)
- `python3` installed (used by `backup-and-rotate.sh` for JSON quoting
  in the status file)
- `ORW_BACKUP_PASSPHRASE` already set in `.env.production` (PR #81)

## One-time install

```bash
# 1. Copy the systemd units into place
sudo cp /opt/openradiusweb/systemd/orw-backup.service /etc/systemd/system/
sudo cp /opt/openradiusweb/systemd/orw-backup.timer   /etc/systemd/system/

# 2. Create the env file the service reads. Default config = local-only,
#    7-day retention, no offsite. Edit to taste.
sudo mkdir -p /etc/openradiusweb
sudo tee /etc/openradiusweb/backup.env <<'EOF'
# Local retention window in days (oldest archives pruned).
ORW_BACKUP_KEEP_DAYS=7

# Offsite target — rsync-compatible spec, e.g. "user@nas.local:/srv/orw-backups"
# Empty / unset → skip offsite step (local-only backups).
# Auth: passwordless SSH key in /root/.ssh/ (this service runs as root).
ORW_BACKUP_OFFSITE_TARGET=
EOF
sudo chmod 600 /etc/openradiusweb/backup.env

# 3. Enable + start the timer
sudo systemctl daemon-reload
sudo systemctl enable --now orw-backup.timer

# 4. Confirm the timer is armed
sudo systemctl list-timers orw-backup.timer
```

## Configuring offsite (when ready)

```bash
# 1. Generate an SSH key for root that you'll authorise on the
#    remote backup target. Don't reuse an admin key.
sudo ssh-keygen -t ed25519 -f /root/.ssh/orw_backup_key -N ''

# 2. Copy the public key onto the remote target's authorized_keys.
#    Use a `command="rsync ..."` restriction in the remote
#    authorized_keys to limit blast radius if the key leaks:
#
#      command="rsync --server -e.LsfxC . /srv/orw-backups/",no-pty,no-port-forwarding ssh-ed25519 AAAA... orw-backup@radius
#
#    (Adjust the rsync command to match your target path + flags.)

# 3. Tell the systemd service which target + key to use
sudo tee /etc/openradiusweb/backup.env <<'EOF'
ORW_BACKUP_KEEP_DAYS=7
ORW_BACKUP_OFFSITE_TARGET=orw-backup@nas.local:/srv/orw-backups/
EOF

# 4. Add the key to root's SSH config so rsync picks it up automatically
sudo tee -a /root/.ssh/config <<'EOF'

Host nas.local
    IdentityFile /root/.ssh/orw_backup_key
    IdentitiesOnly yes
EOF
sudo chmod 600 /root/.ssh/config
```

## Trigger an immediate run for verification

Don't wait until 02:30 — trigger now:

```bash
sudo systemctl start orw-backup.service
sudo journalctl -u orw-backup.service --since '5 min ago' --no-pager
```

Expected: 3 sections (`Step 1/3 local backup`, `Step 2/3 offsite
replication`, `Step 3/3 prune`), then `=== Status written to ...`.

## Verify the health endpoint sees it

```bash
# From the prod host (gateway is on localhost:8000):
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
    -H 'Content-Type: application/json' \
    -d '{"username":"admin","password":"OpenNAC2026"}' \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

curl -s -H "Authorization: Bearer $TOKEN" \
    http://localhost:8000/api/v1/health/backup | python3 -m json.tool
```

Expected shape:
```json
{
  "status": "ok",
  "last_run_started_at": "2026-05-04T18:30:00Z",
  "age_seconds": 12,
  "local": { "status": "ok", "archive_size_bytes": 12345678, ... },
  "offsite": { "status": "ok", ... },     // or "skipped" if not configured
  "prune": { "keep_days": 7, "deleted_count": 0 }
}
```

`status` field meanings:
- `ok` — last run succeeded within `ORW_BACKUP_STALE_AFTER_SECONDS` (default 36h)
- `stale` — last run succeeded but is older than the threshold
- `error` — last run's local backup phase failed
- `unknown` — no status file (scheduled run never happened or
  volume mount is wrong)

## Day-2 ops

### Check upcoming runs

```bash
sudo systemctl list-timers orw-backup.timer
```

### Check last run's full log

```bash
sudo journalctl -u orw-backup.service --since '1 day ago' --no-pager
```

### Manually trigger (e.g. before maintenance)

```bash
sudo systemctl start orw-backup.service
```

### Disable temporarily (e.g. during major migration)

```bash
sudo systemctl stop orw-backup.timer
# … do your thing …
sudo systemctl start orw-backup.timer
```

## Failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `/health/backup` returns `unknown` | timer hasn't fired yet, or `./backups:/var/orw-backups:ro` mount missing | check `systemctl list-timers`, recheck compose file |
| `local.status=error` in status file | encryption passphrase missing, postgres dump failed | journalctl will show the underlying error |
| `offsite.status=error` | SSH key wrong, target path doesn't exist, host unreachable | `ssh -i /root/.ssh/orw_backup_key user@host` from prod box |
| Disk filling despite retention | `KEEP_DAYS` too high OR backups arriving faster than nightly (e.g. someone manually invoking) | check `ls -la backups/`, lower `KEEP_DAYS` |

## Related

- [scripts/backup.sh](../scripts/backup.sh) — the bare backup command this wraps
- [scripts/restore.sh](../scripts/restore.sh) — restore from any tarball
- [scripts/backup-and-rotate.sh](../scripts/backup-and-rotate.sh) — the wrapper invoked by systemd
- [design-backup-ui.md](design-backup-ui.md) — Phase 2 (Web UI) design doc
