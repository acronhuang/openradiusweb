-- OpenRadiusWeb Database Schema
-- PostgreSQL 15 + TimescaleDB

-- Enable extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "citext";
CREATE EXTENSION IF NOT EXISTS "timescaledb" CASCADE;

-- ============================================================
-- Tenants (multi-tenancy support)
-- ============================================================
CREATE TABLE tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL UNIQUE,
    display_name VARCHAR(255),
    enabled BOOLEAN DEFAULT true,
    settings JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Default tenant
INSERT INTO tenants (name, display_name) VALUES ('default', 'Default Tenant');

-- ============================================================
-- Users & Authentication
-- ============================================================
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username CITEXT NOT NULL UNIQUE,
    email CITEXT,
    password_hash VARCHAR(255),
    role VARCHAR(20) NOT NULL DEFAULT 'viewer',  -- admin, operator, viewer
    tenant_id UUID REFERENCES tenants(id),
    enabled BOOLEAN DEFAULT true,
    last_login TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- Devices (endpoints)
-- ============================================================
CREATE TABLE devices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mac_address MACADDR NOT NULL,
    ip_address INET,
    hostname VARCHAR(255),
    device_type VARCHAR(100),
    os_family VARCHAR(50),
    os_version VARCHAR(100),
    vendor VARCHAR(100),
    model VARCHAR(255),
    first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status VARCHAR(20) DEFAULT 'discovered',
    risk_score INTEGER DEFAULT 0 CHECK (risk_score >= 0 AND risk_score <= 100),
    tenant_id UUID REFERENCES tenants(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(mac_address, tenant_id)
);

CREATE INDEX idx_devices_mac ON devices(mac_address);
CREATE INDEX idx_devices_ip ON devices(ip_address);
CREATE INDEX idx_devices_status ON devices(status);
CREATE INDEX idx_devices_tenant ON devices(tenant_id);
CREATE INDEX idx_devices_last_seen ON devices(last_seen);

-- ============================================================
-- Device Properties (EAV model)
-- ============================================================
CREATE TABLE device_properties (
    device_id UUID REFERENCES devices(id) ON DELETE CASCADE,
    category VARCHAR(100) NOT NULL,
    key VARCHAR(255) NOT NULL,
    value TEXT,
    source VARCHAR(50) NOT NULL DEFAULT 'discovery',
    confidence FLOAT DEFAULT 1.0 CHECK (confidence >= 0 AND confidence <= 1),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (device_id, category, key)
);

CREATE INDEX idx_device_props_category ON device_properties(category);

-- ============================================================
-- Network Devices (switches, routers, APs, firewalls)
-- ============================================================
CREATE TABLE network_devices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ip_address INET NOT NULL,
    hostname VARCHAR(255),
    vendor VARCHAR(100),
    model VARCHAR(100),
    os_version VARCHAR(100),
    device_type VARCHAR(50) NOT NULL,  -- switch, router, ap, firewall
    management_protocol VARCHAR(20) DEFAULT 'snmp',  -- snmp, ssh, api
    snmp_version VARCHAR(5) DEFAULT 'v2c',
    snmp_community_encrypted TEXT,
    ssh_credential_ref VARCHAR(255),
    coa_secret_encrypted TEXT,                -- RADIUS CoA shared secret (RFC 5176)
    coa_port INTEGER DEFAULT 3799,            -- CoA port (default 3799)
    enabled BOOLEAN DEFAULT true,
    last_polled TIMESTAMPTZ,
    poll_interval_seconds INTEGER DEFAULT 300,
    tenant_id UUID REFERENCES tenants(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(ip_address, tenant_id)
);

CREATE INDEX idx_network_devices_ip ON network_devices(ip_address);
CREATE INDEX idx_network_devices_type ON network_devices(device_type);

-- ============================================================
-- Switch Ports
-- ============================================================
CREATE TABLE switch_ports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    network_device_id UUID REFERENCES network_devices(id) ON DELETE CASCADE,
    port_name VARCHAR(50) NOT NULL,
    port_index INTEGER,
    port_type VARCHAR(20) DEFAULT 'access',  -- access, trunk, hybrid
    admin_status VARCHAR(10) DEFAULT 'up',
    oper_status VARCHAR(10) DEFAULT 'down',
    speed_mbps INTEGER,
    current_vlan INTEGER,
    assigned_vlan INTEGER,
    native_vlan INTEGER,
    poe_status VARCHAR(20),
    poe_power_mw INTEGER,
    connected_device_id UUID REFERENCES devices(id) ON DELETE SET NULL,
    last_mac_seen MACADDR,
    description VARCHAR(255),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(network_device_id, port_name)
);

CREATE INDEX idx_switch_ports_device ON switch_ports(network_device_id);
CREATE INDEX idx_switch_ports_vlan ON switch_ports(current_vlan);
CREATE INDEX idx_switch_ports_connected ON switch_ports(connected_device_id);

-- ============================================================
-- VLANs
-- ============================================================
CREATE TABLE vlans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vlan_id INTEGER NOT NULL,
    name VARCHAR(100),
    description TEXT,
    purpose VARCHAR(50),  -- corporate, guest, quarantine, iot, voip
    subnet CIDR,
    tenant_id UUID REFERENCES tenants(id),
    UNIQUE(vlan_id, tenant_id)
);

-- ============================================================
-- RADIUS Sessions
-- ============================================================
CREATE TABLE radius_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id VARCHAR(255) UNIQUE NOT NULL,
    device_id UUID REFERENCES devices(id) ON DELETE SET NULL,
    username VARCHAR(255),
    calling_station_id VARCHAR(50),  -- MAC
    called_station_id VARCHAR(50),   -- NAS port MAC
    auth_method VARCHAR(50),
    nas_ip INET,
    nas_port INTEGER,
    nas_port_type VARCHAR(50),
    nas_port_id VARCHAR(100),             -- e.g., "GigabitEthernet1/0/1"
    nas_identifier VARCHAR(255),
    assigned_vlan INTEGER,
    assigned_ip INET,
    status VARCHAR(20) DEFAULT 'active',
    terminate_cause VARCHAR(50),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    bytes_in BIGINT DEFAULT 0,
    bytes_out BIGINT DEFAULT 0,
    packets_in BIGINT DEFAULT 0,
    packets_out BIGINT DEFAULT 0
);

CREATE INDEX idx_radius_sessions_device ON radius_sessions(device_id);
CREATE INDEX idx_radius_sessions_status ON radius_sessions(status);
CREATE INDEX idx_radius_sessions_started ON radius_sessions(started_at);

-- ============================================================
-- Vulnerability Scans & Findings
-- ============================================================
CREATE TABLE vulnerability_scans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id UUID REFERENCES devices(id) ON DELETE CASCADE,
    scan_type VARCHAR(50) NOT NULL,
    scanner VARCHAR(50) NOT NULL,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    status VARCHAR(20) DEFAULT 'running',
    findings_count INTEGER DEFAULT 0,
    scan_config JSONB DEFAULT '{}'
);

CREATE TABLE vulnerabilities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scan_id UUID REFERENCES vulnerability_scans(id) ON DELETE CASCADE,
    device_id UUID REFERENCES devices(id) ON DELETE CASCADE,
    cve_id VARCHAR(20),
    severity VARCHAR(10) NOT NULL,
    cvss_score FLOAT,
    title VARCHAR(500) NOT NULL,
    description TEXT,
    remediation TEXT,
    detected_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ,
    status VARCHAR(20) DEFAULT 'open'  -- open, resolved, accepted, false_positive
);

CREATE INDEX idx_vulnerabilities_device ON vulnerabilities(device_id);
CREATE INDEX idx_vulnerabilities_severity ON vulnerabilities(severity);
CREATE INDEX idx_vulnerabilities_cve ON vulnerabilities(cve_id);

-- ============================================================
-- Compliance Checks
-- ============================================================
CREATE TABLE compliance_checks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id UUID REFERENCES devices(id) ON DELETE CASCADE,
    check_type VARCHAR(100) NOT NULL,
    check_name VARCHAR(255) NOT NULL,
    result VARCHAR(20) NOT NULL,  -- pass, fail, error, skip
    details JSONB DEFAULT '{}',
    checked_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_compliance_device ON compliance_checks(device_id);
CREATE INDEX idx_compliance_result ON compliance_checks(result);

-- ============================================================
-- Policies
-- ============================================================
CREATE TABLE policies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    description TEXT,
    priority INTEGER DEFAULT 100,
    conditions JSONB NOT NULL,
    match_actions JSONB NOT NULL,
    no_match_actions JSONB DEFAULT '[]',
    enabled BOOLEAN DEFAULT true,
    tenant_id UUID REFERENCES tenants(id),
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE policy_evaluations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    policy_id UUID REFERENCES policies(id) ON DELETE CASCADE,
    device_id UUID REFERENCES devices(id) ON DELETE CASCADE,
    result VARCHAR(20) NOT NULL,  -- match, no_match, error
    actions_taken JSONB DEFAULT '[]',
    evaluated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_policy_eval_device ON policy_evaluations(device_id);
CREATE INDEX idx_policy_eval_policy ON policy_evaluations(policy_id);

-- ============================================================
-- Events (TimescaleDB hypertable)
-- ============================================================
CREATE TABLE events (
    id UUID DEFAULT gen_random_uuid(),
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    event_type VARCHAR(50) NOT NULL,
    severity VARCHAR(10) NOT NULL DEFAULT 'info',  -- critical, high, medium, low, info
    device_id UUID,
    source VARCHAR(50) NOT NULL,
    message TEXT NOT NULL,
    details JSONB DEFAULT '{}',
    tenant_id UUID,
    PRIMARY KEY (id, timestamp)
);

SELECT create_hypertable('events', 'timestamp');

CREATE INDEX idx_events_type ON events(event_type, timestamp DESC);
CREATE INDEX idx_events_severity ON events(severity, timestamp DESC);
CREATE INDEX idx_events_device ON events(device_id, timestamp DESC);

-- ============================================================
-- Audit Log
-- ============================================================
CREATE TABLE audit_log (
    id UUID DEFAULT gen_random_uuid(),
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_id UUID,
    action VARCHAR(50) NOT NULL,
    resource_type VARCHAR(50) NOT NULL,
    resource_id UUID,
    details JSONB DEFAULT '{}',
    ip_address INET,
    tenant_id UUID,
    PRIMARY KEY (id, timestamp)
);

SELECT create_hypertable('audit_log', 'timestamp');

-- ============================================================
-- RADIUS Authentication Log (Access Tracker - like ClearPass)
-- Records every 802.1X / MAB authentication attempt with full details
-- ============================================================
CREATE TABLE radius_auth_log (
    id UUID DEFAULT gen_random_uuid(),
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Request identifiers
    session_id VARCHAR(255),
    request_type VARCHAR(20) NOT NULL,  -- Access-Request, Access-Accept, Access-Reject, Access-Challenge

    -- Authentication result
    auth_result VARCHAR(20) NOT NULL,   -- success, reject, timeout, error, challenge
    auth_method VARCHAR(50),            -- EAP-TLS, PEAP-MSCHAPv2, EAP-TTLS, MAB, PAP, CHAP
    eap_type VARCHAR(50),              -- EAP inner method details

    -- Failure details (key feature - like ClearPass failure reason)
    failure_reason VARCHAR(500),        -- Human-readable failure reason
    failure_code INTEGER,               -- RADIUS Reply-Message code
    ad_error_code VARCHAR(100),         -- AD/LDAP specific error code (e.g., 0x775 = account locked)
    ad_error_message VARCHAR(500),      -- AD/LDAP error detail message
    radius_reply_message TEXT,          -- Full RADIUS Reply-Message

    -- Client/Supplicant info
    calling_station_id VARCHAR(50),     -- Client MAC address
    username VARCHAR(255),              -- 802.1X username (e.g., user@domain.com)
    user_domain VARCHAR(255),           -- AD domain
    device_id UUID,                     -- Linked device in inventory

    -- NAS (Authenticator / Switch) info
    nas_ip INET,                        -- Switch/AP IP
    nas_port INTEGER,                   -- Physical port
    nas_port_type VARCHAR(50),          -- Ethernet, Wireless-802.11
    nas_port_id VARCHAR(100),           -- Port description (e.g., GigabitEthernet1/0/1)
    nas_identifier VARCHAR(255),        -- Switch hostname

    -- Network assignment
    assigned_vlan INTEGER,
    assigned_vlan_name VARCHAR(100),
    filter_id VARCHAR(255),             -- RADIUS Filter-Id (ACL name)

    -- Certificate info (EAP-TLS)
    client_cert_cn VARCHAR(255),
    client_cert_issuer VARCHAR(500),
    client_cert_serial VARCHAR(100),
    client_cert_expiry TIMESTAMPTZ,
    server_cert_cn VARCHAR(255),

    -- Additional context
    service_type VARCHAR(50),           -- Framed, Login, Call-Check
    framed_ip INET,                     -- Assigned IP
    source_ip INET,                     -- RADIUS client source IP
    processing_time_ms INTEGER,         -- How long authentication took
    policy_matched VARCHAR(255),        -- Which auth policy was applied

    -- Raw attributes for debugging
    request_attributes JSONB DEFAULT '{}',   -- All RADIUS request attributes
    response_attributes JSONB DEFAULT '{}',  -- All RADIUS response attributes

    tenant_id UUID,
    PRIMARY KEY (id, timestamp)
);

-- Make it a TimescaleDB hypertable for efficient time-range queries
SELECT create_hypertable('radius_auth_log', 'timestamp');

-- Indexes for common Access Tracker queries
CREATE INDEX idx_radius_auth_result ON radius_auth_log(auth_result, timestamp DESC);
CREATE INDEX idx_radius_auth_mac ON radius_auth_log(calling_station_id, timestamp DESC);
CREATE INDEX idx_radius_auth_username ON radius_auth_log(username, timestamp DESC);
CREATE INDEX idx_radius_auth_nas ON radius_auth_log(nas_ip, timestamp DESC);
CREATE INDEX idx_radius_auth_failure ON radius_auth_log(failure_reason, timestamp DESC)
    WHERE auth_result != 'success';
CREATE INDEX idx_radius_auth_method ON radius_auth_log(auth_method, timestamp DESC);

-- Partial index for quick failure-only queries
CREATE INDEX idx_radius_auth_failures_only ON radius_auth_log(timestamp DESC)
    WHERE auth_result IN ('reject', 'timeout', 'error');

-- ============================================================
-- RADIUS Failure Reason Catalog
-- Pre-defined failure reasons with troubleshooting guidance
-- ============================================================
CREATE TABLE radius_failure_catalog (
    id SERIAL PRIMARY KEY,
    failure_code VARCHAR(100) NOT NULL UNIQUE,
    category VARCHAR(50) NOT NULL,        -- credential, certificate, policy, network, system
    description VARCHAR(500) NOT NULL,
    possible_causes TEXT[],
    remediation_steps TEXT[],
    severity VARCHAR(10) DEFAULT 'medium', -- critical, high, medium, low
    kb_url VARCHAR(500)                    -- Link to knowledge base article
);

-- Pre-populate common 802.1X / AD failure reasons
INSERT INTO radius_failure_catalog (failure_code, category, description, possible_causes, remediation_steps, severity) VALUES
-- AD/LDAP Authentication Failures
('AD_INVALID_CREDENTIALS', 'credential', 'AD authentication failed: invalid username or password',
 ARRAY['User entered wrong password', 'Password expired and not yet updated on device', 'Username typo or wrong format (user vs user@domain vs DOMAIN\\user)'],
 ARRAY['Verify username format matches AD UPN or sAMAccountName', 'Reset user password in AD', 'Check if the supplicant is sending correct credentials'],
 'medium'),

('AD_ACCOUNT_DISABLED', 'credential', 'AD account is disabled',
 ARRAY['Account was disabled by administrator', 'Account auto-disabled by policy', 'User terminated but device not updated'],
 ARRAY['Enable the account in AD Users and Computers', 'Check AD account status: Get-ADUser -Identity <user> -Properties Enabled'],
 'medium'),

('AD_ACCOUNT_LOCKED', 'credential', 'AD account is locked out',
 ARRAY['Too many failed login attempts', 'Old password cached on device', 'Multiple devices trying with wrong password', 'Brute force attack'],
 ARRAY['Unlock the account: Unlock-ADAccount -Identity <user>', 'Check lockout source: Get-ADUser -Identity <user> -Properties LockedOut,BadLogonCount,LastBadPasswordAttempt', 'Review lockout threshold policy'],
 'high'),

('AD_ACCOUNT_EXPIRED', 'credential', 'AD account has expired',
 ARRAY['Account expiration date has passed', 'Temporary/contractor account expired'],
 ARRAY['Extend account expiration in AD: Set-ADAccountExpiration -Identity <user> -DateTime <date>', 'If account should remain expired, remove device from network or re-assign VLAN'],
 'medium'),

('AD_PASSWORD_EXPIRED', 'credential', 'AD password has expired',
 ARRAY['Password max age exceeded', 'User has not changed password within required period'],
 ARRAY['User needs to change password via a non-802.1X method first', 'Temporarily move to password-change VLAN', 'Reset password: Set-ADAccountPassword -Identity <user>'],
 'medium'),

('AD_LOGON_HOURS', 'credential', 'AD logon denied due to time restriction',
 ARRAY['User attempting to authenticate outside of allowed logon hours'],
 ARRAY['Check logon hours restriction in AD: Get-ADUser -Identity <user> -Properties LogonHours', 'Modify logon hours if needed'],
 'low'),

('AD_MACHINE_NOT_FOUND', 'credential', 'Computer account not found in AD',
 ARRAY['Device not domain-joined', 'Computer account deleted from AD', 'DNS/trust issue preventing computer account lookup'],
 ARRAY['Join the computer to the domain', 'Verify computer account exists: Get-ADComputer -Identity <hostname>', 'Check AD connectivity from RADIUS server'],
 'medium'),

('AD_CONNECT_FAILED', 'system', 'Cannot connect to Active Directory',
 ARRAY['AD domain controller unreachable', 'DNS resolution failure', 'LDAP port blocked (389/636)', 'AD service account password changed'],
 ARRAY['Verify DC connectivity: Test-NetConnection -ComputerName <DC> -Port 389', 'Check DNS: nslookup <domain>', 'Verify service account credentials', 'Check firewall rules for LDAP/LDAPS ports'],
 'critical'),

-- Certificate Failures (EAP-TLS)
('CERT_EXPIRED', 'certificate', 'Client certificate has expired',
 ARRAY['Certificate validity period ended', 'Auto-enrollment failed to renew'],
 ARRAY['Renew the client certificate', 'Check auto-enrollment GPO settings', 'Verify CA certificate validity chain'],
 'high'),

('CERT_NOT_TRUSTED', 'certificate', 'Client certificate issued by untrusted CA',
 ARRAY['CA certificate not in RADIUS trust store', 'Intermediate CA missing', 'Self-signed certificate'],
 ARRAY['Import the CA certificate to FreeRADIUS trust store', 'Verify full certificate chain is available', 'Check ca_file and ca_path in eap.conf'],
 'high'),

('CERT_REVOKED', 'certificate', 'Client certificate has been revoked',
 ARRAY['Certificate revoked via CRL/OCSP', 'Key compromise reported', 'User left organization'],
 ARRAY['Issue a new certificate', 'Check CRL distribution point accessibility', 'Verify OCSP responder is working'],
 'critical'),

('CERT_CN_MISMATCH', 'certificate', 'Certificate CN/SAN does not match expected identity',
 ARRAY['Certificate issued with wrong CN', 'SAN does not include required values', 'Certificate template misconfigured'],
 ARRAY['Re-issue certificate with correct CN/SAN', 'Check certificate template settings in CA'],
 'medium'),

-- EAP / Protocol Failures
('EAP_TIMEOUT', 'network', 'EAP conversation timed out',
 ARRAY['Supplicant did not respond in time', 'Network latency too high', 'Supplicant software crash', 'Switch EAP timer too short'],
 ARRAY['Increase EAP timeout on switch (dot1x timeout tx-period)', 'Check supplicant logs for errors', 'Verify network path between switch and RADIUS'],
 'medium'),

('EAP_METHOD_MISMATCH', 'policy', 'EAP method not supported or not allowed by policy',
 ARRAY['Supplicant configured for EAP-TLS but server expects PEAP', 'Server policy rejects the offered EAP method'],
 ARRAY['Align supplicant and server EAP configuration', 'Check FreeRADIUS eap.conf for allowed methods', 'Update network profile on client device'],
 'medium'),

('EAP_INNER_METHOD_FAIL', 'credential', 'EAP inner authentication (Phase 2) failed',
 ARRAY['MSCHAPv2 password mismatch', 'GTC token invalid', 'Inner method configuration mismatch'],
 ARRAY['Verify inner method matches: PEAP typically uses MSCHAPv2', 'Check user password', 'Review EAP inner method logs in detail'],
 'medium'),

-- RADIUS / NAS Failures
('NAS_NOT_AUTHORIZED', 'policy', 'RADIUS client (NAS) not authorized',
 ARRAY['Switch IP not in RADIUS clients list', 'Shared secret mismatch', 'New switch added but not configured in RADIUS'],
 ARRAY['Add NAS to FreeRADIUS clients.conf', 'Verify shared secret matches on both switch and RADIUS', 'Check if NAS IP changed (DHCP)'],
 'high'),

('SHARED_SECRET_MISMATCH', 'network', 'RADIUS shared secret mismatch between NAS and server',
 ARRAY['Secret changed on one side but not the other', 'Special characters causing encoding issues'],
 ARRAY['Reset shared secret on both switch and RADIUS server', 'Use alphanumeric-only secret to avoid encoding issues', 'Verify with: radtest user password nas-ip 0 secret'],
 'high'),

('MAB_NOT_FOUND', 'policy', 'MAC address not found in MAB database',
 ARRAY['Device MAC not registered for MAB authentication', 'MAC address format mismatch (upper/lower case, delimiter)'],
 ARRAY['Add MAC address to authorized MAB list', 'Check MAC format: AA:BB:CC:DD:EE:FF vs aabb.ccdd.eeff', 'Consider auto-registration policy for unknown MACs'],
 'low'),

('POLICY_REJECT', 'policy', 'Authentication rejected by authorization policy',
 ARRAY['User/device does not match any allow policy', 'Explicitly denied by policy rule', 'Posture/compliance check failed'],
 ARRAY['Review authorization policies', 'Check which policy was evaluated', 'Verify device compliance status'],
 'medium'),

('VLAN_ASSIGNMENT_FAILED', 'network', 'Failed to assign VLAN to session',
 ARRAY['VLAN does not exist on switch', 'VLAN assignment policy misconfigured', 'CoA failure'],
 ARRAY['Verify VLAN exists on the switch', 'Check RADIUS VLAN attributes (Tunnel-Type, Tunnel-Medium-Type, Tunnel-Private-Group-ID)', 'Test CoA connectivity'],
 'medium'),

-- System Failures
('RADIUS_INTERNAL_ERROR', 'system', 'RADIUS server internal error',
 ARRAY['FreeRADIUS process error', 'Database connection failure', 'Module loading error'],
 ARRAY['Check FreeRADIUS logs: journalctl -u freeradius', 'Verify database connectivity', 'Restart FreeRADIUS service'],
 'critical'),

('RADIUS_OVERLOADED', 'system', 'RADIUS server overloaded - request dropped',
 ARRAY['Too many concurrent authentication requests', 'Thread pool exhausted', 'Database connection pool full'],
 ARRAY['Scale RADIUS server resources', 'Increase thread pool size in radiusd.conf', 'Add additional RADIUS server for HA'],
 'critical')

ON CONFLICT DO NOTHING;

-- ============================================================
-- Helper function: update updated_at
-- ============================================================
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_devices_updated_at BEFORE UPDATE ON devices FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER trg_network_devices_updated_at BEFORE UPDATE ON network_devices FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER trg_users_updated_at BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER trg_policies_updated_at BEFORE UPDATE ON policies FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER trg_tenants_updated_at BEFORE UPDATE ON tenants FOR EACH ROW EXECUTE FUNCTION update_updated_at();
