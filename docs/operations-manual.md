# OpenRadiusWeb Operations Manual

## Table of Contents

1. [System Architecture](#1-system-architecture)
2. [Service Management](#2-service-management)
3. [User Management](#3-user-management)
4. [RADIUS Authentication Configuration](#4-radius-authentication-configuration)
5. [Device Management](#5-device-management)
6. [Policy Management](#6-policy-management)
7. [802.1X and Dynamic VLAN](#7-8021x-and-dynamic-vlan)
8. [Change of Authorization (CoA)](#8-change-of-authorization-coa)
9. [Logging and Monitoring](#9-logging-and-monitoring)
10. [API Reference](#10-api-reference)

---

## 1. System Architecture

### 1.1 Components

OpenRadiusWeb consists of 12 Docker containers:

```
Browser -> Nginx (8888) -> FastAPI Gateway (8000) -> PostgreSQL / Redis / NATS
                                                 -> FreeRADIUS (1812/1813 UDP)
                                                 -> Discovery / Inventory / Policy / Switch Mgmt
```

### 1.2 Technology Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Frontend | React 18 + Ant Design 5 | Web UI |
| API Gateway | FastAPI (Python 3.11) | REST API |
| Database | PostgreSQL 15 + TimescaleDB | Data storage |
| Cache | Redis 7 | Session/rate limiting |
| Message Bus | NATS JetStream | Inter-service events |
| RADIUS | FreeRADIUS 3.2.3 | 802.1X authentication |

### 1.3 Access Points

| Service | Default URL |
|---------|-------------|
| Web UI | http://SERVER_IP:8888 |
| API Gateway | http://SERVER_IP:8000 |
| API Documentation (Swagger) | http://SERVER_IP:8000/docs |
| RADIUS Authentication | SERVER_IP:1812/udp |
| RADIUS Accounting | SERVER_IP:1813/udp |
| RADIUS CoA | SERVER_IP:3799/udp |

---

## 2. Service Management

### 2.1 Check Service Status

```bash
cd /opt/openradiusweb
docker compose -f docker-compose.prod.yml ps
```

### 2.2 View Logs

```bash
# All services
docker compose -f docker-compose.prod.yml logs -f --tail=100

# Specific service
docker logs orw-gateway --tail=50 -f
docker logs orw-freeradius --tail=50 -f
docker logs orw-postgres --tail=50
```

### 2.3 Restart Services

```bash
# Single service
docker compose -f docker-compose.prod.yml restart gateway

# All services
docker compose -f docker-compose.prod.yml restart
```

### 2.4 Rebuild After Code Changes

```bash
# Rebuild + restart specific service
docker compose -f docker-compose.prod.yml build gateway && \
docker compose -f docker-compose.prod.yml up -d gateway

# Rebuild all
docker compose -f docker-compose.prod.yml up -d --build
```

### 2.5 Resource Monitoring

```bash
docker stats --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"
```

---

## 3. User Management

### 3.1 Roles

| Role | Permissions |
|------|------------|
| `admin` | Full access: create/read/update/delete all resources, manage users |
| `operator` | Read/write: manage devices, policies, RADIUS config |
| `viewer` | Read-only: view dashboards, logs, device list |

### 3.2 Managing Users (Web UI)

Navigate to **Settings > Users**:
- Click **Add User** to create a new user
- Set username, email, role, and initial password
- Toggle **Enabled** to activate/deactivate accounts

### 3.3 Managing Users (API)

```bash
# Create user
curl -X POST http://SERVER:8000/api/v1/auth/users \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"username":"john","password":"SecurePass123!","email":"john@corp.com","role":"operator"}'

# List users
curl -H "Authorization: Bearer $TOKEN" http://SERVER:8000/api/v1/auth/users

# Disable user
curl -X PATCH http://SERVER:8000/api/v1/auth/users/{user_id} \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"enabled":false}'
```

### 3.4 Password Reset (Emergency)

```bash
# Reset admin password directly in database
docker exec orw-postgres psql -U orw -d orw -c "
UPDATE users SET password_hash = '\$2b\$12\$LJ3m4yPADCgNB8YFTlPALuSCDREGjJe6vFcszC...'
WHERE username = 'admin';"

# Or use the reset script
python3 deploy/reset_admin_pw.py
```

---

## 4. RADIUS Authentication Configuration

### 4.1 LDAP Server Setup

Navigate to **RADIUS Config > LDAP Servers > Add**:

| Field | Description | Example |
|-------|-------------|---------|
| Name | Display name | AD-Primary |
| Host | LDAP/AD server hostname or IP | dc01.corp.local |
| Port | LDAP port (389 or 636 for LDAPS) | 389 |
| Use TLS | Enable LDAPS (port 636) | false |
| Use StartTLS | Upgrade to TLS on port 389 | true |
| Bind DN | Service account DN | CN=svc-radius,OU=Service,DC=corp,DC=local |
| Bind Password | Service account password | *** |
| Base DN | LDAP search base | DC=corp,DC=local |
| User Search Filter | LDAP filter for user lookup | (sAMAccountName=%{User-Name}) |
| Group Membership Attr | Attribute containing groups | memberOf |
| Priority | Lower number = tried first | 10 |

**Test Connection**: Click the **Test** button to verify LDAP connectivity.

### 4.2 RADIUS Realm Setup

Navigate to **RADIUS Config > Realms > Add**:

| Field | Description | Example |
|-------|-------------|---------|
| Name | Realm name (domain) | corp.local |
| Type | local or proxy | local |
| LDAP Server | Associated LDAP server | AD-Primary |
| Auth Types | Allowed EAP methods | PEAP, EAP-TLS |
| Default VLAN | Fallback VLAN for this realm | 10 |
| Strip Username | Remove @realm from username | true |

### 4.3 NAS Client Setup

Navigate to **RADIUS Config > NAS Clients > Add**:

| Field | Description | Example |
|-------|-------------|---------|
| Name | Switch/AP name | Core-SW-01 |
| IP Address | NAS IP (must match RADIUS source) | 192.168.1.1 |
| Shared Secret | RADIUS shared secret | MySecretKey123 |
| Type | NAS vendor type | cisco |
| Description | Location or purpose | Building A Core Switch |

### 4.4 Certificate Management

Navigate to **RADIUS Config > Certificates**:

**Step 1: Generate CA Certificate**
- Click **Generate CA** to create a Certificate Authority
- Set Common Name (e.g., "OpenRadiusWeb CA")
- Set validity period (e.g., 3650 days = 10 years)

**Step 2: Generate Server Certificate**
- Click **Generate Server Cert**
- Set Common Name (e.g., "radius.corp.local")
- Add Subject Alternative Names (DNS names and IPs)
- Sign with the CA created in Step 1

**Step 3: Activate**
- Select the server certificate and click **Activate**
- FreeRADIUS will reload automatically via Config Watcher

### 4.5 FreeRADIUS Configuration

Navigate to **RADIUS Config > FreeRADIUS**:

This page shows the current FreeRADIUS configuration status:
- Active EAP methods
- Loaded LDAP modules
- Registered NAS clients
- Certificate status

**Configuration is generated automatically** from the database. Changes to LDAP servers, NAS clients, realms, and certificates trigger Config Watcher to regenerate and reload FreeRADIUS.

---

## 5. Device Management

### 5.1 Device Discovery

Devices are discovered automatically via:
- **Passive**: ARP/DHCP monitoring on the configured network interface
- **Active**: On-demand Nmap/SNMP scans triggered via API

### 5.2 Viewing Devices

Navigate to **Devices** in the sidebar. The table shows:
- MAC Address, IP Address, Hostname
- Device Type, OS Family, Vendor
- Status (discovered, authenticated, quarantined)
- Risk Score (0-100)
- First Seen / Last Seen timestamps

### 5.3 Switch Management

Navigate to **Switches** in the sidebar:
- Add network switches/APs with SNMP or SSH credentials
- View port status, connected devices, VLAN assignments
- Trigger port discovery scans

---

## 6. Policy Management

### 6.1 Policy Structure

Each policy consists of:
- **Conditions**: IF rules (field, operator, value)
- **Match Actions**: Execute when ALL conditions match
- **No-Match Actions**: Execute when conditions do NOT match
- **Priority**: Lower number = evaluated first

### 6.2 Available Operators

| Operator | Description | Example |
|----------|-------------|---------|
| equals | Exact match | device_type equals "printer" |
| not_equals | Not equal | status not_equals "quarantined" |
| in | Value in list | os_family in ["Windows", "macOS"] |
| not_in | Value not in list | vlan not_in [99, 100] |
| contains | String contains | hostname contains "srv" |
| gt | Greater than | risk_score gt 70 |
| lt | Less than | risk_score lt 30 |
| regex | Regular expression | mac_address regex "^aa:bb" |

### 6.3 Available Actions

| Action | Parameters | Description |
|--------|-----------|-------------|
| vlan_assign | vlan_id | Move device to specified VLAN |
| quarantine | vlan_id | Move to quarantine VLAN |
| notify | message, channel | Send notification |
| acl_apply | acl_name | Apply access control list |
| coa | action (disconnect/bounce) | Send CoA to NAS |
| tag_device | tag | Apply tag to device |
| bounce_port | - | Bounce the switch port |
| log_event | severity, message | Log a security event |

### 6.4 Creating a Policy (Web UI)

1. Navigate to **Policies > Add Policy**
2. Set name, description, priority
3. Add conditions (e.g., device_type equals "unknown", risk_score gt 50)
4. Add match actions (e.g., quarantine to VLAN 99)
5. Enable and save

---

## 7. 802.1X and Dynamic VLAN

### 7.1 Overview

The 802.1X authentication flow:

```
Supplicant (PC) ---EAP---> Switch ---RADIUS---> FreeRADIUS
                                                    |
                                              rlm_python3 (rlm_orw.py)
                                                    |
                                              +-- authorize: MAB check, realm detection
                                              +-- authenticate: LDAP bind
                                              +-- post_auth: Dynamic VLAN Assignment
                                                    |
                                              Access-Accept + Tunnel-Private-Group-Id
                                                    |
Switch assigns port to VLAN <-----------------------+
```

### 7.2 VLAN Management

Navigate to **RADIUS Config > VLANs**:

Define all VLANs used in your network:

| VLAN ID | Name | Purpose |
|---------|------|---------|
| 10 | Corporate | corporate |
| 20 | Guest | guest |
| 30 | IoT | iot |
| 40 | Printer | printer |
| 98 | Remediation | remediation |
| 99 | Quarantine | quarantine |
| 100 | VoIP | voip |

### 7.3 MAB (MAC Authentication Bypass)

Navigate to **RADIUS Config > MAB Devices**:

Add devices that cannot do 802.1X (printers, cameras, IoT):

| Field | Description |
|-------|-------------|
| MAC Address | Device MAC (any format, auto-normalized) |
| Name | Device name |
| Device Type | printer, camera, iot, phone, sensor, ap, other |
| Assigned VLAN | VLAN to assign on authentication |
| Expiry Date | Optional expiration date |

### 7.4 Dynamic VLAN Assignment

Navigate to **RADIUS Config > Dynamic VLAN**:

Map AD/LDAP groups to VLANs:

| Field | Description |
|-------|-------------|
| AD/LDAP Group | Exact group name in AD (e.g., "IT-Staff") |
| Assigned VLAN | VLAN to assign |
| Priority | Lower = higher priority (first match wins) |
| LDAP Server | Optional: limit to specific LDAP server |

**How it works:**
1. User authenticates via 802.1X (PEAP/EAP-TLS)
2. FreeRADIUS authenticates against AD/LDAP
3. `post_auth()` queries LDAP for user's group memberships
4. Matches groups against `group_vlan_mappings` table (by priority)
5. Returns `Tunnel-Private-Group-Id` in Access-Accept
6. Switch assigns port to the matching VLAN

**Example mappings:**

| Priority | AD Group | VLAN |
|----------|----------|------|
| 10 | Domain Admins | 100 (Management) |
| 20 | IT-Staff | 10 (Corporate) |
| 50 | Contractors | 20 (Guest) |
| 90 | Domain Users | 10 (Corporate) |

---

## 8. Change of Authorization (CoA)

### 8.1 Overview

CoA allows real-time changes to authenticated sessions without waiting for re-authentication.

### 8.2 Using CoA (Web UI)

Navigate to **CoA**:

1. Enter the NAS IP and MAC address
2. Select action:
   - **Disconnect**: Force device to re-authenticate
   - **Re-authenticate**: Trigger re-authentication
   - **VLAN Change**: Move to a different VLAN
   - **Bounce Port**: Shut/no-shut the switch port
3. Click **Send CoA**

### 8.3 Using CoA (API)

```bash
curl -X POST http://SERVER:8000/api/v1/coa \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "nas_ip": "192.168.1.1",
    "mac_address": "aa:bb:cc:dd:ee:ff",
    "action": "vlan_change",
    "vlan_id": 99
  }'
```

---

## 9. Logging and Monitoring

### 9.1 Log Locations

| Component | Location | Format | Access Method |
|-----------|----------|--------|---------------|
| Python Services | Container stdout | Structured JSON | `docker logs <container>` |
| RADIUS Auth Log | PostgreSQL `radius_auth_log` | Structured rows | Web UI: Access Tracker |
| Audit Log | PostgreSQL `audit_log` | Structured rows | Web UI: Settings > Audit |
| Events | PostgreSQL `events` | Structured rows | API: /api/v1/events |
| FreeRADIUS | Container stdout + detail files | Text | `docker logs orw-freeradius` |
| Nginx | Container stdout | CLF (text) | `docker logs orw-frontend` |

### 9.2 Viewing RADIUS Auth Logs

Navigate to **Access Tracker** in the sidebar:
- Filter by username, MAC address, NAS IP, auth result
- View auth method (PEAP, EAP-TLS, MAB)
- See failure reasons (AD errors, certificate issues)
- Export logs as CSV or JSON

### 9.3 Audit Log

Navigate to **Settings > Audit Log**:
- View all administrative actions (create, update, delete)
- Filter by user, action, resource type, date range
- Export for compliance reporting

### 9.4 Log Level Configuration

Set `LOG_LEVEL` in `.env.production`:

| Level | Description |
|-------|-------------|
| DEBUG | Verbose (development only) |
| INFO | Normal operations (recommended) |
| WARNING | Issues that may need attention |
| ERROR | Errors requiring action |

### 9.5 Prometheus Metrics

The API Gateway exposes metrics at `http://SERVER:8000/metrics` for Prometheus scraping.

---

## 10. API Reference

### 10.1 Authentication

```bash
# Login (get JWT token)
POST /api/v1/auth/login
Body: {"username": "admin", "password": "password"}
Response: {"access_token": "eyJ...", "token_type": "bearer"}

# Use token in subsequent requests
Authorization: Bearer eyJ...
```

### 10.2 Key Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | /health | Health check |
| POST | /api/v1/auth/login | Login |
| GET | /api/v1/devices | List devices |
| GET | /api/v1/network-devices | List switches |
| GET/POST | /api/v1/policies | List/create policies |
| GET | /api/v1/radius/auth-log | RADIUS auth logs |
| POST | /api/v1/coa | Send CoA request |
| GET/POST | /api/v1/ldap-servers | LDAP server config |
| GET/POST | /api/v1/radius-realms | Realm config |
| GET/POST | /api/v1/nas-clients | NAS client config |
| GET/POST | /api/v1/certificates | Certificate management |
| GET/POST | /api/v1/vlans | VLAN management |
| GET/POST | /api/v1/mab-devices | MAB whitelist |
| GET/POST | /api/v1/group-vlan-mappings | Dynamic VLAN mappings |
| GET | /api/v1/dot1x/overview | 802.1X status dashboard |
| GET | /api/v1/settings | System settings |
| GET | /api/v1/audit-log | Audit log |

### 10.3 Interactive API Docs

Full interactive API documentation is available at:
- **Swagger UI**: http://SERVER:8000/docs
- **ReDoc**: http://SERVER:8000/redoc

---

> **Version:** 1.0
> **Last Updated:** 2026-04-23
