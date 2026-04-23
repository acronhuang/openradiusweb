-- Migration 003: Add operational columns to vlans + create mab_devices table
BEGIN;

-- Add operational columns to vlans table
ALTER TABLE vlans ADD COLUMN IF NOT EXISTS enabled BOOLEAN DEFAULT true;
ALTER TABLE vlans ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE vlans ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();

-- MAB (MAC Authentication Bypass) device whitelist
CREATE TABLE IF NOT EXISTS mab_devices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mac_address MACADDR NOT NULL,
    name VARCHAR(255),
    description TEXT,
    device_type VARCHAR(100),
    assigned_vlan_id INTEGER,
    enabled BOOLEAN DEFAULT true,
    expiry_date TIMESTAMPTZ,
    tenant_id UUID REFERENCES tenants(id),
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(mac_address, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_mab_devices_mac ON mab_devices(mac_address);
CREATE INDEX IF NOT EXISTS idx_mab_devices_enabled ON mab_devices(enabled) WHERE enabled = true;
CREATE INDEX IF NOT EXISTS idx_mab_devices_tenant ON mab_devices(tenant_id);

-- Seed default VLANs (only if vlans table is empty)
INSERT INTO vlans (vlan_id, name, description, purpose, tenant_id, enabled)
SELECT v.vlan_id, v.name, v.description, v.purpose,
       (SELECT id FROM tenants LIMIT 1),
       true
FROM (VALUES
    (10, 'Corporate', 'Authenticated corporate devices', 'corporate'),
    (20, 'Guest', 'Guest and unauthenticated devices', 'guest'),
    (30, 'IoT', 'IoT devices (sensors, cameras, etc.)', 'iot'),
    (40, 'Printer', 'Network printers and MFPs', 'printer'),
    (98, 'Remediation', 'Non-compliant devices for remediation', 'remediation'),
    (99, 'Quarantine', 'Quarantined devices', 'quarantine'),
    (100, 'VoIP', 'Voice over IP devices', 'voip')
) AS v(vlan_id, name, description, purpose)
WHERE NOT EXISTS (SELECT 1 FROM vlans LIMIT 1);

COMMIT;
