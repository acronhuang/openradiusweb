-- 007: Backup management — settings + run history
--
-- Backs the Phase 2 Web UI for the backup feature (PR #102 shipped
-- the systemd-driven Phase 1; Phase 2 moves the schedule + offsite
-- destination into the DB so operators can edit via UI without SSH).
-- See docs/design-backup-ui.md for the full decision matrix.
--
-- Two tables:
--   backup_settings   one row per tenant (UNIQUE constraint enforces
--                     singleton). Holds the schedule cron + retention
--                     window + the active offsite destination type +
--                     an encrypted JSON blob with the destination
--                     config (SSH key for rsync, etc.). Defaults are
--                     "disabled, no destination" so a fresh deploy is
--                     a no-op until an admin enables it via UI.
--
--   backup_runs       one row per backup execution (scheduled or
--                     manually triggered). Tracks the same shape the
--                     existing /health/backup endpoint reports
--                     (local_status / offsite_status / archive size)
--                     plus who triggered it and when.
--
-- Idempotent: ADD-style migration with IF NOT EXISTS guards.

BEGIN;

CREATE TABLE IF NOT EXISTS backup_settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    -- Cron expression (5 fields). Default = nightly 02:30 (matches the
    -- systemd timer hardcoded in PR #102 for migration parity).
    schedule_cron VARCHAR(64) NOT NULL DEFAULT '30 2 * * *',
    keep_days INTEGER NOT NULL DEFAULT 7 CHECK (keep_days >= 1),
    -- Destination type. 'none' = local-only (matches Phase 1 default).
    -- v1 of the UI ships 'rsync' + 'local' (per docs/design-backup-ui.md
    -- D4); s3/smb defer to v2.
    destination_type VARCHAR(32) NOT NULL DEFAULT 'none'
        CHECK (destination_type IN ('none', 'rsync', 'local')),
    -- AES-256-GCM-encrypted JSON blob. Shape varies per
    -- destination_type. Decrypt via orw_common.secrets.decrypt_secret
    -- (PR #82's pre-commit hook enforces wrapping). NULL when
    -- destination_type='none'.
    destination_config_encrypted TEXT,
    -- Master switch. False = scheduler does not fire even if a cron
    -- expression is set (lets operator pause without losing settings).
    enabled BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id)
);

COMMENT ON TABLE backup_settings IS
    'Per-tenant backup schedule + destination config. Singleton per '
    'tenant via UNIQUE(tenant_id). PR #104 (Phase 2 sub-PR 1).';
COMMENT ON COLUMN backup_settings.destination_config_encrypted IS
    'AES-256-GCM JSON blob. Shape per destination_type — see '
    'docs/design-backup-ui.md D5. NEVER write plaintext here; the '
    'encrypted-columns-wrapped pre-commit hook (PR #82) enforces this.';

CREATE TABLE IF NOT EXISTS backup_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    triggered_by VARCHAR(32) NOT NULL
        CHECK (triggered_by IN ('schedule', 'manual', 'api')),
    -- Nullable for triggered_by='schedule' (no human user).
    triggered_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    duration_seconds INTEGER,
    -- 'pending' = inserted but scheduler hasn't picked up yet (manual
    -- trigger flow per D6); 'running' = in flight; 'ok' / 'error' = done.
    local_status VARCHAR(16) NOT NULL DEFAULT 'pending'
        CHECK (local_status IN ('pending', 'running', 'ok', 'error')),
    local_archive_path TEXT,
    local_archive_size_bytes BIGINT,
    local_error TEXT,
    -- NULL when destination_type='none' or local_status != 'ok'.
    offsite_status VARCHAR(16)
        CHECK (offsite_status IS NULL OR offsite_status IN
               ('skipped', 'pending', 'running', 'ok', 'error')),
    offsite_error TEXT,
    prune_deleted_count INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_backup_runs_tenant_started
    ON backup_runs(tenant_id, started_at DESC);
-- Also index for the "currently in flight" check the scheduler uses
-- to avoid double-fire (D6 race-condition guard in design doc Risks).
CREATE INDEX IF NOT EXISTS idx_backup_runs_pending_running
    ON backup_runs(tenant_id, local_status)
    WHERE local_status IN ('pending', 'running');

COMMENT ON TABLE backup_runs IS
    'Per-execution history of backup runs (scheduled + manual). '
    'Joined to backup_settings via tenant_id. PR #104.';

-- Trigger to keep updated_at in sync — cribbed from existing
-- migrations/init.sql convention.
CREATE TRIGGER set_backup_settings_updated_at
    BEFORE UPDATE ON backup_settings
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

COMMIT;
