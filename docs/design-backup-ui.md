# Design — Backup management Web UI (Phase 2)

Status: **DESIGN, not implementation.** This doc captures the
decisions that need to be made before writing code, plus a sketch of
the schema / API / UI surface. PR #102 shipped Phase 1 (systemd timer
+ env-var-driven offsite + health endpoint); Phase 2 turns the env
config into UI-editable settings + adds a history view.

Out of scope for this doc:
- Whether to do Phase 2 at all (that's a product decision; tracked in
  `openradiusweb_backlog_strategic.md`)
- Migration to a different backup tool (Borg / Restic / kopia) — keep
  using the existing `scripts/backup.sh` GPG-encrypted tarball flow

## Goals

1. **Operator changes the schedule + destination from the Web UI**, no
   SSH-into-host required for routine config.
2. **Operator sees backup history** (success / fail / size / duration)
   in a table, can re-trigger a manual run, can download a specific
   archive.
3. **Failure visibility** — operator sees the most recent failure +
   error message without `journalctl`-fu.
4. **Multiple offsite destinations** supported (SSH/rsync, S3-compatible,
   SMB/CIFS, local NAS mount). At any given time exactly one is active.

## Non-goals

- Real-time backup (incremental / continuous data protection). Phase 1's
  nightly is fine for the data shape (RADIUS auth state isn't lost on
  rollback).
- Backup verification by automatic restore (operator does this manually
  per `runbook-key-rotation.md` Phase 1 verification flow).
- Cross-region geo-replication policies. Single offsite target suffices
  for SMB scale.

## Decisions to make BEFORE writing code

Each is a real decision the implementer needs to make. Don't skip
the discussion — these shape the implementation significantly.

### D1. Scheduler architecture

| Option | Pros | Cons |
|---|---|---|
| **A. Keep systemd timer**, gateway just edits the env file the timer reads | minimal change, host owns scheduling, journalctl free | gateway needs write access to `/etc/openradiusweb/backup.env`, awkward in container |
| **B. Move scheduler into gateway** as an asyncio background task (like cert renewal in PR #93) | self-contained, no host coupling, easier UI integration | duplicates systemd functionality, gateway holds a long-running scheduler |
| **C. Scheduler in a new dedicated container** | clean separation, scales independently | extra service to operate, more complex compose |

**Recommendation: B.** Cert auto-renewal (PR #93) already proved the
pattern. A would need privileged file mounts that violate
defense-in-depth. C is overkill for one task.

### D2. Settings storage

| Option | Pros | Cons |
|---|---|---|
| **A. New DB table** `backup_settings` (singleton row per tenant) | versioned with migrations, audit log, easy to query | schema migration cost |
| **B. JSON file** at `/etc/openradiusweb/backup.json` mounted into gateway | no schema, simple | bypasses DB audit, harder to back up the backup config |
| **C. Existing `system_settings` table** (key-value) | reuse infra | nested JSON value gets awkward |

**Recommendation: A.** Audit-log compatibility (PR #82 audit trail
patterns) is worth the migration. New columns:

```sql
CREATE TABLE backup_settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    schedule_cron VARCHAR(64) NOT NULL DEFAULT '30 2 * * *',
    keep_days INTEGER NOT NULL DEFAULT 7 CHECK (keep_days >= 1),
    destination_type VARCHAR(32) NOT NULL DEFAULT 'none',
        -- 'none' | 'rsync' | 's3' | 'smb' | 'local'
    destination_config_encrypted TEXT,
        -- AES-256-GCM JSON blob; shape varies per destination_type.
        -- See orw_common.secrets pattern from PR #71-#74 + #100.
    enabled BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id)
);
```

### D3. History storage

| Option | Pros | Cons |
|---|---|---|
| **A. New DB table** `backup_runs` (one row per scheduled fire) | queryable, joinable to `users` for "who triggered manually" | unbounded growth (~365 rows/year, fine) |
| **B. Read from filesystem** (one JSON per run, glob to enumerate) | no schema | ordering / filtering harder, slower |
| **C. Use TimescaleDB hypertable** (already in use for auth_log) | auto-retention, time-series indexed | overkill for ~daily writes |

**Recommendation: A.** Predictable shape, easy joins.

```sql
CREATE TABLE backup_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    triggered_by VARCHAR(32) NOT NULL,  -- 'schedule' | 'manual' | 'api'
    triggered_user_id UUID REFERENCES users(id),  -- nullable, for 'schedule'
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    duration_seconds INTEGER,
    local_status VARCHAR(16) NOT NULL DEFAULT 'pending',
        -- 'pending' | 'ok' | 'error'
    local_archive_path TEXT,
    local_archive_size_bytes BIGINT,
    local_error TEXT,
    offsite_status VARCHAR(16),
        -- NULL when destination_type='none', else 'ok'/'error'/'skipped'
    offsite_error TEXT,
    prune_deleted_count INTEGER DEFAULT 0
);
CREATE INDEX idx_backup_runs_tenant_started ON backup_runs(tenant_id, started_at DESC);
```

### D4. Destination types — which to support in v1

| Destination | Effort | When valuable |
|---|---|---|
| **rsync over SSH** | already exists in Phase 1 | universal, works with NAS / 2nd Linux box |
| **S3-compatible** (AWS S3 / Backblaze B2 / MinIO / Wasabi) | medium (use boto3 or rclone) | cloud-native deployments |
| **SMB / CIFS** (Windows file share / Synology / QNAP) | medium (mount + cp, OR samba lib) | Windows shops |
| **Local mount** (operator already mounted NFS / iSCSI) | trivial (just `cp`) | already-managed shared storage |

**Recommendation:** v1 ship rsync + local mount only (low effort, high
coverage). S3 + SMB in v2 if operator demand emerges.

### D5. Credential storage for offsite

For rsync: SSH private key. For S3: access key + secret. For SMB:
domain\username + password. All sensitive.

| Option | Pros | Cons |
|---|---|---|
| **A. Encrypt as JSON blob in `destination_config_encrypted`** | one column, AES-256-GCM via existing pattern | UI must serialise/deserialise the type-specific shape |
| **B. Separate column per credential field** | typed, cleaner queries | many nullable columns, schema noise |
| **C. Reference to a separate `credentials` table** | DRY if other features grow encrypted creds (e.g. PR #100's ssh_password) | over-engineering for one feature |

**Recommendation: A.** Same pattern as PR #100's
`ssh_password_encrypted`. JSON shape per `destination_type`:

```json
// rsync
{"host":"nas.local","user":"orw-backup","path":"/srv/orw-backups",
 "ssh_private_key":"-----BEGIN OPENSSH..."}

// s3
{"endpoint":"s3.us-east-1.amazonaws.com","bucket":"orw-backups",
 "access_key":"AKIA...","secret_key":"..."}

// local
{"path":"/mnt/nas-backup"}
```

### D6. Manual trigger semantics

| Option | Pros | Cons |
|---|---|---|
| **A. POST /backups/runs creates a row + scheduler picks it up** | survives gateway restart | latency (next scheduler tick, up to 60s) |
| **B. POST blocks until backup completes** | immediate feedback | HTTP timeout for large backups (~5min for prod) |
| **C. POST returns 202 + websocket / SSE for progress** | best UX | implementation complexity |

**Recommendation: A.** Status visible via existing `/health/backup`
endpoint after the scheduler picks it up. UI polls
`GET /backups/runs?status=pending,running` for in-flight.

### D7. Failure notification

| Option | Pros | Cons |
|---|---|---|
| **A. UI banner only** (operator must check) | zero infra | misses operator notice |
| **B. Email via SMTP** (configured globally) | standard | SMTP config + spam filter |
| **C. Webhook** (Slack / Discord / generic POST) | flexible | per-tenant webhook config UI |

**Recommendation:** A in v1, B+C in v2 once the rest works. Operators
already check `/health/backup` per the runbook; UI banner satisfies
the "did last night work?" use case.

### D8. UI placement

- New page `/settings/backup` under SystemSettings (matches
  existing `/settings/audit-log` pattern)
- Three sections: Schedule / Destination / History
- "Trigger now" button at top (admin-only)

## API surface

```
GET  /api/v1/backups/settings
PUT  /api/v1/backups/settings
GET  /api/v1/backups/runs?page=1&page_size=50&status=
POST /api/v1/backups/runs                              # manual trigger
GET  /api/v1/backups/runs/{id}/download                # streams the archive
DELETE /api/v1/backups/runs/{id}                       # delete row + archive
```

All endpoints `require_admin` except download which can be
`get_current_user` (read-only).

## UI wireframe (text)

```
┌──────────────────────────────────────────────────────┐
│ Backup Management                  [Trigger now]     │
├──────────────────────────────────────────────────────┤
│  Schedule                                            │
│   ▢ Enabled                                          │
│   Cron expression: [30 2 * * *  ] (preview: nightly  │
│                                   at 02:30)          │
│   Local retention: [ 7 ▼ ] days                      │
├──────────────────────────────────────────────────────┤
│  Offsite destination                                 │
│   Type: ◉ none  ○ rsync  ○ local                     │
│   ┌───────── (rsync form fields appear here) ─────┐  │
│   │ Host:        [nas.local              ]        │  │
│   │ User:        [orw-backup             ]        │  │
│   │ Remote path: [/srv/orw-backups       ]        │  │
│   │ SSH key:     [───── (paste ed25519) ────]     │  │
│   │ [Test connection]    [Save]                   │  │
│   └────────────────────────────────────────────────┘  │
├──────────────────────────────────────────────────────┤
│  History          (showing last 30 runs)             │
│   When        Trigger    Local  Offsite  Size  Dur   │
│   2026-05-04  schedule   ok     ok       12MB  18s   │
│   2026-05-03  schedule   ok     ok       12MB  17s   │
│   2026-05-02  manual(a)  error  -        -     2s    │
│   …                                                  │
└──────────────────────────────────────────────────────┘
```

## Implementation phases (within Phase 2 itself)

Each is a shippable PR:

1. **Schema migration + repo + read-only settings API** (~2 days)
   - Migration 007: backup_settings + backup_runs tables
   - Repo: read settings (defaults), list runs, lookup run
   - Routes: GET /backups/settings + GET /backups/runs (admin gates)
   - Tests: integration round-trip
2. **Scheduler in gateway lifespan** (~2 days)
   - Background task reads settings, fires backup-and-rotate.sh
     (or pure-Python equivalent), writes a `backup_runs` row
   - Existing systemd timer disabled in favour of this
   - Migration: read current `/etc/openradiusweb/backup.env` once,
     seed into new table
3. **PUT settings + manual trigger** (~2 days)
   - Routes: PUT /backups/settings, POST /backups/runs
   - Encryption of credential blob per D5
   - Tests: settings round-trip, manual trigger creates pending row
4. **Download + delete archive** (~1 day)
   - Routes: GET /runs/{id}/download (StreamingResponse), DELETE
   - Cross-tenant guards
5. **Frontend** (~3-4 days)
   - New page `/settings/backup`
   - Three forms + history table
   - Per-destination-type dynamic form (rsync vs local vs … in v2)
6. **rsync test-connection endpoint** (~1 day)
   - POST /backups/test-connection — fires a dry-run to the proposed
     target without saving settings
   - UI "Test" button before Save

Total Phase 2 estimate: **~2 weeks** (10 working days).

## Risks

- **Credential leakage via API responses**: never return the encrypted
  blob in plaintext; use the `*_encrypted` column naming so the
  pre-commit hook (PR #82) catches accidental SQL writes.
- **Scheduler vs systemd timer drift**: phase 2 must explicitly
  replace the systemd timer (and document in
  `runbook-backup-cron.md` that the unit can be removed).
- **Disk fill from runaway manual triggers**: rate-limit POST
  /backups/runs (e.g. max 1 per 5 min per tenant).
- **Backup-while-rotating race**: if the scheduler fires while a
  manual one is still running, second invocation should detect via
  `pending`/`running` row + skip with audit. Don't trust filesystem
  locks alone.

## Reuse from Phase 1

- `scripts/backup-and-rotate.sh` keeps working — Phase 2's scheduler
  could either:
  - **Shell-out** to it (least change, easiest rollback)
  - **Reimplement in Python** (cleaner, easier testing, but rewrites
    the rsync orchestration)

  Recommendation: **shell-out in v1, reimplement in v2** (only if
  test pain emerges).
- `/api/v1/health/backup` endpoint stays. Phase 2 might add
  `/api/v1/health/backup-runs` for the scheduler-aware view, but
  the file-based one keeps working as a fallback.
