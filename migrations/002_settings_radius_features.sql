-- Migration 002: Settings and RADIUS Features
-- Adds tables for LDAP, realms, certificates, NAS clients, settings, preferences, and FreeRADIUS config

BEGIN;

-- ============================================================================
-- 1. LDAP Servers
-- ============================================================================
CREATE TABLE IF NOT EXISTS ldap_servers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    description TEXT,
    host VARCHAR(255) NOT NULL,
    port INTEGER DEFAULT 389,
    use_tls BOOLEAN DEFAULT false,
    use_starttls BOOLEAN DEFAULT false,
    bind_dn VARCHAR(500) NOT NULL,
    bind_password_encrypted TEXT NOT NULL,
    base_dn VARCHAR(500) NOT NULL,
    user_search_filter VARCHAR(500) DEFAULT '(sAMAccountName=%{%{Stripped-User-Name}:-%{User-Name}})',
    user_search_base VARCHAR(500),
    group_search_filter VARCHAR(500) DEFAULT '(member=%{control:Ldap-UserDn})',
    group_search_base VARCHAR(500),
    group_membership_attr VARCHAR(100) DEFAULT 'memberOf',
    username_attr VARCHAR(100) DEFAULT 'sAMAccountName',
    display_name_attr VARCHAR(100) DEFAULT 'displayName',
    email_attr VARCHAR(100) DEFAULT 'mail',
    connect_timeout_seconds INTEGER DEFAULT 5,
    search_timeout_seconds INTEGER DEFAULT 10,
    idle_timeout_seconds INTEGER DEFAULT 60,
    tls_ca_cert TEXT,
    tls_require_cert VARCHAR(20) DEFAULT 'demand',
    priority INTEGER DEFAULT 100,
    enabled BOOLEAN DEFAULT true,
    last_test_at TIMESTAMPTZ,
    last_test_result VARCHAR(20),
    last_test_message TEXT,
    tenant_id UUID REFERENCES tenants(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(name, tenant_id)
);

CREATE TRIGGER set_ldap_servers_updated_at
    BEFORE UPDATE ON ldap_servers
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ============================================================================
-- 2. RADIUS Realms
-- ============================================================================
CREATE TABLE IF NOT EXISTS radius_realms (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    description TEXT,
    realm_type VARCHAR(20) NOT NULL DEFAULT 'local',  -- local, proxy, reject
    strip_username BOOLEAN DEFAULT true,
    proxy_host VARCHAR(255),
    proxy_port INTEGER DEFAULT 1812,
    proxy_secret_encrypted TEXT,
    proxy_nostrip BOOLEAN DEFAULT false,
    proxy_retry_count INTEGER DEFAULT 3,
    proxy_retry_delay_seconds INTEGER DEFAULT 5,
    proxy_dead_time_seconds INTEGER DEFAULT 120,
    ldap_server_id UUID REFERENCES ldap_servers(id) ON DELETE SET NULL,
    auth_types_allowed TEXT[] DEFAULT ARRAY['EAP-TLS', 'PEAP', 'EAP-TTLS', 'MAB'],
    default_vlan INTEGER,
    default_filter_id VARCHAR(255),
    fallback_realm_id UUID REFERENCES radius_realms(id) ON DELETE SET NULL,
    priority INTEGER DEFAULT 100,
    enabled BOOLEAN DEFAULT true,
    tenant_id UUID REFERENCES tenants(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(name, tenant_id)
);

CREATE TRIGGER set_radius_realms_updated_at
    BEFORE UPDATE ON radius_realms
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ============================================================================
-- 3. Certificates
-- ============================================================================
CREATE TABLE IF NOT EXISTS certificates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cert_type VARCHAR(20) NOT NULL,  -- ca, server
    name VARCHAR(255) NOT NULL,
    description TEXT,
    common_name VARCHAR(255),
    issuer VARCHAR(500),
    serial_number VARCHAR(100),
    not_before TIMESTAMPTZ,
    not_after TIMESTAMPTZ,
    fingerprint_sha256 VARCHAR(100),
    key_algorithm VARCHAR(50),
    key_size INTEGER,
    subject_alt_names TEXT[],
    pem_data TEXT NOT NULL,
    key_pem_encrypted TEXT,
    chain_pem TEXT,
    dh_params_pem TEXT,
    is_active BOOLEAN DEFAULT false,
    is_self_signed BOOLEAN DEFAULT false,
    imported BOOLEAN DEFAULT false,
    enabled BOOLEAN DEFAULT true,
    tenant_id UUID REFERENCES tenants(id),
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(name, tenant_id)
);

CREATE TRIGGER set_certificates_updated_at
    BEFORE UPDATE ON certificates
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE INDEX IF NOT EXISTS idx_certificates_type_active
    ON certificates(cert_type, is_active);

-- ============================================================================
-- 4. RADIUS NAS Clients
-- ============================================================================
CREATE TABLE IF NOT EXISTS radius_nas_clients (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    ip_address VARCHAR(50) NOT NULL,  -- Can be IP or CIDR
    secret_encrypted TEXT NOT NULL,
    shortname VARCHAR(100),
    nas_type VARCHAR(50) DEFAULT 'other',  -- cisco, juniper, aruba, other
    description TEXT,
    virtual_server VARCHAR(100),
    enabled BOOLEAN DEFAULT true,
    tenant_id UUID REFERENCES tenants(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(name, tenant_id)
);

CREATE TRIGGER set_radius_nas_clients_updated_at
    BEFORE UPDATE ON radius_nas_clients
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ============================================================================
-- 5. System Settings
-- ============================================================================
CREATE TABLE IF NOT EXISTS system_settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category VARCHAR(100) NOT NULL,
    setting_key VARCHAR(255) NOT NULL,
    setting_value TEXT,
    value_type VARCHAR(20) DEFAULT 'string',  -- string, integer, boolean, json
    description TEXT,
    is_secret BOOLEAN DEFAULT false,
    tenant_id UUID REFERENCES tenants(id),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    updated_by UUID REFERENCES users(id),
    UNIQUE(category, setting_key, tenant_id)
);

-- Seed default settings
INSERT INTO system_settings (category, setting_key, setting_value, value_type, description) VALUES
    ('radius', 'auth_port', '1812', 'integer', 'RADIUS authentication port'),
    ('radius', 'acct_port', '1813', 'integer', 'RADIUS accounting port'),
    ('radius', 'coa_port', '3799', 'integer', 'RADIUS Change of Authorization port'),
    ('radius', 'default_eap_type', 'peap', 'string', 'Default EAP type for authentication'),
    ('radius', 'tls_min_version', '1.2', 'string', 'Minimum TLS version for EAP-TLS/PEAP'),
    ('general', 'jwt_expire_minutes', '60', 'integer', 'JWT token expiration in minutes'),
    ('general', 'log_level', 'INFO', 'string', 'Application log level'),
    ('general', 'session_timeout_minutes', '480', 'integer', 'User session timeout in minutes'),
    ('general', 'max_login_attempts', '5', 'integer', 'Maximum failed login attempts before lockout')
ON CONFLICT (category, setting_key, tenant_id) DO NOTHING;

-- ============================================================================
-- 6. User Preferences
-- ============================================================================
CREATE TABLE IF NOT EXISTS user_preferences (
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    timezone VARCHAR(50) DEFAULT 'UTC',
    language VARCHAR(10) DEFAULT 'en',
    theme VARCHAR(20) DEFAULT 'light',
    notifications_enabled BOOLEAN DEFAULT true,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- 7. FreeRADIUS Config
-- ============================================================================
CREATE TABLE IF NOT EXISTS freeradius_config (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    config_type VARCHAR(50) NOT NULL,
    config_name VARCHAR(100) NOT NULL,
    config_content TEXT NOT NULL,
    config_hash VARCHAR(64),
    last_applied_at TIMESTAMPTZ,
    last_applied_hash VARCHAR(64),
    status VARCHAR(20) DEFAULT 'pending',
    error_message TEXT,
    tenant_id UUID REFERENCES tenants(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(config_type, config_name, tenant_id)
);

-- ============================================================================
-- 8. Schema Modification: Add realm_name to radius_auth_log
-- ============================================================================
ALTER TABLE radius_auth_log ADD COLUMN IF NOT EXISTS realm_name VARCHAR(255);
CREATE INDEX IF NOT EXISTS idx_radius_auth_realm ON radius_auth_log(realm_name, timestamp DESC);

COMMIT;
