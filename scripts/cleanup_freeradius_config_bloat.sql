-- One-shot cleanup for the freeradius_config table bloat caused by the
-- pre-PR-#88 NULL-tenant_id bug. Background:
--
--   _save_config_state used to INSERT without populating tenant_id, leaving
--   it NULL. Combined with the "ON CONFLICT (config_type, config_name,
--   tenant_id)" constraint AND PostgreSQL's NULL != NULL semantics, every
--   reconcile produced a fresh row instead of updating the existing one.
--   During the SIGHUP storm (2026-05-03) the watcher reconciled every ~1s
--   and accumulated ~135k rows per config_type before the fix landed.
--
-- This script:
--   1. DELETEs every row with tenant_id IS NULL (all of which are stale
--      copies of older renders — we now write fresh ones under the
--      'default' tenant).
--   2. Reports the row counts before + after so an operator can sanity-
--      check.
--
-- Idempotent: re-running on a clean table is a no-op (DELETE matches 0
-- rows). Safe to leave around / call from a cron if needed.
--
-- Run:
--     sudo docker exec -i orw-postgres psql -U orw -d orw \
--       < scripts/cleanup_freeradius_config_bloat.sql

\echo === BEFORE cleanup ===
SELECT config_type,
       COUNT(*) AS total_rows,
       COUNT(*) FILTER (WHERE tenant_id IS NULL) AS null_tenant_rows,
       COUNT(DISTINCT last_applied_hash) AS distinct_hashes
FROM freeradius_config
GROUP BY config_type
ORDER BY config_type;

BEGIN;

DELETE FROM freeradius_config WHERE tenant_id IS NULL;

COMMIT;

\echo === AFTER cleanup ===
SELECT config_type,
       COUNT(*) AS total_rows,
       COUNT(*) FILTER (WHERE tenant_id IS NULL) AS null_tenant_rows,
       COUNT(DISTINCT last_applied_hash) AS distinct_hashes
FROM freeradius_config
GROUP BY config_type
ORDER BY config_type;

\echo === VACUUM (reclaim space) ===
VACUUM ANALYZE freeradius_config;
