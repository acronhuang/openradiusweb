-- OpenRadiusWeb Seed Data for Development
-- Creates admin user and sample data

-- Default admin user (change password immediately after first login)
INSERT INTO users (username, email, password_hash, role, tenant_id)
VALUES (
    'admin',
    'admin@orw.local',
    -- bcrypt hash of default password — see deployment guide
    '$2b$12$KC/fBkYpOBHmqZuYIFcYK.bKhlSyU7wNnbtMZwF4VVgTBOoozYRbG',
    'admin',
    (SELECT id FROM tenants WHERE name = 'default')
) ON CONFLICT (username) DO NOTHING;

-- Sample VLANs
INSERT INTO vlans (vlan_id, name, purpose, subnet, tenant_id) VALUES
(1, 'Default', 'corporate', '10.0.1.0/24', (SELECT id FROM tenants WHERE name = 'default')),
(10, 'Corporate', 'corporate', '10.0.10.0/24', (SELECT id FROM tenants WHERE name = 'default')),
(20, 'Guest', 'guest', '10.0.20.0/24', (SELECT id FROM tenants WHERE name = 'default')),
(30, 'IoT', 'iot', '10.0.30.0/24', (SELECT id FROM tenants WHERE name = 'default')),
(99, 'Quarantine', 'quarantine', '10.0.99.0/24', (SELECT id FROM tenants WHERE name = 'default')),
(100, 'VoIP', 'voip', '10.0.100.0/24', (SELECT id FROM tenants WHERE name = 'default'))
ON CONFLICT DO NOTHING;

-- Sample policy: Quarantine unknown devices
INSERT INTO policies (name, description, priority, conditions, match_actions, no_match_actions, tenant_id)
VALUES (
    'Quarantine Unknown Devices',
    'Move unclassified devices to quarantine VLAN',
    200,
    '[{"field": "device_type", "operator": "equals", "value": null}]'::jsonb,
    '[{"type": "vlan_assign", "params": {"vlan_id": 99}}]'::jsonb,
    '[]'::jsonb,
    (SELECT id FROM tenants WHERE name = 'default')
) ON CONFLICT DO NOTHING;

-- Sample policy: Corporate devices get full access
INSERT INTO policies (name, description, priority, conditions, match_actions, no_match_actions, tenant_id)
VALUES (
    'Corporate Device Access',
    'Authenticated corporate devices get corporate VLAN',
    100,
    '[{"field": "status", "operator": "equals", "value": "authenticated"}, {"field": "device_type", "operator": "in", "value": ["workstation", "server"]}]'::jsonb,
    '[{"type": "vlan_assign", "params": {"vlan_id": 10}}, {"type": "acl_apply", "params": {"acl": "full_access"}}]'::jsonb,
    '[]'::jsonb,
    (SELECT id FROM tenants WHERE name = 'default')
) ON CONFLICT DO NOTHING;
