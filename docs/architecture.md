# OpenRadiusWeb Architecture Document (SDD / BDD / DDD)

## 1. System Overview

OpenRadiusWeb is an open-source Network Access Control (NAC) system combining RADIUS-based 802.1X authentication with device visibility and policy enforcement. It uses a microservices architecture with event-driven communication.

**Technology Stack:**
| Layer | Technology |
|-------|-----------|
| Frontend | React 18, Ant Design 5, TypeScript, Vite 5, Nginx |
| API Gateway | FastAPI (Python 3.11), Uvicorn |
| Message Bus | NATS JetStream |
| Database | PostgreSQL 15 (TimescaleDB) |
| Cache | Redis 7 |
| Authentication | FreeRADIUS 3.2.3, rlm_python3, rlm_ldap |
| Container | Docker, Docker Compose |

---

## 2. SDD (Software Design Document) Analysis

### 2.1 Layered Architecture

```
+------------------------------------------------------+
|  PRESENTATION LAYER (Frontend)                       |
|  React SPA  ->  Nginx Reverse Proxy                  |
+------------------------------------------------------+
                        |  HTTP/REST
+------------------------------------------------------+
|  API LAYER (Gateway)                                 |
|  FastAPI Routes  ->  Middleware (JWT, CORS, Audit)   |
|  20 route modules  ->  Request validation            |
+------------------------------------------------------+
                        |  SQL / NATS
+------------------------------------------------------+
|  DOMAIN LAYER (Shared + Services)                    |
|  PolicyEvaluator  ->  Device Enrichment              |
|  Pydantic Models  ->  Domain Exceptions              |
+------------------------------------------------------+
                        |  asyncpg / psycopg2
+------------------------------------------------------+
|  DATA LAYER (PostgreSQL + Redis + NATS)              |
|  TimescaleDB Hypertables  ->  JetStream Streams      |
+------------------------------------------------------+
```

### 2.2 Design Patterns in Use

| Pattern | Location | Usage |
|---------|----------|-------|
| **Dependency Injection** | `services/gateway/main.py` | FastAPI `Depends()` for DB, auth, middleware |
| **Repository** | `services/gateway/routes/*.py` | Async session with parameterized SQL queries |
| **Factory** | `shared/orw_common/database.py` | `create_async_engine()` for DB connections |
| **Strategy** | `shared/orw_common/policy_evaluator.py` | Multiple evaluation operators (equals, gt, regex, etc.) |
| **Middleware Chain** | `services/gateway/main.py:115-139` | CORS, SecurityHeaders, RequestID stacked middleware |
| **Observer / Pub-Sub** | `shared/orw_common/nats_client.py` | NATS JetStream inter-service event messaging |
| **Adapter** | `services/gateway/utils/safe_sql.py` | Request fields -> safe SQL clause translation |
| **Template Method** | `services/auth/freeradius/templates/` | Jinja2 config templates for FreeRADIUS |
| **Singleton** | `shared/orw_common/nats_client.py` | Global NATS connection (`_nc`, `_js`) |
| **Exception Mapping** | `services/gateway/main.py:54-66` | DomainError hierarchy -> HTTP status codes |

### 2.3 API Design

**RESTful Conventions:** All resources follow standard CRUD patterns:
```
GET    /api/v1/{resource}           - List (with filters, pagination)
GET    /api/v1/{resource}/{id}      - Get single resource
POST   /api/v1/{resource}           - Create
PUT    /api/v1/{resource}/{id}      - Update
DELETE /api/v1/{resource}/{id}      - Delete
```

**Authentication:** JWT Bearer Token (HS256)
- Token payload: `{sub, username, role, tenant_id, exp, iat}`
- Roles: `admin` (full), `operator` (read/write), `viewer` (read-only)
- Rate limiting: 20 login attempts/minute, 5 failed attempts -> 15-min lockout

**Error Handling:** Domain exception hierarchy -> HTTP codes:
| Exception | HTTP Code |
|-----------|-----------|
| `NotFoundError` | 404 |
| `ConflictError` | 409 |
| `ValidationError` | 400 |
| `AuthenticationError` | 401 |
| `AuthorizationError` | 403 |
| `RateLimitError` | 429 |
| Unhandled | 500 (generic message, details logged internally) |

### 2.4 Security Patterns

| Aspect | Implementation |
|--------|---------------|
| Password hashing | bcrypt (12 rounds) |
| JWT | HS256, configurable expiry |
| Rate limiting | Redis-backed, IP + account lockout |
| RBAC | 3-tier (admin/operator/viewer) |
| SQL injection | Parameterized queries + column allowlists (`safe_sql.py`) |
| Security headers | X-Content-Type-Options, X-Frame-Options, HSTS, etc. |
| CORS | Explicit origin whitelist |
| Audit trail | All mutations logged to `audit_log` table |
| Tenant isolation | All queries filtered by `tenant_id` |

### 2.5 Database Patterns

- **Connection pooling**: SQLAlchemy async engine (pool_size=20, overflow=10, pre-ping=true)
- **Transactions**: Auto-commit on success, auto-rollback on exception via `AsyncSession`
- **Upsert**: PostgreSQL `ON CONFLICT ... DO UPDATE` for device discovery
- **Safe SQL**: Column allowlists per table prevent SQL injection in dynamic UPDATE queries
- **TimescaleDB**: Hypertables for `radius_auth_log`, `events`, `audit_log` (time-partitioned)

---

## 3. BDD (Behavior-Driven Development) Analysis

### 3.1 Test Framework

**Framework:** pytest (standard Python testing)
**No BDD framework** (behave, pytest-bdd) is currently used.

### 3.2 Test Structure

| Test File | Scope | Tests |
|-----------|-------|-------|
| `tests/unit/test_models.py` | Pydantic model validation | Device, Policy, NetworkDevice create/update constraints |
| `tests/unit/test_evaluator.py` | Policy evaluation logic | 11+ scenarios (equals, in, contains, gt, lt, regex, empty conditions, multiple conditions) |
| `tests/unit/test_fingerprinter.py` | Device fingerprinting | OS/vendor detection |
| `services/gateway/tests/api/test_auth.py` | Auth API endpoints | Login, token validation, RBAC enforcement |
| `services/gateway/tests/api/test_devices.py` | Device CRUD API | List, create, auth checks |
| `services/gateway/tests/api/test_policies.py` | Policy CRUD API | List, create, validation |
| `services/gateway/tests/unit/test_audit.py` | Audit logging | Log creation, field validation |
| `services/gateway/tests/unit/test_safe_sql.py` | SQL safety | Column filtering, injection prevention |

### 3.3 Test Coverage Assessment

| Component | Coverage | Status |
|-----------|----------|--------|
| Pydantic models | High | Validation constraints, edge cases |
| Policy evaluator | High | 11+ operator scenarios |
| Auth API | Medium | Login, token, RBAC |
| Device API | Medium | List, create |
| Safe SQL | High | Injection prevention |
| LDAP/RADIUS config | Low | Not yet tested |
| CoA operations | Low | Not yet tested |
| Certificate management | Low | Not yet tested |
| NATS event handling | Partial | Mocked in fixtures |

### 3.4 Running Tests

```bash
# Unit tests (shared)
cd tests && pytest -v

# Gateway API tests
cd services/gateway && pytest -v

# All tests with coverage
pytest --cov=shared --cov=services -v
```

---

## 4. DDD (Domain-Driven Design) Analysis

### 4.1 Bounded Contexts

Each microservice represents a bounded context:

```
+------------------+     NATS Events      +------------------+
|   Discovery      |--------------------->|  Device          |
|   Context        |  orw.device.         |  Inventory       |
|                  |  discovered          |  Context         |
+------------------+                      +--------+---------+
                                                   |
                                    orw.policy.evaluate_device
                                                   |
+------------------+                      +--------v---------+
|   Switch Mgmt    |<--------------------|  Policy Engine   |
|   Context        |  orw.switch.         |  Context         |
|                  |  set_vlan            |                  |
+------------------+                      +------------------+
                                                   |
+------------------+                      +--------v---------+
|   Authentication |                      |  Event           |
|   Context        |                      |  Context         |
|  (FreeRADIUS)    |                      |                  |
+------------------+                      +------------------+
         |
         v
+------------------+
|   API Gateway    |
|   Context        |
|  (Presentation)  |
+------------------+
```

### 4.2 Entities (Objects with Identity)

| Entity | Identity | Natural Key | Key Attributes |
|--------|----------|-------------|----------------|
| Device | UUID | mac_address | ip, hostname, os, status, risk_score |
| User | UUID | username | email, role, enabled, last_login |
| Policy | UUID | name | priority, conditions, actions, enabled |
| NetworkDevice | UUID | ip_address | vendor, type, ports, snmp_config |
| Certificate | UUID | serial_number | type, subject, not_before, not_after |
| VLAN | UUID | vlan_id (per tenant) | name, purpose, subnet |
| MabDevice | UUID | mac_address | device_type, assigned_vlan, expiry |
| LdapServer | UUID | host+port | bind_dn, base_dn, search_filters |
| NasClient | UUID | ip_address | secret, shortname, type |
| Realm | UUID | name | type, ldap_server, auth_types, default_vlan |
| GroupVlanMapping | UUID | group_name (per tenant) | vlan_id, priority, ldap_server |

### 4.3 Value Objects (No Identity)

| Value Object | Used In | Fields |
|-------------|---------|--------|
| PolicyCondition | Policy | field, operator, value |
| PolicyAction | Policy | type, params |
| DeviceContext | PolicyEvaluator | mac, ip, hostname, type, vlan, port |
| CoARequest | CoA Service | action, vlan_id, acl_name, reason |
| PaginationParams | All list endpoints | page, page_size, sort_by, sort_order |

### 4.4 Aggregates

**Device Aggregate:**
```
Device (Root)
  +-- DeviceProperty[] (name, value, source)
  +-- Event[] (type, severity, message)
  +-- status: str (discovered/authenticated/quarantined)
  +-- risk_score: int (0-100)
```

**Policy Aggregate:**
```
Policy (Root)
  +-- PolicyCondition[] (field, operator, value)
  +-- PolicyAction[] match_actions (type, params)
  +-- PolicyAction[] no_match_actions (type, params)
  +-- priority: int (evaluation order)
```

**NetworkDevice Aggregate:**
```
NetworkDevice (Root)
  +-- SwitchPort[] (port_id, vlan, status, connected_mac)
  +-- SnmpConfig (community, version, credentials)
  +-- SshConfig (username, password, enable_password)
```

### 4.5 Domain Events (via NATS JetStream)

| Event Subject | Publisher | Consumer | Payload |
|--------------|-----------|----------|---------|
| `orw.device.discovered` | Discovery | Device Inventory | mac, ip, hostname, services |
| `orw.device.upserted` | Device Inventory, Gateway | Policy Engine | device_id, mac, ip |
| `orw.policy.evaluate_device` | Device Inventory | Policy Engine | device_id |
| `orw.policy.created` | Gateway | Policy Engine | policy_id, name |
| `orw.device.evaluated` | Policy Engine | Event Service | device_id, matched_policies |
| `orw.switch.set_vlan` | Policy Engine | Switch Mgmt | switch_ip, port, vlan_id |
| `orw.switch.bounce_port` | Policy Engine, CoA | Switch Mgmt | switch_ip, port |
| `orw.discovery.scan_request` | Gateway | Discovery | subnet, scan_type |
| `orw.coa.request` | Gateway | CoA Service | nas_ip, mac, action |

### 4.6 Domain Services

| Service | Responsibility | Location |
|---------|---------------|----------|
| PolicyEvaluator | Match conditions against device context | `shared/orw_common/policy_evaluator.py` |
| DeviceInventory | Upsert and enrich discovered devices | `services/device_inventory/main.py` |
| Discovery | ARP/DHCP/SNMP/Nmap scanning | `services/discovery/main.py` |
| SwitchMgmt | VLAN/port control via SNMP/SSH | `services/switch_mgmt/main.py` |
| CoAService | RADIUS Change of Authorization packets | `services/auth/coa_service.py` |
| ConfigWatcher | FreeRADIUS config sync from DB | `services/auth/freeradius_config_watcher.py` |

### 4.7 Anti-Corruption Layers

| Between | Adapter | Purpose |
|---------|---------|---------|
| JWT Token <-> Internal User | `middleware/auth.py` | Decode token claims to user dict |
| RADIUS Attributes <-> Domain | `rlm_orw.py` | Translate RADIUS tuples to Python dicts |
| Discovery Protocols <-> Device | `device_inventory/main.py` | Normalize ARP/SNMP/Nmap data to device entity |
| Request Fields <-> SQL Columns | `utils/safe_sql.py` | Column allowlist, type casting |

---

## 5. Feature-to-Component Mapping

### 5.1 Which Features Use Which Components

| Feature | Frontend Page | API Route | Service | DB Table |
|---------|--------------|-----------|---------|----------|
| **Login / Auth** | LoginPage.tsx | routes/auth.py | Gateway | users |
| **Dashboard** | Dashboard.tsx | routes/health.py, devices.py | Gateway | devices, radius_auth_log |
| **Device Management** | Devices.tsx | routes/devices.py | Device Inventory | devices, device_properties |
| **Switch Management** | Switches.tsx | routes/network_devices.py | Switch Mgmt | network_devices |
| **Policy Engine** | Policies.tsx | routes/policies.py | Policy Engine | policies, policy_evaluations |
| **Access Tracker** | AccessTracker.tsx | routes/radius_auth_log.py | Gateway (read) | radius_auth_log |
| **CoA** | CoAPage.tsx | routes/coa.py | CoA Service | - (stateless) |
| **802.1X Overview** | Dot1xOverview.tsx | routes/dot1x_overview.py | Gateway | (aggregation) |
| **LDAP Config** | LdapServers.tsx | routes/ldap_servers.py | Config Watcher | ldap_servers |
| **Realm Config** | Realms.tsx | routes/radius_realms.py | Config Watcher | radius_realms |
| **Certificates** | CertificatesPage.tsx | routes/certificates.py | Config Watcher | certificates |
| **NAS Clients** | NasClients.tsx | routes/nas_clients.py | Config Watcher | radius_nas_clients |
| **VLAN Mgmt** | VlanManagement.tsx | routes/vlans.py | Gateway | vlans |
| **MAB Devices** | MabDevices.tsx | routes/mab_devices.py | FreeRADIUS (rlm_orw) | mab_devices |
| **Dynamic VLAN** | GroupVlanMappings.tsx | routes/group_vlan_mappings.py | FreeRADIUS (rlm_orw) | group_vlan_mappings |
| **FreeRADIUS Config** | FreeRadiusConfig.tsx | routes/freeradius_config.py | Config Watcher | (templates) |
| **User Mgmt** | UserManagement.tsx | routes/auth.py | Gateway | users |
| **System Settings** | SystemSettings.tsx | routes/settings.py | Gateway | system_settings |
| **Audit Log** | AuditLog.tsx | routes/audit.py | Gateway | audit_log |
| **Network Discovery** | - (triggered via API) | - (NATS) | Discovery | devices |
| **Event Service** | - (background) | - (NATS) | Event Service | events |

### 5.2 RADIUS Authentication Flow

```
Supplicant (PC/Phone)
        |
        | EAP over 802.1X
        v
Authenticator (Switch/AP)
        |
        | RADIUS Access-Request (UDP 1812)
        v
FreeRADIUS (orw-freeradius container)
        |
        +-- authorize phase: rlm_orw.py authorize()
        |     +-- Detect MAB (username == MAC) -> check mab_devices table
        |     +-- Detect realm from User-Name
        |
        +-- authenticate phase: rlm_ldap (AD/LDAP bind)
        |
        +-- post_auth phase: rlm_orw.py post_auth()
              +-- Log to radius_auth_log table
              +-- Dynamic VLAN Assignment:
              |     1. Query LDAP for user's group memberships
              |     2. Match groups against group_vlan_mappings (priority order)
              |     3. Return Tunnel-Private-Group-Id in Access-Accept
              +-- Switch assigns port to VLAN
```
