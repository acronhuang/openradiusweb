-- Seed `tls_max_version` for the RADIUS category.
--
-- The original 002 migration seeded `tls_min_version=1.2` but never seeded a
-- corresponding max version, so the manager hardcoded "1.3" — and freeradius
-- itself prints a warning at startup that most 802.1X supplicants don't
-- support EAP-TLS over TLS 1.3.
--
-- After this migration + the manager change in PR #56, the System Settings UI
-- can edit both min and max versions; default is "1.2" because that's what
-- works with Android / iOS / older Windows out of the box.

INSERT INTO system_settings
    (category, setting_key, setting_value, value_type, description) VALUES
    ('radius', 'tls_max_version', '1.2', 'string',
     'Maximum TLS version for EAP-TLS/PEAP. Most 802.1X supplicants do not '
     'support TLS 1.3 — keep at 1.2 unless your environment is verified.')
ON CONFLICT (category, setting_key, tenant_id) DO NOTHING;
