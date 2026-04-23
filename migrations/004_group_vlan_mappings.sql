-- Migration 004: Group-to-VLAN mappings for Dynamic VLAN Assignment
-- Maps AD/LDAP groups to VLANs so FreeRADIUS can assign VLANs based on user group membership

CREATE TABLE IF NOT EXISTS group_vlan_mappings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    group_name VARCHAR(255) NOT NULL,
    vlan_id INTEGER NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100,
    description TEXT,
    ldap_server_id UUID REFERENCES ldap_servers(id) ON DELETE SET NULL,
    enabled BOOLEAN NOT NULL DEFAULT true,
    tenant_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(group_name, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_group_vlan_mappings_tenant
    ON group_vlan_mappings(tenant_id);
CREATE INDEX IF NOT EXISTS idx_group_vlan_mappings_enabled
    ON group_vlan_mappings(enabled) WHERE enabled = true;
CREATE INDEX IF NOT EXISTS idx_group_vlan_mappings_priority
    ON group_vlan_mappings(tenant_id, priority) WHERE enabled = true;

-- Seed some example mappings (disabled by default)
-- Uncomment and adapt to your AD group names:
-- INSERT INTO group_vlan_mappings (group_name, vlan_id, priority, description, tenant_id)
-- SELECT 'Domain Admins', 100, 10, 'Admin management VLAN', id FROM tenants LIMIT 1;
-- INSERT INTO group_vlan_mappings (group_name, vlan_id, priority, description, tenant_id)
-- SELECT 'IT-Staff', 10, 20, 'Corporate VLAN for IT', id FROM tenants LIMIT 1;
-- INSERT INTO group_vlan_mappings (group_name, vlan_id, priority, description, tenant_id)
-- SELECT 'Domain Users', 20, 90, 'Guest/standard user VLAN', id FROM tenants LIMIT 1;
