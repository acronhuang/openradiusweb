-- 006: per-device SSH credentials on network_devices
--
-- The `ssh_credential_ref VARCHAR(255)` column was added in init.sql
-- as a placeholder for "look up SSH creds in HashiCorp Vault by this
-- reference string." The Vault integration was deferred indefinitely
-- (security_audit-2026-05-02 §5 → DECIDED-NOT-DOING 2026-05-04 in
-- the strategic backlog). Result: ssh_manager._get_ssh_credentials
-- returned hardcoded empty creds and the policy_engine's `bounce_port`
-- action chain has been silently failing whenever fired.
--
-- This migration adds two real columns matching the LDAP / NAS / SNMP
-- pattern from PRs #71-#74 — username in plaintext, password as
-- AES-256-GCM ciphertext via orw_common.secrets:
--
--   ssh_username           VARCHAR(64)   nullable
--   ssh_password_encrypted TEXT          nullable
--
-- Old `ssh_credential_ref` column is left in place (no `DROP COLUMN`)
-- to keep this migration purely additive — older code reading the
-- column won't break, and we can drop it in a later migration once
-- nothing references it. Currently nothing in services/ references
-- ssh_credential_ref; safe to drop in a follow-up.
--
-- Idempotent: ALTER TABLE ... ADD COLUMN IF NOT EXISTS is PG 9.6+.

BEGIN;

ALTER TABLE network_devices
    ADD COLUMN IF NOT EXISTS ssh_username VARCHAR(64),
    ADD COLUMN IF NOT EXISTS ssh_password_encrypted TEXT;

COMMENT ON COLUMN network_devices.ssh_username IS
    'SSH login user for switch_mgmt port-bounce / VLAN-via-CLI actions. '
    'Plaintext (low-sensitivity); the password is encrypted separately.';
COMMENT ON COLUMN network_devices.ssh_password_encrypted IS
    'AES-256-GCM ciphertext of the SSH login password. '
    'Decrypt via orw_common.secrets.decrypt_secret. '
    'Migration 006 (PR #100). Operators must NEVER write plaintext here — '
    'the encrypted-columns-wrapped pre-commit hook (PR #82) enforces this.';

COMMIT;
