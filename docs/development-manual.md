# OpenRadiusWeb — Development Manual

**Version:** 1.4
**Date:** 2026-04-29
**Audience:** Developers, code reviewers, architects working on OpenRadiusWeb
**Languages:** English (this file) · 中文 ([development-manual.zh.md](development-manual.zh.md))

This manual is the consolidated reference for understanding what the project does, what features exist, and how the codebase is decomposed into atomic, single-responsibility modules.

---

## Development Principles

> **Build features as the smallest possible modules.**
>
> - **Goal:** achieve high cohesion and low coupling so the system stays easy to test and easy to replace.
> - **Means:** decompose every requirement into atomic modules — each module does exactly one thing.

The rest of this manual elaborates on this principle:

- [§3 Atomic Module Catalog](#part-3--atomic-module-catalog) — inventory of ~600 atoms across 19 patterns
- [§5.1 Eight Rules for Atomic Code](#51-eight-rules-for-atomic-code) — how to write a single atom
- [§9.1 Three-Layer Architecture](#91-three-layer-architecture) — interface / service / infrastructure layering
- [§10.6 Standard Directory Structure](#106-standard-directory-structure-feature-oriented-recursively-modular) — wrapping atoms with `features/<name>/`

---

## Table of Contents

0. [Development Principles](#development-principles)
1. [Project Requirements](#part-1--project-requirements)
2. [Core Feature Inventory](#part-2--core-feature-inventory)
3. [Atomic Module Catalog](#part-3--atomic-module-catalog)
4. [Composition Flows](#part-4--composition-flows)
5. [Development Conventions](#part-5--development-conventions)
6. [Quick Reference Index](#part-6--quick-reference-index)
7. [API Specification (OpenAPI)](#part-7--api-specification-openapi)
8. [Decoupling Design (DI + Event-Driven)](#part-8--decoupling-design-di--event-driven)
9. [Development Workflow](#part-9--development-workflow)
10. [Unified Deployment Strategy](#part-10--unified-deployment-strategy)

---

# Part 1 — Project Requirements

## 1.1 What This Project Does

OpenRadiusWeb is a **Network Access Control (NAC) system** that controls who and what can connect to a corporate network. It performs three primary jobs:

1. **Authenticates** users and devices via 802.1X (RADIUS/EAP) or MAC bypass (MAB)
2. **Authorizes** them to specific VLANs/ACLs based on policies and AD group membership
3. **Enforces** policies at runtime via Change-of-Authorization (CoA) when conditions change

It is built as a Docker-Compose stack of microservices around **FreeRADIUS 3.2.3** with a React/Ant Design web UI and a FastAPI gateway.

## 1.2 Functional Requirements (Currently Implemented)

| ID | Requirement | Priority |
|----|------------|----------|
| F1 | Authenticate users via 802.1X (PEAP, EAP-TLS, EAP-TTLS, MSCHAPv2) | Core |
| F2 | Authenticate devices via MAB (MAC whitelist) | Core |
| F3 | Look up user identity in LDAP/Active Directory | Core |
| F4 | Assign VLAN dynamically based on AD group membership | Core |
| F5 | Send CoA (RFC 5176) to disconnect/reauth/change VLAN | Core |
| F6 | Discover devices passively (ARP/DHCP) and actively (Nmap/SNMP) | Core |
| F7 | Maintain a device inventory with fingerprinting | Core |
| F8 | Manage NAS clients, VLANs, realms, certificates via UI | Core |
| F9 | Evaluate policies (conditions → actions) per device | Core |
| F10 | Log every RADIUS auth attempt with reason for failure | Core |
| F11 | Maintain a tamper-evident audit log of admin actions | Core |
| F12 | Multi-tenant isolation across all data | Core |
| F13 | Role-based access control (admin / operator / viewer) | Core |
| F14 | Manage switches via SSH (Cisco/Aruba/Juniper/HP/Dell/Extreme) | Core |
| F15 | Manage switches via SNMP v2c/v3 | Core |

## 1.3 Non-Functional Requirements

| ID | Requirement | Implementation |
|----|------------|----------------|
| NF1 | API response time < 500ms (p95) for CRUD endpoints | FastAPI + asyncpg pool=20 |
| NF2 | RADIUS auth latency < 100ms (excluding LDAP) | rlm_orw.py with pooled DB |
| NF3 | Survive single Postgres slowness | Redis rate-limit cache |
| NF4 | Audit log retention ≥ 1 year | TimescaleDB hypertable |
| NF5 | Auth log retention ≥ 1 year, queryable by MAC/user | TimescaleDB hypertable |
| NF6 | Secrets never in plaintext source/config | env file + vault (in progress) |
| NF7 | Brute-force protection on login | Redis token bucket + lockout |
| NF8 | Containerized, reproducible deployment | docker-compose.prod.yml |
| NF9 | All admin mutations audited with user + IP | log_audit() helper everywhere |
| NF10 | Tenant data isolation enforced at SQL layer | tenant_id WHERE clause everywhere |

## 1.4 Out of Scope (Currently Missing)

These are NOT implemented and intentionally excluded from current scope:

- Captive portal / guest self-registration
- BYOD onboarding (mobileconfig, ONC, Win profiles)
- Endpoint posture/compliance checks
- High availability / clustering
- Multi-factor authentication (admin login)
- SAML SSO (admin login)
- Firewall SSO push (Palo Alto, Forti, etc.)
- MDM integration (Intune, JAMF)
- TACACS+ device administration

## 1.5 Architecture Constraints

| Constraint | Rationale |
|-----------|-----------|
| Each microservice has its own DB connection pool | Independent scaling; isolated DB load |
| Inter-service communication via NATS JetStream only | No HTTP between services; durable delivery |
| Frontend uses axios instance with JWT interceptor | Centralized auth; no per-call boilerplate |
| All mutations write to audit_log | Compliance & forensics |
| All time-series data uses TimescaleDB hypertables | Fast time-range queries; automatic partitioning |
| All policies/secrets stored per-tenant | Multi-tenant isolation |

---

# Part 2 — Core Feature Inventory

## 2.1 Code Inventory Statistics

| Metric | Count |
|--------|-------|
| HTTP route handlers | 107 |
| NATS event channels | 8 |
| Microservices | 8 (gateway, discovery, device_inventory, policy_engine, switch_mgmt, freeradius, freeradius_config_watcher, coa_service, event_service) |
| Frontend pages | 18 |
| Pydantic domain models | 14 |
| Database tables | 17 |
| Database migrations | 5 |
| FreeRADIUS Jinja2 templates | 7 |
| Switch vendor adapters | 7 |
| Total atomic modules (decomposed) | ~600 |

## 2.2 Feature Map (16 Top-Level Groups)

| # | Feature Group | Backend Routes | Frontend Pages | NATS Channels | DB Tables |
|---|--------------|----------------|----------------|---------------|-----------|
| 1 | Authentication & User Mgmt | auth.py, profile.py | LoginPage, ProfilePage, UserManagement | — | users, tenants |
| 2 | Device Inventory | devices.py | Devices | orw.device.* | devices, device_properties |
| 3 | Device Discovery | — | — | orw.discovery.* | events |
| 4 | Policy Engine | policies.py | Policies | orw.policy.* | policies, policy_evaluations |
| 5 | RADIUS Authentication | radius_auth_log.py | AccessTracker | — | radius_auth_log |
| 6 | Dynamic VLAN | group_vlan_mappings.py | GroupVlanMappings | — | group_vlan_mappings |
| 7 | MAB | mab_devices.py | MabDevices | — | mab_devices |
| 8 | CoA | coa.py | CoAPage | orw.coa.* | (uses radius_auth_log + audit_log) |
| 9 | RADIUS Config | ldap_servers.py, radius_realms.py, nas_clients.py, vlans.py, freeradius_config.py | LdapServers, Realms, NasClients, VlanManagement, FreeRadiusConfig | orw.config.freeradius.apply | ldap_servers, radius_realms, nas_clients, vlans, freeradius_config |
| 10 | Certificates | certificates.py | CertificatesPage | — | certificates |
| 11 | Switch Management | network_devices.py | Switches | orw.switch.* | network_devices, switch_ports |
| 12 | Audit & Logging | audit.py | AuditLog | — | audit_log |
| 13 | 802.1X Overview | dot1x_overview.py | Dot1xOverview | — | (aggregations) |
| 14 | Event Service | — | — | (consumes all) | events |
| 15 | System Settings | settings.py | SystemSettings | — | system_settings |
| 16 | Health/Monitoring | health.py | Dashboard | — | — |

## 2.3 Endpoint Catalog (Backend)

### 2.3.1 Authentication (`/auth`)

| Method | Path | Permission | Purpose |
|--------|------|-----------|---------|
| POST | /auth/login | public | Login, return JWT |
| GET | /auth/me | authed | Current user profile |
| POST | /auth/users | admin | Create user |
| GET | /auth/users | operator+ | List users |
| GET | /auth/users/{id} | operator+ | Get user |
| PUT | /auth/users/{id} | admin | Update user |
| DELETE | /auth/users/{id} | admin | Delete user |
| POST | /auth/users/{id}/reset-password | admin | Reset password |
| GET | /auth/roles | authed | RBAC matrix |

### 2.3.2 Profile (`/profile`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | /profile | Own profile |
| PUT | /profile | Update prefs |
| POST | /profile/change-password | Change own password |
| PUT | /profile/email | Update own email |

### 2.3.3 Devices (`/devices`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | /devices | List + filters |
| GET | /devices/{id} | Get device |
| POST | /devices | Upsert by MAC |
| PATCH | /devices/{id} | Update |
| DELETE | /devices/{id} | Delete (admin) |
| POST | /devices/{id}/properties | Add property |
| GET | /devices/{id}/properties | Get properties |

### 2.3.4 Policies (`/policies`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | /policies | List |
| POST | /policies | Create |
| GET | /policies/{id} | Get |
| PATCH | /policies/{id} | Update |
| DELETE | /policies/{id} | Delete |
| GET | /policies/templates/list | Built-in templates |
| POST | /policies/templates/{id}/apply | Apply template |
| POST | /policies/simulate-all | Bulk simulate |
| POST | /policies/{id}/simulate | Single policy test |

### 2.3.5 RADIUS Configuration

`/ldap-servers`, `/radius/realms`, `/nas-clients`, `/vlans`, `/mab-devices`, `/group-vlan-mappings`, `/certificates`, `/network-devices`, `/freeradius-config`, `/settings`

Each follows the standard 5-endpoint CRUD pattern (`GET list`, `POST`, `GET {id}`, `PUT/PATCH {id}`, `DELETE {id}`) plus occasional special endpoints (test, lookup, generate).

### 2.3.6 RADIUS / Auth Log

| Method | Path | Purpose |
|--------|------|---------|
| GET | /radius/auth-log | Auth attempt history with filters |

### 2.3.7 CoA

| Method | Path | Purpose |
|--------|------|---------|
| POST | /coa/by-mac | Send CoA by MAC |
| POST | /coa/by-username | Send CoA by user |
| POST | /coa/by-session | Send CoA by session ID |
| POST | /coa/bulk | Up to 100 targets |
| GET | /coa/history | CoA event audit |
| GET | /coa/active-sessions | List live sessions |

### 2.3.8 Audit, Health, Overview

| Method | Path | Purpose |
|--------|------|---------|
| GET | /audit-log | Audit history |
| GET | /audit-log/export | JSON / CSV export |
| GET | /health | Liveness + version |
| GET | /dot1x-overview | 802.1X dashboard data |

## 2.4 NATS Subject Catalog

| Subject | Publisher | Subscriber | Purpose |
|---------|-----------|-----------|---------|
| orw.device.discovered | discovery | device_inventory | New device seen |
| orw.device.evaluated | policy_engine | event_service | Policy result |
| orw.policy.evaluate_device | gateway, device_inventory | policy_engine | Trigger evaluation |
| orw.policy.action.* | policy_engine | event_service | Action taken |
| orw.switch.set_vlan | policy_engine, gateway | switch_mgmt | Change VLAN |
| orw.switch.bounce_port | gateway | switch_mgmt | Shut/no-shut |
| orw.switch.poll_requested | gateway | switch_mgmt | Refresh port state |
| orw.discovery.scan_request | gateway | discovery | Active scan |
| orw.coa.send | gateway, policy_engine | coa_service | RADIUS CoA |
| orw.config.freeradius.apply | gateway | freeradius_config_watcher | Regenerate config |

## 2.5 Database Table Catalog

| Table | Purpose | Type |
|-------|---------|------|
| tenants | Multi-tenant root | Standard |
| users | Local user accounts | Standard |
| devices | Device inventory | Standard |
| device_properties | EAV extensible attrs | Standard |
| policies | Access policies | Standard |
| policy_evaluations | Eval history | Standard |
| network_devices | Switches/NAS | Standard |
| switch_ports | Per-port state | Standard |
| nas_clients | RADIUS NAS registry | Standard |
| ldap_servers | LDAP/AD configs | Standard |
| radius_realms | Realm chain | Standard |
| vlans | VLAN registry | Standard |
| mab_devices | MAB whitelist | Standard |
| group_vlan_mappings | Dynamic VLAN | Standard |
| certificates | PKI / EAP-TLS | Standard |
| system_settings | Tenant config | Standard |
| radius_auth_log | Auth attempts | TimescaleDB hypertable |
| events | All cross-service events | TimescaleDB hypertable |
| audit_log | Admin action trail | TimescaleDB hypertable |

---

# Part 3 — Atomic Module Catalog

**Atomic module** = a function or class with **one reason to change**, **one input shape**, **one output shape**, **one side-effect class**.

## 3.1 The 19 Atomic Patterns

| Pattern | Suffix Convention | Side-Effect | Test Difficulty |
|---------|-------------------|-------------|-----------------|
| **Validator** | `validate_*` | None (pure) | Trivial |
| **Normalizer** | `normalize_*` | None (pure) | Trivial |
| **Parser** | `parse_*` | None (pure) | Trivial |
| **Formatter** | `format_*` | None (pure) | Trivial |
| **Mapper** | `map_*`, `*_to_*` | None (pure) | Trivial |
| **Comparator** | `match_*`, `compare_*` | None (pure) | Trivial |
| **Builder** | `build_*` | None (pure) | Trivial |
| **Serializer** | `serialize_*`, `*_to_dict` | None (pure) | Trivial |
| **Resolver** | `lookup_*`, `resolve_*` | DB read | Easy |
| **Query** | `query_*`, `count_*` | DB read | Easy |
| **Repository** | `save_*`, `update_*`, `delete_*` | DB write | Easy |
| **Command** | action verbs | DB write + event | Medium |
| **Publisher** | `publish_*` | NATS publish | Easy (mock NATS) |
| **Subscriber** | `handle_*`, `on_*` | varies | Medium |
| **Authorizer** | `require_*`, `authorize_*` | None or raise | Easy |
| **Auditor** | `log_audit*` | DB write | Easy |
| **Generator** | `generate_*`, `create_*_token` | Crypto/RNG | Easy |
| **Hasher** | `hash_*`, `verify_*` | CPU only | Easy |
| **Counter** | `check_*`, `increment_*` | Redis I/O | Easy (mock Redis) |

## 3.2 Cross-Cutting Atoms (Shared Library)

These atoms are used by many features. They live in `shared/orw_common/` so they are discoverable and reusable.

### 3.2.1 MAC Address Atoms

| Atom | Pattern | Inputs → Outputs | One-line responsibility |
|------|---------|------------------|------------------------|
| `validate_mac_address` | Validator | `str → bool` | Is this a valid MAC? |
| `normalize_mac_to_colon` | Normalizer | `str → str` | Any format → `aa:bb:cc:dd:ee:ff` |
| `normalize_mac_to_dashed` | Normalizer | `str → str` | Any format → `aa-bb-cc-dd-ee-ff` |
| `normalize_mac_to_dotted` | Normalizer | `str → str` | Any format → `aabb.ccdd.eeff` |
| `mac_to_oui` | Mapper | `str → str` | First 3 octets |
| `lookup_vendor_by_oui` | Resolver | `oui, db → str?` | Vendor name |

### 3.2.2 Username / Realm Atoms

| Atom | Pattern | One-line responsibility |
|------|---------|------------------------|
| `parse_username_realm` | Parser | `user@realm` or `DOM\user` → (user, realm, format) |
| `strip_realm` | Mapper | `user@realm` → `user` |
| `extract_realm` | Mapper | `user@realm` → `realm` |
| `format_upn` | Formatter | (user, realm) → `user@realm` |
| `format_downlevel` | Formatter | (domain, user) → `DOMAIN\user` |

### 3.2.3 Distinguished Name Atoms

| Atom | Pattern | One-line responsibility |
|------|---------|------------------------|
| `parse_dn` | Parser | `CN=x,OU=y,DC=z` → `[(attr, value), ...]` |
| `extract_cn` | Mapper | DN → first CN |
| `extract_ou_path` | Mapper | DN → list of OUs |
| `format_dn` | Formatter | List → DN string |

### 3.2.4 Time Atoms

| Atom | Pattern | One-line responsibility |
|------|---------|------------------------|
| `now_utc` | Generator | UTC datetime |
| `to_iso8601` | Formatter | datetime → ISO string |
| `parse_iso8601` | Parser | ISO string → datetime |
| `is_expired` | Comparator | dt < now? |
| `add_minutes` | Mapper | dt + n minutes |
| `to_unix_timestamp` | Mapper | datetime → epoch |

### 3.2.5 Crypto Atoms

| Atom | Pattern | One-line responsibility |
|------|---------|------------------------|
| `hash_password` | Hasher | bcrypt(12) |
| `verify_password` | Hasher | constant-time compare |
| `generate_jwt` | Generator | HS256 sign |
| `verify_jwt` | Hasher | HS256 verify + decode claims |
| `generate_random_token` | Generator | secrets.token_urlsafe(n) |
| `generate_uuid4` | Generator | UUID v4 |
| `compute_sha256` | Hasher | SHA-256 digest |
| `compute_cert_fingerprint` | Hasher | DER → SHA-256 hex |
| `generate_rsa_keypair` | Generator | New RSA key |
| `sign_certificate` | Generator | CA-signed cert |
| `parse_pem_certificate` | Parser | PEM → x509 |

### 3.2.6 Database Atoms

| Atom | Pattern | One-line responsibility |
|------|---------|------------------------|
| `get_db` | Resolver | DI dependency |
| `get_db_context` | Resolver | Async context manager |
| `build_safe_set_clause` | Builder | Safe `SET col=val,...` |
| `build_pagination` | Builder | `LIMIT x OFFSET y` |
| `build_order_by` | Builder | Validated `ORDER BY` |
| `coerce_ip_address` | Mapper | IPv4Address → str |
| `coerce_macaddr` | Mapper | EUI → str |
| `coerce_uuid` | Mapper | UUID → str |
| `apply_tenant_filter` | Mapper | Add `WHERE tenant_id=$N` |

### 3.2.7 NATS Atoms

| Atom | Pattern | One-line responsibility |
|------|---------|------------------------|
| `nats_connect` | Resolver | Acquire connection |
| `nats_publish` | Publisher | Fire-and-forget message |
| `nats_subscribe` | Subscriber | Bind handler with queue group |
| `ensure_jetstream_stream` | Command | Create stream if absent |
| `delete_stale_consumer` | Command | Clean stale durable |
| `serialize_event` | Mapper | dict → JSON bytes |
| `deserialize_event` | Mapper | JSON bytes → dict |

### 3.2.8 Auth / RBAC Atoms

| Atom | Pattern | One-line responsibility |
|------|---------|------------------------|
| `extract_bearer_token` | Parser | Authorization header → token |
| `decode_token_claims` | Mapper | JWT → claims |
| `get_current_user` | Resolver | Token → User |
| `require_admin` | Authorizer | Raise if not admin |
| `require_operator` | Authorizer | Raise if not operator+ |
| `require_self_or_admin` | Authorizer | Self-edit or admin |
| `check_login_rate` | Counter | Returns bool, increments |
| `check_lockout` | Counter | Returns bool |
| `record_failed_login` | Counter | Increment |
| `clear_lockout` | Counter | Reset |

### 3.2.9 Audit / Logging Atoms

| Atom | Pattern | One-line responsibility |
|------|---------|------------------------|
| `log_audit` | Auditor | Insert audit row |
| `extract_client_ip` | Parser | Headers → IP |
| `format_log_record` | Formatter | Structured JSON line |
| `get_logger` | Resolver | Per-module logger |

### 3.2.10 Error → HTTP Atoms

| Atom | Pattern | One-line responsibility |
|------|---------|------------------------|
| `map_not_found_to_404` | Mapper | NotFoundError → 404 |
| `map_conflict_to_409` | Mapper | ConflictError → 409 |
| `map_validation_to_422` | Mapper | ValidationError → 422 |
| `map_auth_to_401` | Mapper | AuthenticationError → 401 |
| `map_authz_to_403` | Mapper | AuthorizationError → 403 |
| `map_rate_limit_to_429` | Mapper | RateLimitError → 429 |
| `map_unhandled_to_500` | Mapper | Generic 500 + log |

### 3.2.11 Pagination / Filtering Atoms

| Atom | Pattern | One-line responsibility |
|------|---------|------------------------|
| `parse_pagination` | Validator | Bound limit/offset |
| `parse_time_range` | Validator | Build time filter |
| `wrap_paginated_response` | Mapper | Standard envelope |

**Cross-cutting subtotal: ~80 atoms**

## 3.3 Feature-Specific Atoms

### 3.3.1 Local User Authentication (Group 1)

`POST /auth/login` decomposes into 12 atoms; user-management endpoints reuse most.

| # | Atom | Pattern | Responsibility |
|---|------|---------|---------------|
| 1 | `parse_login_payload` | Validator | Username + password present |
| 2 | `lookup_user_by_username` | Resolver | DB read scoped by tenant |
| 3 | `update_last_login` | Repository | Set timestamp |
| 4 | `serialize_login_response` | Serializer | Token + user shape |
| 5 | `validate_user_create` | Validator | New user payload |
| 6 | `validate_user_update` | Validator | Partial update payload |
| 7 | `check_username_unique` | Validator | DB check |
| 8 | `insert_user` | Repository | Create row |
| 9 | `lookup_user_by_id` | Resolver | DB read |
| 10 | `apply_user_changes` | Mapper | Diff existing + new |
| 11 | `update_user` | Repository | Patch row |
| 12 | `delete_user` | Repository | Remove row |
| 13 | `query_users_by_tenant` | Query | Paged list |
| 14 | `count_users` | Query | Total count |
| 15 | `update_password` | Repository | Hash + persist |
| 16 | `serialize_user` | Serializer | DTO shape |
| 17 | `static_role_matrix` | Resolver | Static permission table |

**Group 1 subtotal: 17 atoms (+ shared 12)**

### 3.3.2 Device Inventory (Group 2)

| Atom | Pattern | Responsibility |
|------|---------|---------------|
| `validate_device_create` | Validator | Required fields, MAC format |
| `validate_device_update` | Validator | Partial update |
| `lookup_device_by_mac` | Resolver | DB read |
| `lookup_device_by_id` | Resolver | DB read |
| `query_devices_by_filter` | Query | Paged list |
| `count_devices_by_filter` | Query | Total |
| `upsert_device` | Repository | INSERT...ON CONFLICT |
| `update_device_fields` | Repository | Partial update |
| `delete_device` | Repository | Remove |
| `add_device_property` | Repository | EAV insert |
| `query_device_properties_by_category` | Query | EAV read |
| `serialize_device` | Serializer | DTO |
| `serialize_device_list_item` | Serializer | Compact DTO |
| `serialize_device_property` | Serializer | DTO |
| `publish_device_event` | Publisher | NATS emit |

**Group 2 subtotal: 15 atoms**

### 3.3.3 Device Discovery (Group 3)

| Atom | Pattern | Responsibility |
|------|---------|---------------|
| `start_arp_listener` | Subscriber | Bind to ARP frames |
| `parse_arp_packet` | Parser | Extract src MAC + IP |
| `start_dhcp_listener` | Subscriber | UDP/67-68 bind |
| `parse_dhcp_ack` | Parser | MAC, IP, hostname |
| `parse_dhcp_option_55` | Parser | Param request list |
| `parse_dhcp_option_60` | Parser | Vendor class ID |
| `run_nmap_scan` | Command | subprocess wrapper |
| `parse_nmap_xml` | Parser | Hosts + ports + services |
| `run_snmp_walk` | Command | SNMP query |
| `parse_snmp_sysdescr` | Parser | Vendor/model/version |
| `classify_device_type` | Mapper | Heuristic → type |
| `detect_os_family` | Mapper | Fingerprint → OS |
| `build_device_payload` | Builder | Discovery → device dict |
| `publish_device_discovered` | Publisher | NATS emit |
| `handle_scan_request` | Subscriber | Trigger active scan |

**Group 3 subtotal: 15 atoms**

### 3.3.4 Policy Engine (Group 4)

**Evaluator atoms (pure):**
| Atom | Pattern | Responsibility |
|------|---------|---------------|
| `resolve_field` | Mapper | Dotted path → value |
| `resolve_alias` | Mapper | Field alias |
| `match_equals` | Comparator | == |
| `match_not_equals` | Comparator | != |
| `match_in` | Comparator | ∈ list |
| `match_not_in` | Comparator | ∉ list |
| `match_contains` | Comparator | substring/membership |
| `match_starts_with` | Comparator | prefix |
| `match_ends_with` | Comparator | suffix |
| `match_gt` | Comparator | > |
| `match_lt` | Comparator | < |
| `match_regex` | Comparator | regex test |
| `match_is_null` | Comparator | null test |
| `evaluate_condition` | Mapper | Single condition → bool |
| `evaluate_policy_and` | Mapper | All-must-match |
| `select_actions` | Mapper | match/no-match action set |

**Worker atoms (effectful):**
| Atom | Pattern | Responsibility |
|------|---------|---------------|
| `handle_evaluate_device` | Subscriber | NATS handler |
| `load_device_context` | Query | Device + properties → ctx dict |
| `load_active_policies` | Query | Priority order |
| `record_policy_evaluation` | Repository | Insert eval row |
| `dispatch_action` | Command | Route to handler |
| `dispatch_vlan_assign` | Publisher | switch.set_vlan |
| `dispatch_acl_apply` | Publisher | switch.apply_acl |
| `dispatch_quarantine` | Command | Move to quarantine |
| `dispatch_coa` | Publisher | coa.send |

**Gateway atoms:**
| Atom | Pattern | Responsibility |
|------|---------|---------------|
| `validate_policy_create` | Validator | Conditions + actions valid |
| `validate_condition_field` | Validator | Field is in catalog |
| `validate_condition_operator` | Validator | Operator valid for field |
| `validate_action_type` | Validator | Action type known |
| `validate_action_params` | Validator | Per-action schema |
| `query_policies_by_filter` | Query | Paged + ordered |
| `lookup_policy_by_id` | Resolver | DB read |
| `insert_policy` | Repository | Create |
| `update_policy` | Repository | Patch |
| `delete_policy` | Repository | Remove |
| `serialize_policy` | Serializer | DTO |
| `list_policy_templates` | Resolver | Static list |
| `apply_policy_template` | Builder | Template + overrides → policy |
| `simulate_policy` | Mapper | Policy + ctx → eval result |
| `simulate_all_policies` | Mapper | All policies + ctx |

**Group 4 subtotal: 40 atoms**

### 3.3.5 RADIUS Authentication / FreeRADIUS Module (Group 5)

| Atom | Pattern | Responsibility |
|------|---------|---------------|
| `instantiate` | Subscriber | FR module load hook |
| `authorize` | Subscriber | Pre-auth phase entry |
| `post_auth` | Subscriber | Post-auth phase entry |
| `accounting` | Subscriber | Accounting hook |
| `detach` | Subscriber | FR module unload |
| `extract_attrs` | Mapper | Tuple list → dict |
| `detect_auth_method` | Mapper | Heuristic → "MAB"/"PAP"/EAP |
| `is_mab_request` | Comparator | Boolean |
| `is_eap_request` | Comparator | Boolean |
| `map_eap_type_id` | Mapper | int → name |
| `map_ad_error_code` | Mapper | "775" → reason+desc |
| `map_mschap_error` | Mapper | "E=691" → reason |
| `detect_failure_reason` | Mapper | Best failure description |
| `extract_cert_attrs` | Parser | TLS-Client-Cert-* → dict |
| `extract_vlan_from_reply` | Parser | Tunnel-Private-Group-Id → int |
| `parse_username_components` | Parser | "DOM\\user@realm" |
| `lookup_mab_device` | Resolver | DB query |
| `is_mab_expired` | Comparator | Boolean |
| `build_mab_accept_reply` | Builder | Attribute tuple |
| `lookup_ldap_server` | Resolver | DB query |
| `bind_ldap` | Resolver | Network call |
| `search_user_groups` | Resolver | LDAP search |
| `extract_groups_from_memberOf` | Parser | DN list → CN list |
| `lookup_vlan_for_groups` | Resolver | DB priority match |
| `build_vlan_assign_reply` | Builder | Tunnel attr tuple |
| `build_auth_log_row` | Builder | dict for INSERT |
| `insert_auth_log` | Repository | DB write |
| `radlog_emit` | Publisher | FR log |
| `query_radius_auth_log` | Query | Paged history |
| `parse_auth_log_filter` | Parser | URL params → filter |
| `serialize_auth_log_entry` | Serializer | DTO |

**Group 5 subtotal: 31 atoms**

### 3.3.6 Dynamic VLAN (Group 6)

| Atom | Pattern | Responsibility |
|------|---------|---------------|
| `validate_vlan_id` | Validator | 1..4094 |
| `validate_priority` | Validator | 1..9999 |
| `check_group_name_unique` | Validator | DB check |
| `validate_ldap_server_exists` | Validator | FK check |
| `lookup_group_vlan_mapping` | Resolver | DB read |
| `query_group_vlan_mappings` | Query | List |
| `insert_group_vlan_mapping` | Repository | Create |
| `update_group_vlan_mapping` | Repository | Patch |
| `delete_group_vlan_mapping` | Repository | Remove |
| `lookup_vlan_for_groups_by_priority` | Resolver | First-match |
| `serialize_group_vlan_mapping` | Serializer | DTO |

**Group 6 subtotal: 11 atoms**

### 3.3.7 MAB (Group 7)

| Atom | Pattern | Responsibility |
|------|---------|---------------|
| `validate_mab_create` | Validator | MAC + VLAN valid |
| `validate_mab_expiry` | Validator | Future date |
| `check_mac_unique_in_mab` | Validator | DB check |
| `lookup_mab_by_mac` | Resolver | DB read |
| `query_mab_devices` | Query | Paged list |
| `insert_mab_device` | Repository | Create |
| `update_mab_device` | Repository | Patch |
| `delete_mab_device` | Repository | Remove |
| `is_mab_currently_valid` | Comparator | enabled AND not expired |
| `serialize_mab_device` | Serializer | DTO |

**Group 7 subtotal: 10 atoms**

### 3.3.8 CoA (Group 8)

| Atom | Pattern | Responsibility |
|------|---------|---------------|
| `validate_coa_action` | Validator | Action enum |
| `validate_coa_target` | Validator | mac/user/session_id |
| `find_active_sessions_by_mac` | Query | DB |
| `find_active_sessions_by_username` | Query | DB |
| `find_session_by_id` | Resolver | DB |
| `lookup_nas_for_session` | Resolver | DB |
| `build_coa_request_packet` | Builder | RADIUS packet bytes |
| `sign_radius_coa` | Hasher | Message-Authenticator |
| `send_coa_packet` | Command | UDP/3799 |
| `parse_coa_response` | Parser | Bytes → response |
| `is_coa_ack` | Comparator | Boolean |
| `extract_coa_error` | Mapper | NAK code → reason |
| `record_coa_event` | Auditor | Audit log |
| `query_coa_history` | Query | Audit |
| `query_active_sessions` | Query | Auth log |
| `serialize_coa_result` | Serializer | DTO |
| `serialize_active_session` | Serializer | DTO |

**Group 8 subtotal: 17 atoms**

### 3.3.9 LDAP Servers (Group 9, partial)

| Atom | Pattern | Responsibility |
|------|---------|---------------|
| `validate_ldap_create` | Validator | Required fields |
| `validate_ldap_url` | Validator | host/port/TLS valid |
| `encrypt_bind_password` | Hasher | Vault store |
| `decrypt_bind_password` | Hasher | Vault retrieve |
| `lookup_ldap_server` | Resolver | DB read |
| `query_ldap_servers` | Query | List |
| `insert_ldap_server` | Repository | Create |
| `update_ldap_server` | Repository | Patch |
| `delete_ldap_server` | Repository | Remove |
| `test_ldap_bind` | Resolver | Network call |
| `test_ldap_search` | Resolver | Network call |
| `record_ldap_test_result` | Repository | Save outcome |
| `strip_password_from_dto` | Mapper | Security mask |
| `serialize_ldap_server` | Serializer | DTO |

**Group 9 LDAP subtotal: 14 atoms**

### 3.3.10 RADIUS Realms (Group 9, continued)

| Atom | Pattern | Responsibility |
|------|---------|---------------|
| `validate_realm_name` | Validator | Format/uniqueness |
| `check_realm_chain_no_cycle` | Validator | Recursive check |
| `resolve_realm_chain` | Resolver | Recursive |
| `lookup_realm_by_id` | Resolver | DB |
| `query_realms` | Query | List |
| `insert_realm` | Repository | Create |
| `update_realm` | Repository | Patch |
| `delete_realm` | Repository | Remove |
| `serialize_realm` | Serializer | DTO |

**Realm subtotal: 9 atoms**

### 3.3.11 NAS Clients (Group 9)

| Atom | Pattern | Responsibility |
|------|---------|---------------|
| `validate_nas_create` | Validator | Fields |
| `validate_nas_ip` | Validator | IP/CIDR |
| `encrypt_shared_secret` | Hasher | Vault store |
| `decrypt_shared_secret` | Hasher | Vault retrieve |
| `check_nas_ip_unique` | Validator | DB check |
| `lookup_nas_by_id` | Resolver | DB |
| `query_nas_clients` | Query | List |
| `insert_nas_client` | Repository | Create |
| `update_nas_client` | Repository | Patch |
| `delete_nas_client` | Repository | Remove |
| `serialize_nas_client` | Serializer | DTO |

**NAS subtotal: 11 atoms**

### 3.3.12 VLANs (Group 9)

| Atom | Pattern | Responsibility |
|------|---------|---------------|
| `validate_vlan_create` | Validator | Fields |
| `validate_vlan_purpose` | Validator | Enum |
| `validate_subnet_cidr` | Validator | CIDR format |
| `check_vlan_id_unique` | Validator | DB check |
| `lookup_vlan_by_id` | Resolver | DB |
| `query_vlans` | Query | List |
| `insert_vlan` | Repository | Create |
| `update_vlan` | Repository | Patch |
| `delete_vlan` | Repository | Remove |
| `serialize_vlan` | Serializer | DTO |

**VLAN subtotal: 10 atoms**

### 3.3.13 FreeRADIUS Config Generator (Group 9)

| Atom | Pattern | Responsibility |
|------|---------|---------------|
| `render_clients_conf` | Builder | Jinja → string |
| `render_eap_conf` | Builder | Jinja → string |
| `render_ldap_conf` | Builder | Jinja → string |
| `render_proxy_conf` | Builder | Jinja → string |
| `render_python_conf` | Builder | Jinja → string |
| `render_site_default` | Builder | Jinja → string |
| `render_site_inner_tunnel` | Builder | Jinja → string |
| `compute_config_hash` | Hasher | SHA-256 |
| `compare_config_hash` | Comparator | Drift detect |
| `write_config_file` | Command | I/O |
| `record_config_state` | Repository | Track hash |
| `send_sighup_to_freeradius` | Command | docker exec kill -HUP |
| `handle_config_apply_message` | Subscriber | NATS handler |

**Config gen subtotal: 13 atoms**

### 3.3.14 Certificates (Group 10)

| Atom | Pattern | Responsibility |
|------|---------|---------------|
| `validate_pem_format` | Validator | Boolean |
| `parse_pem_certificate` | Parser | x509 object |
| `extract_cert_subject` | Mapper | CN/O/OU |
| `extract_cert_issuer` | Mapper | Issuer DN |
| `extract_cert_serial` | Mapper | Serial number |
| `extract_cert_not_after` | Mapper | Expiry datetime |
| `extract_cert_san` | Mapper | SAN list |
| `compute_cert_fingerprint` | Hasher | SHA-256 hex |
| `cert_expiry_status` | Mapper | "expired"/"expiring"/"valid" |
| `generate_rsa_key` | Generator | RSA |
| `build_csr` | Builder | CSR object |
| `self_sign_certificate` | Generator | CA |
| `ca_sign_certificate` | Generator | Server cert |
| `serialize_cert_pem` | Serializer | PEM bytes |
| `serialize_cert_der` | Serializer | DER bytes |
| `lookup_certificate_by_id` | Resolver | DB |
| `query_certificates` | Query | List |
| `insert_certificate` | Repository | Create |
| `delete_certificate` | Repository | Remove |
| `serialize_cert_response` | Serializer | DTO |

**Group 10 subtotal: 20 atoms**

### 3.3.15 Switch Management (Group 11)

**Vendor adapters (per vendor × per command type):**
| Atom | Pattern | Responsibility |
|------|---------|---------------|
| `cisco_ios_set_vlan_command` | Builder | CLI string |
| `cisco_ios_bounce_port_command` | Builder | CLI string |
| `cisco_ios_show_mac_table_command` | Builder | CLI string |
| `cisco_xe_set_vlan_command` | Builder | (variant) |
| `cisco_nxos_set_vlan_command` | Builder | (variant) |
| `aruba_set_vlan_command` | Builder | CLI string |
| `aruba_cx_set_vlan_command` | Builder | (variant) |
| `juniper_set_vlan_command` | Builder | CLI string |
| `hp_procurve_show_mac_command` | Builder | CLI string |
| `dell_set_vlan_command` | Builder | CLI string |
| `extreme_set_vlan_command` | Builder | CLI string |

**SSH atoms:**
| Atom | Pattern | Responsibility |
|------|---------|---------------|
| `ssh_connect` | Resolver | Network |
| `ssh_send_config` | Command | Apply lines |
| `ssh_send_command` | Resolver | Run query |
| `ssh_disconnect` | Command | Cleanup |
| `parse_show_mac_output` | Parser | Per-vendor |

**SNMP atoms:**
| Atom | Pattern | Responsibility |
|------|---------|---------------|
| `snmp_get` | Resolver | Network |
| `snmp_walk` | Resolver | Network |
| `snmp_set_vlan` | Command | Network |
| `parse_snmp_oid_response` | Parser | Varbind |
| `parse_oid_to_port_index` | Parser | OID → port |

**Worker atoms:**
| Atom | Pattern | Responsibility |
|------|---------|---------------|
| `handle_set_vlan_message` | Subscriber | NATS |
| `handle_bounce_port_message` | Subscriber | NATS |
| `handle_poll_requested_message` | Subscriber | NATS |
| `lookup_switch_credentials` | Resolver | DB |
| `select_vendor_adapter` | Mapper | vendor → adapter |
| `record_command_result` | Repository | Audit |
| `publish_port_state_changed` | Publisher | NATS |

**Group 11 subtotal: 28 atoms**

### 3.3.16 Audit Log (Group 12)

| Atom | Pattern | Responsibility |
|------|---------|---------------|
| `parse_audit_filter` | Parser | URL params → filter |
| `query_audit_by_filter` | Query | Paged |
| `count_audit_entries` | Query | Total |
| `serialize_audit_csv` | Serializer | CSV bytes |
| `serialize_audit_json` | Serializer | JSON |
| `serialize_audit_entry` | Serializer | DTO |

**Group 12 subtotal: 6 atoms**

### 3.3.17 802.1X Overview (Group 13)

| Atom | Pattern | Responsibility |
|------|---------|---------------|
| `count_active_radius_sessions` | Query | Aggregate |
| `count_auth_log_by_result` | Query | Aggregate |
| `compute_success_rate` | Mapper | Percentage |
| `count_nas_clients` | Query | Aggregate |
| `count_ldap_servers` | Query | Aggregate |
| `count_realms` | Query | Aggregate |
| `count_certificates_by_status` | Query | Aggregate |
| `count_mab_devices` | Query | Aggregate |
| `count_group_vlan_mappings` | Query | Aggregate |
| `build_dot1x_overview_response` | Builder | Compose |

**Group 13 subtotal: 10 atoms**

### 3.3.18 Event Service (Group 14)

| Atom | Pattern | Responsibility |
|------|---------|---------------|
| `handle_device_evaluated` | Subscriber | NATS |
| `handle_policy_action` | Subscriber | NATS |
| `record_event` | Repository | Insert |
| `forward_to_wazuh` | Publisher | HTTP push |
| `query_ad_event_log` | Resolver | AD HTTP/LDAP |
| `parse_ad_event` | Parser | Event → struct |
| `correlate_event_to_device` | Mapper | Event ↔ device |

**Group 14 subtotal: 7 atoms**

### 3.3.19 Settings (Group 15)

| Atom | Pattern | Responsibility |
|------|---------|---------------|
| `validate_settings_update` | Validator | Per-key schema |
| `lookup_system_settings` | Resolver | DB read |
| `update_system_settings` | Repository | Upsert |
| `serialize_settings` | Serializer | DTO |

**Group 15 subtotal: 4 atoms**

### 3.3.20 Health (Group 16)

| Atom | Pattern | Responsibility |
|------|---------|---------------|
| `check_db_alive` | Resolver | Ping |
| `check_redis_alive` | Resolver | Ping |
| `check_nats_alive` | Resolver | Ping |
| `build_health_response` | Builder | Compose |

**Group 16 subtotal: 4 atoms**

## 3.4 Frontend Atoms (React)

For each page, atoms break into:
- **Hooks** (Resolvers) — `useDevices()`, `usePolicies()`
- **Form validators** (Validators) — Ant rules
- **Cell renderers** (Mappers) — `renderStatusBadge`, `formatMacForDisplay`
- **Action handlers** (Commands) — `handleCreate`, `handleEdit`, `handleDelete`
- **URL parsers/serializers** — `buildFilterFromUrl`, `serializeFilterToUrl`
- **Presentational components** — leaf UI

**Per-page atoms (typical):** 10–15
**Total pages:** 18
**Frontend atoms total:** ~150

## 3.5 Atomic Module Statistics

| Category | Count |
|----------|-------|
| Cross-cutting (shared library) | ~80 |
| Group 1 — Auth/User | 17 |
| Group 2 — Device Inventory | 15 |
| Group 3 — Discovery | 15 |
| Group 4 — Policy Engine | 40 |
| Group 5 — RADIUS Auth | 31 |
| Group 6 — Dynamic VLAN | 11 |
| Group 7 — MAB | 10 |
| Group 8 — CoA | 17 |
| Group 9 — RADIUS Config (LDAP+Realm+NAS+VLAN+FRConfig) | 57 |
| Group 10 — Certificates | 20 |
| Group 11 — Switch Mgmt | 28 |
| Group 12 — Audit | 6 |
| Group 13 — 802.1X Overview | 10 |
| Group 14 — Event Service | 7 |
| Group 15 — Settings | 4 |
| Group 16 — Health | 4 |
| Frontend (18 pages × ~10) | ~150 |
| **Total atomic modules** | **~600** |

---

# Part 4 — Composition Flows

This section shows how atoms compose into user-facing features. Each flow is a sequenced list of atom calls.

## 4.1 Worked Example: `POST /devices`

```
Request: POST /devices
Body: { mac_address, hostname, vendor?, type?, ... }

  1. extract_bearer_token(req)              [Parser]
  2. decode_token_claims(token)             [Mapper]
  3. get_current_user(claims)               [Resolver, DB]
  4. require_operator(user)                 [Authorizer]
  5. parse_device_create_payload(body)      [Validator]
  6. validate_mac_address(payload.mac)      [Validator]
  7. normalize_mac_to_colon(payload.mac)    [Normalizer]
  8. extract_tenant_id(user)                [Mapper]
  9. check_mac_unique_in_tenant(db, ...)    [Validator, DB]
 10. build_device_insert_row(payload, t)    [Builder]
 11. upsert_device(db, row)                 [Repository, DB]
 12. serialize_event_payload(device)        [Mapper]
 13. publish_device_event(payload)          [Publisher, NATS]
 14. log_audit("create","device",id,user)   [Auditor, DB]
 15. serialize_device_response(device)      [Serializer]
 16. wrap_response(201, body)               [Mapper]

16 atoms. 7 pure, 4 DB ops, 1 NATS.
```

## 4.2 Worked Example: `POST /auth/login`

```
  1. parse_login_payload(body)              [Validator]
  2. extract_client_ip(req)                 [Parser]
  3. check_login_rate(ip)                   [Counter, Redis]
  4. check_lockout(username)                [Counter, Redis]
  5. lookup_user_by_username(db, name)      [Resolver, DB]
  6. verify_password(plain, hash)           [Hasher]
  7a. (if fail) record_failed_login(name)   [Counter, Redis]
  7b. (if pass) clear_lockout(name)         [Counter, Redis]
  8. update_last_login(db, user_id)         [Repository, DB]
  9. generate_jwt({sub, role, tenant})      [Generator, Crypto]
 10. log_audit("login","user",id,user)      [Auditor, DB]
 11. serialize_login_response(token, user)  [Serializer]
 12. wrap_response(200, body)               [Mapper]
```

## 4.3 Worked Example: `POST /coa/by-mac`

```
  1. require_operator(user)                  [Authorizer]
  2. parse_coa_payload(body)                 [Validator]
  3. validate_coa_action(action)             [Validator]
  4. normalize_mac_to_colon(mac)             [Normalizer]
  5. find_active_sessions_by_mac(db, mac)    [Query, DB]
  6. (per session)
       6a. lookup_nas_for_session(s)         [Resolver, DB]
       6b. build_coa_request_packet(...)     [Builder]
       6c. sign_radius_coa(packet, secret)   [Hasher]
       6d. send_coa_packet(nas_ip,3799,pkt)  [Command, UDP]
       6e. parse_coa_response(bytes)         [Parser]
       6f. is_coa_ack(resp)                  [Comparator]
       6g. record_coa_event(db, ...)         [Auditor, DB]
  7. log_audit("coa.by_mac",...)             [Auditor, DB]
  8. serialize_coa_result(results)           [Serializer]
  9. wrap_response(200, body)                [Mapper]
```

## 4.4 Worked Example: RADIUS Access-Request → Access-Accept (FreeRADIUS)

```
Phase: authorize()
  1. extract_attrs(p)                        [Mapper]
  2. parse_username_components(name)         [Parser]
  3. detect_auth_method(req, {})             [Mapper]
  4. (if MAB)
       4a. normalize_mac_to_colon(mac)       [Normalizer]
       4b. lookup_mab_device(db, mac, tenant)[Resolver, DB]
       4c. is_mab_currently_valid(device)    [Comparator]
       4d. build_mab_accept_reply(vlan_id)   [Builder]
       4e. return RLM_OK + reply             [native]
  5. return RLM_OK + realm                   [native]

Phase: post_auth() (on success)
  1. extract_attrs(p)                        [Mapper]
  2. detect_auth_method(req, {})             [Mapper]
  3. (if 802.1X)
       3a. lookup_ldap_server(db, tenant)    [Resolver, DB]
       3b. bind_ldap(server, dn, pw)         [Resolver, NET]
       3c. search_user_groups(conn, ...)     [Resolver, NET]
       3d. extract_groups_from_memberOf(...) [Parser]
       3e. lookup_vlan_for_groups(db, grps)  [Resolver, DB]
       3f. build_vlan_assign_reply(vlan)     [Builder]
  4. build_auth_log_row(req, reply, ...)     [Builder]
  5. insert_auth_log(db, row)                [Repository, DB]
  6. radlog_emit("INFO", msg)                [Publisher, FR]
  7. return RLM_UPDATED + reply              [native]
```

## 4.5 Worked Example: Device Discovered → Policy Action

```
Trigger: PassiveMonitor sees ARP reply

discovery service:
  1. parse_arp_packet(frame)                 [Parser]
  2. build_device_payload(raw)               [Builder]
  3. publish_device_discovered(payload)      [Publisher, NATS]

device_inventory service:
  4. handle_device_discovered(msg)           [Subscriber]
  5. deserialize_event(bytes)                [Mapper]
  6. normalize_mac_to_colon(mac)             [Normalizer]
  7. upsert_device(db, payload)              [Repository, DB]
  8. add_device_property(...)                [Repository, DB]
  9. record_event(db, "device_discovered")   [Repository, DB]
 10. publish_evaluate_device(device_id)      [Publisher, NATS]

policy_engine service:
 11. handle_evaluate_device(msg)             [Subscriber]
 12. load_device_context(device_id)          [Query, DB]
 13. load_active_policies(tenant_id)         [Query, DB]
 14. (per policy, in priority order)
       14a. evaluate_policy_and(conds, ctx)  [Mapper]
       14b. record_policy_evaluation(...)    [Repository, DB]
       14c. (if matched) select_actions()    [Mapper]
       14d. (per action) dispatch_action()   [Command]
              → dispatch_vlan_assign         [Publisher, NATS]
              → dispatch_coa                 [Publisher, NATS]

switch_mgmt service:
 15. handle_set_vlan_message(msg)            [Subscriber]
 16. lookup_switch_credentials(db, dev_id)   [Resolver, DB]
 17. select_vendor_adapter(vendor)           [Mapper]
 18. cisco_ios_set_vlan_command(port, vlan)  [Builder]
 19. ssh_connect(host, user, pw, type)       [Resolver, NET]
 20. ssh_send_config(conn, cmds)             [Command, NET]
 21. ssh_disconnect(conn)                    [Command, NET]
 22. record_command_result(db, ...)          [Repository, DB]
 23. publish_port_state_changed(...)         [Publisher, NATS]
```

This 23-atom flow spans 4 microservices and 5 different side-effect categories (DB read, DB write, NATS publish, NATS subscribe, network).

---

# Part 5 — Development Conventions

## 5.1 The 8 Rules of Atomic Code

| # | Rule | Why |
|---|------|-----|
| 1 | **One reason to change** per atom | Changes don't ripple |
| 2 | **One return shape** per atom | No polymorphic returns |
| 3 | **One side-effect category** per atom | DB-read OR DB-write OR NATS, never mixed |
| 4 | **Pure atoms in `shared/orw_common`** | Discoverable & reusable |
| 5 | **Effectful atoms in service-local module** | Clear ownership |
| 6 | **Name by responsibility, not implementation** | `validate_mac_address`, not `is_six_octets_hex` |
| 7 | **No "and" in function name** | If you must, split into two atoms |
| 8 | **Test pure atoms exhaustively, mock at I/O boundary** | 80% coverage with simple tests |

## 5.2 Anti-Patterns (Refactor on Sight)

| Smell | Refactor to |
|-------|-------------|
| Function does validation AND DB write | validator + repository |
| Function returns AND publishes NATS | command (returns) + publisher |
| Helper takes >5 parameters | extract a value object |
| Function name contains "and" | two atoms |
| Function uses both `db` and `redis` | two atoms |
| Function reads DB, calls LDAP, writes DB | three atoms |
| One function logs to audit AND emits to logger | auditor + logger are different |

## 5.3 New Feature Development Checklist

When adding a new feature, follow this discipline:

- [ ] **Define the user-facing endpoint** (HTTP method + path)
- [ ] **Sketch the atomic flow** (numbered list like Part 4) BEFORE writing code
- [ ] **Search shared library** for reusable atoms
- [ ] **Identify new atoms needed** (each named by responsibility)
- [ ] **Write pure atoms first** (validators, builders, mappers)
- [ ] **Unit-test each pure atom**
- [ ] **Write effectful atoms next** (repositories, publishers)
- [ ] **Integration-test the full flow** with one happy + one sad path
- [ ] **Add audit logging atom call** for every mutation
- [ ] **Document in this manual** under appropriate Group

## 5.4 File Layout

**Standard layout is feature-oriented (`features/<name>/`).** New features must use it; the existing flat `routes/` is a transitional state to be migrated per §10.6.3.

```
shared/orw_common/          # Cross-cutting atoms (pure + crypto)
  ├── config.py             # Settings (no atoms — DI only)
  ├── database.py           # DB resolvers
  ├── nats_client.py        # NATS publisher/subscriber atoms
  ├── exceptions.py         # Error types
  ├── logging.py            # Logger resolver
  ├── policy_evaluator.py   # Policy comparison atoms (pure)
  └── models/               # Pydantic value objects

services/<svc>/
  ├── main.py               # Entry point (FastAPI app for gateway; NATS subscriber registration for others)
  ├── features/             # Feature-oriented layout (standard)
  │   └── <feature>/
  │       ├── routes.py     # Layer 3 — REST routes (gateway only)
  │       ├── service.py    # Layer 2 — use-case composition
  │       ├── repository.py # Layer 2 — DB read/write atoms
  │       ├── events.py     # Layer 2 — NATS publisher/subscriber atoms
  │       ├── schemas.py    # Pydantic request/response/event models
  │       ├── __init__.py   # Explicit public API (only what other features may import)
  │       └── tests/        # Unit + integration tests
  ├── middleware/           # Cross-feature (gateway only) — auth, request_id, etc.
  └── utils/                # Service-local helpers (rare; prefer placing inside the relevant feature/)
```

Each `features/<name>/` is self-contained and end-to-end ownable by one team. See §10.6 for rationale and cross-feature communication rules.

## 5.5 Naming Conventions

| Type | Prefix/Suffix | Example |
|------|---------------|---------|
| Validator | `validate_*` | `validate_mac_address` |
| Normalizer | `normalize_*` | `normalize_mac_to_colon` |
| Parser | `parse_*` | `parse_username_realm` |
| Formatter | `format_*` | `format_dn` |
| Mapper | `*_to_*` | `device_row_to_dto` |
| Comparator | `match_*`, `is_*` | `match_regex`, `is_expired` |
| Builder | `build_*` | `build_safe_set_clause` |
| Serializer | `serialize_*` | `serialize_device` |
| Resolver | `lookup_*`, `resolve_*` | `lookup_user_by_id` |
| Query | `query_*`, `count_*` | `query_devices_by_filter` |
| Repository | verb (insert/update/delete/upsert/save) | `insert_device` |
| Command | action verb | `disconnect_session` |
| Publisher | `publish_*` | `publish_device_discovered` |
| Subscriber | `handle_*`, `on_*` | `handle_evaluate_device` |
| Authorizer | `require_*` | `require_admin` |
| Auditor | `log_audit*` | `log_audit` |
| Generator | `generate_*` | `generate_jwt` |
| Hasher | `hash_*`, `verify_*` | `hash_password` |
| Counter | `check_*`, `increment_*` | `check_login_rate` |

## 5.6 Testing Strategy by Atom Type

| Atom Type | Test Strategy | Coverage Target |
|-----------|---------------|-----------------|
| Validator | Table-driven (good/bad pairs) | 100% |
| Normalizer | Table-driven (input → output) | 100% |
| Parser | Table-driven + fuzz | 95% |
| Formatter | Table-driven | 100% |
| Mapper | Table-driven | 100% |
| Comparator | Table-driven | 100% |
| Builder | Table-driven | 100% |
| Serializer | Snapshot tests | 90% |
| Resolver | Mock DB / fake repo | 80% |
| Query | Integration test on test DB | 70% |
| Repository | Integration test on test DB | 80% |
| Command | Integration test (DB + NATS) | 70% |
| Publisher | Mock NATS, assert subject + payload | 90% |
| Subscriber | Inject fake message | 80% |
| Authorizer | Table-driven (role × resource) | 100% |
| Auditor | Verify INSERT happened | 90% |
| Generator | Mock RNG; verify shape + signing | 95% |
| Hasher | Verify roundtrip + constant-time | 95% |
| Counter | Mock Redis; verify increment | 90% |

---

# Part 6 — Quick Reference Index

## 6.1 Atom Lookup by Domain

**MAC addresses** → §3.2.1
**Usernames / realms** → §3.2.2
**LDAP DNs** → §3.2.3
**Time / dates** → §3.2.4
**Crypto / JWT / cert** → §3.2.5
**Database** → §3.2.6
**NATS** → §3.2.7
**Auth / RBAC** → §3.2.8
**Audit / log** → §3.2.9
**Errors → HTTP** → §3.2.10
**Pagination** → §3.2.11

## 6.2 Atom Lookup by Feature

| Feature | Section |
|---------|---------|
| Login / users | §3.3.1 |
| Devices | §3.3.2 |
| Discovery | §3.3.3 |
| Policies | §3.3.4 |
| RADIUS auth (FreeRADIUS hooks) | §3.3.5 |
| Dynamic VLAN | §3.3.6 |
| MAB | §3.3.7 |
| CoA | §3.3.8 |
| LDAP servers | §3.3.9 |
| RADIUS realms | §3.3.10 |
| NAS clients | §3.3.11 |
| VLANs | §3.3.12 |
| FreeRADIUS config | §3.3.13 |
| Certificates | §3.3.14 |
| Switch management | §3.3.15 |
| Audit log | §3.3.16 |
| 802.1X overview | §3.3.17 |
| Event service | §3.3.18 |
| Settings | §3.3.19 |
| Health | §3.3.20 |

## 6.3 Worked Composition Flows

| Flow | Section | # Atoms |
|------|---------|---------|
| `POST /devices` | §4.1 | 16 |
| `POST /auth/login` | §4.2 | 12 |
| `POST /coa/by-mac` | §4.3 | 9+ (per-session 7) |
| RADIUS Access-Request → Accept | §4.4 | ~15 |
| Device discovered → policy action | §4.5 | 23 |

## 6.4 Statistics Quick View

| Metric | Count |
|--------|-------|
| HTTP endpoints | 107 |
| NATS subjects | 8 |
| Microservices | 8 |
| Frontend pages | 18 |
| Database tables | 17 |
| Pydantic models | 14 |
| FreeRADIUS templates | 7 |
| Switch vendor adapters | 7 |
| **Atomic modules (total)** | **~600** |
| — Pure atoms (no I/O) | ~180 |
| — DB-read atoms | ~70 |
| — DB-write atoms | ~50 |
| — NATS publishers | ~15 |
| — NATS subscribers | ~20 |
| — Network atoms (LDAP/SNMP/SSH/UDP) | ~25 |
| — Crypto atoms | ~12 |
| — Frontend atoms | ~150 |

---

---

# Part 7 — API Specification (OpenAPI)

The HTTP API is documented in **OpenAPI 3.0** format. There are three sources:

| Source | URL / Path | Use case |
|--------|-----------|----------|
| **Auto-generated (live)** | `http://<host>:8000/docs` (Swagger UI) | Interactive exploration |
| **Auto-generated (live)** | `http://<host>:8000/openapi.json` | Tooling / SDK generation |
| **Static spec file** | [`docs/api/openapi.yaml`](api/openapi.yaml) | Version-controlled reference |

## 7.1 Conventions

| Convention | Detail |
|-----------|--------|
| **Base URL** | `/api/v1` |
| **Authentication** | `Authorization: Bearer <jwt>` (HS256, 60-min expiry) |
| **Content-Type** | `application/json` (request & response) |
| **Pagination** | `?limit=<n>&offset=<n>` (max 200, default 50) |
| **Time format** | ISO 8601 UTC (`2026-04-27T12:34:56Z`) |
| **Identifiers** | UUID v4 |
| **Error envelope** | `{"detail": "<message>", "code": "<error_code>"}` |

## 7.2 Standard HTTP Status Codes

| Code | Meaning | Triggered by |
|------|---------|--------------|
| 200 | OK | Successful read/update |
| 201 | Created | Successful create |
| 204 | No Content | Successful delete |
| 400 | Bad Request | Malformed payload |
| 401 | Unauthorized | Missing/invalid JWT |
| 403 | Forbidden | RBAC denial |
| 404 | Not Found | Resource missing |
| 409 | Conflict | Unique-key violation |
| 422 | Unprocessable Entity | Pydantic validation failure |
| 429 | Too Many Requests | Rate limit hit |
| 500 | Internal Server Error | Unhandled exception |

## 7.3 Common Schemas (top-level)

### 7.3.1 PaginatedResponse<T>

```yaml
type: object
properties:
  items: { type: array, items: { $ref: '#/components/schemas/T' } }
  total: { type: integer }
  limit: { type: integer }
  offset: { type: integer }
```

### 7.3.2 ErrorResponse

```yaml
type: object
required: [detail]
properties:
  detail: { type: string }
  code: { type: string, nullable: true }
  field: { type: string, nullable: true }
```

### 7.3.3 Core Domain Schemas

```yaml
User:
  required: [id, username, role, enabled]
  properties:
    id: { type: string, format: uuid }
    username: { type: string }
    email: { type: string, format: email, nullable: true }
    role: { enum: [admin, operator, viewer] }
    enabled: { type: boolean }
    last_login: { type: string, format: date-time, nullable: true }

Device:
  required: [id, mac_address]
  properties:
    id: { type: string, format: uuid }
    mac_address: { type: string, pattern: '^([0-9a-f]{2}:){5}[0-9a-f]{2}$' }
    ip_address: { type: string, nullable: true }
    hostname: { type: string, nullable: true }
    device_type: { type: string, nullable: true }
    os_family: { type: string, nullable: true }
    vendor: { type: string, nullable: true }
    status: { enum: [authenticated, quarantined, blocked, unknown] }
    risk_score: { type: integer, minimum: 0, maximum: 100, nullable: true }

Policy:
  required: [id, name, priority, conditions, match_actions]
  properties:
    id: { type: string, format: uuid }
    name: { type: string, maxLength: 200 }
    description: { type: string, nullable: true }
    priority: { type: integer, minimum: 1, maximum: 9999 }
    enabled: { type: boolean, default: true }
    conditions: { type: array, items: { $ref: '#/components/schemas/PolicyCondition' } }
    match_actions: { type: array, items: { $ref: '#/components/schemas/PolicyAction' } }
    no_match_actions: { type: array, items: { $ref: '#/components/schemas/PolicyAction' } }

PolicyCondition:
  required: [field, operator, value]
  properties:
    field: { type: string, example: "device.os_family" }
    operator: { enum: [equals, not_equals, in, not_in, contains, starts_with, ends_with, gt, lt, regex, is_null] }
    value: { description: "Operator-dependent (string, number, list, or null)" }

PolicyAction:
  required: [type]
  properties:
    type: { enum: [vlan_assign, acl_apply, quarantine, coa] }
    params: { type: object, additionalProperties: true }
```

## 7.4 Endpoint Reference (Highlights)

A representative subset is below. The full spec is at [`docs/api/openapi.yaml`](api/openapi.yaml).

### 7.4.1 Auth

| Method | Path | Request | Response |
|--------|------|---------|----------|
| POST | /api/v1/auth/login | `{ username, password }` | `{ access_token, token_type, expires_in, user }` |
| GET | /api/v1/auth/me | (auth) | `User` |
| POST | /api/v1/auth/users | `UserCreate` (admin) | `User` (201) |
| GET | /api/v1/auth/users | `?limit&offset` | `PaginatedResponse<User>` |

### 7.4.2 Devices

| Method | Path | Request | Response |
|--------|------|---------|----------|
| GET | /api/v1/devices | `?status&type&search&limit&offset` | `PaginatedResponse<Device>` |
| GET | /api/v1/devices/{id} | — | `Device` |
| POST | /api/v1/devices | `DeviceCreate` | `Device` (201) |
| PATCH | /api/v1/devices/{id} | `DeviceUpdate` | `Device` |
| DELETE | /api/v1/devices/{id} | (admin) | (204) |

### 7.4.3 Policies

| Method | Path | Request | Response |
|--------|------|---------|----------|
| GET | /api/v1/policies | `?enabled&limit&offset` | `PaginatedResponse<Policy>` |
| POST | /api/v1/policies | `PolicyCreate` | `Policy` (201) |
| POST | /api/v1/policies/{id}/simulate | `DeviceContext` | `SimulationResult` |
| POST | /api/v1/policies/simulate-all | `DeviceContext` | `[SimulationResult]` |

### 7.4.4 CoA

| Method | Path | Request | Response |
|--------|------|---------|----------|
| POST | /api/v1/coa/by-mac | `{ mac, action, params? }` | `CoAResult` |
| POST | /api/v1/coa/by-username | `{ username, action, params? }` | `[CoAResult]` |
| POST | /api/v1/coa/by-session | `{ session_id, action, params? }` | `CoAResult` |
| POST | /api/v1/coa/bulk | `{ targets: [...] }` (max 100) | `BulkCoAResult` |
| GET | /api/v1/coa/active-sessions | `?nas_ip&vlan&limit&offset` | `PaginatedResponse<Session>` |

## 7.5 Generating Client SDKs

The OpenAPI spec is the contract. To generate clients:

```bash
# TypeScript
openapi-typescript http://localhost:8000/openapi.json -o frontend/src/api-types.ts

# Python
openapi-python-client generate --url http://localhost:8000/openapi.json

# Postman / Insomnia
# Import http://localhost:8000/openapi.json directly
```

## 7.6 Adding a New Endpoint

When you add a new endpoint, FastAPI auto-publishes it to `/openapi.json`. To keep the static spec in sync:

```bash
# From running container
curl http://localhost:8000/openapi.json > docs/api/openapi.yaml.tmp
yq -y . docs/api/openapi.yaml.tmp > docs/api/openapi.yaml
rm docs/api/openapi.yaml.tmp
git diff docs/api/openapi.yaml  # Review
```

Required for every new endpoint:

- [ ] Pydantic request/response models with descriptions
- [ ] Status code in `responses=` dict (201 for create, 204 for delete)
- [ ] `tags=` set to the resource group
- [ ] `summary` (one line) and `description` (multi-line) on the route
- [ ] Examples in models via `Field(..., example=...)`

---

# Part 8 — Decoupling Design (DI + Event-Driven)

OpenRadiusWeb avoids "change A, break B" through **two complementary decoupling strategies**:

1. **Dependency Injection (DI)** — within a single process (gateway, each service)
2. **Event-Driven Messaging (NATS)** — between processes (microservices)

## 8.1 Dependency Injection in the Gateway

### 8.1.1 FastAPI's `Depends()` Pattern

Every route handler receives its dependencies via constructor-style injection:

```python
# services/gateway/routes/devices.py

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from orw_common.database import get_db
from gateway.middleware.auth import get_current_user, require_operator

router = APIRouter()

@router.get("/devices")
async def list_devices(
    db: AsyncSession = Depends(get_db),          # ← injected
    user: dict = Depends(get_current_user),      # ← injected
    _ = Depends(require_operator),               # ← injected (raises if denied)
    limit: int = 50,
    offset: int = 0,
):
    return await query_devices_by_filter(db, user["tenant_id"], limit, offset)
```

**Why this matters:** The handler does not import or construct DB sessions, auth tokens, or RBAC checks. Tests inject fakes via `app.dependency_overrides[get_db] = fake_db`.

### 8.1.2 Five Standard Injectables

| Dependency | Provider | Returns | Test override |
|-----------|----------|---------|---------------|
| `Depends(get_db)` | `shared/orw_common/database.py` | `AsyncSession` | In-memory SQLite or test DB |
| `Depends(get_redis_client)` | `gateway/utils/redis_client.py` | `aioredis.Redis` | `fakeredis` |
| `Depends(get_current_user)` | `gateway/middleware/auth.py` | `User dict` | Fake user |
| `Depends(require_admin)` | same | None or 403 | No-op for tests |
| `Depends(get_settings)` | `shared/orw_common/config.py` | `Settings` | Override values |

### 8.1.3 Layered DI Across Service Boundaries

For background services (not FastAPI), use a **Composition Root** pattern: dependencies are constructed once at `main()` and threaded through.

```python
# services/policy_engine/main.py

async def main():
    db_pool = await create_db_pool(settings.database_url)
    nats_conn = await nats.connect(settings.nats_url)

    # Build the worker, injecting all deps
    worker = PolicyWorker(db=db_pool, nats=nats_conn)

    await worker.subscribe()
    await worker.run()
```

The `PolicyWorker` accepts `db` and `nats` as constructor args — never imports them globally. This means:
- Tests inject `MockNATS()` and `MockDB()` and exercise `worker.handle_evaluate_device()` directly
- A future change to NATS (e.g., switch to Redis Streams) only touches `main.py`

### 8.1.4 DI Anti-Patterns

| Anti-pattern | Refactor to |
|--------------|-------------|
| Global `db = create_engine(...)` at module top | DI via `Depends(get_db)` |
| `import requests; requests.get(...)` inside a route | Inject an HTTP client interface |
| `os.environ["DB_URL"]` read inside a function | Inject `Settings` |
| Hardcoded singleton `nats_client.publish(...)` | Inject `Publisher` interface |

## 8.2 Event-Driven Decoupling via NATS

### 8.2.1 Why NATS, not HTTP

| Aspect | Synchronous HTTP | Async NATS |
|--------|-----------------|-----------|
| Coupling | Direct (caller knows callee URL) | Indirect (subject namespace) |
| Failure mode | Caller blocked / 5xx | Buffered in JetStream |
| Testability | Mock HTTP server needed | Inject fake message |
| Scalability | One caller, one callee | One publisher, N subscribers |
| Use case | Reads, immediate responses | Mutations, fan-out, async pipelines |

### 8.2.2 Subject Namespace Convention

```
orw.<domain>.<event>
```

| Domain | Examples | Producer | Consumer |
|--------|----------|----------|---------|
| `device` | `discovered`, `evaluated`, `deleted` | discovery, gateway | device_inventory, event_service, policy_engine |
| `policy` | `evaluate_device`, `action.vlan_assign`, `action.coa` | gateway, device_inventory | policy_engine, event_service |
| `switch` | `set_vlan`, `bounce_port`, `port_state_changed` | policy_engine, gateway | switch_mgmt |
| `coa` | `send` | gateway, policy_engine | coa_service |
| `discovery` | `scan_request` | gateway | discovery |
| `config` | `freeradius.apply` | gateway | freeradius_config_watcher |
| `security` | `wazuh.alert` | event_service | (out: Wazuh) |
| `system` | `health.heartbeat` | all services | monitoring |

### 8.2.3 Producer / Consumer Independence

Critical decoupling property: **a producer does not know which subscribers exist**.

```python
# Producer (any service)
await nats_publish("orw.device.discovered", {"mac": "...", "ip": "..."})
# It does NOT call device_inventory directly.

# Consumer (device_inventory)
await nats_subscribe("orw.device.discovered", handle_device_discovered, queue="device-inventory")
# It does NOT know who published.

# Adding a NEW consumer (e.g., risk_scoring service in the future)
await nats_subscribe("orw.device.discovered", handle_device_for_risk, queue="risk-scoring")
# No change to producer.
```

### 8.2.4 Durable Consumers (JetStream)

Each subscriber has a **durable name**. If a consumer is offline, JetStream retains messages and replays on reconnect.

| Service | Durable name | Stream | Replay on restart |
|---------|--------------|--------|------------------|
| device_inventory | `device-inventory` | orw | Yes |
| policy_engine | `policy-engine` | orw | Yes |
| switch_mgmt | `switch-mgmt` | orw | Yes |
| event_service | `event-service` | orw | Yes |
| coa_service | `coa-service` | orw | Yes |
| freeradius_config_watcher | `freeradius-config-watcher` | orw | Yes |

### 8.2.5 Event Schema Stability

To avoid breaking subscribers, follow these rules:

| Rule | Reason |
|------|--------|
| **Add fields, never remove** | Old consumers can ignore new fields |
| **Never change a field's type** | If type changes, use a NEW field name |
| **Never change a subject's semantic meaning** | If meaning changes, use a NEW subject |
| **Version event schemas via `schema_version` field** | Consumers can branch on version |
| **Document every event in this manual** | Visible to all teams |

### 8.2.6 Event Inventory (current)

| Subject | Schema (key fields) | Producer | Consumer(s) |
|---------|--------------------|----------|-------------|
| `orw.device.discovered` | `mac, ip?, hostname?, vendor?, source` | discovery | device_inventory |
| `orw.policy.evaluate_device` | `device_id, tenant_id, trigger` | device_inventory, gateway | policy_engine |
| `orw.policy.action.vlan_assign` | `device_id, vlan_id, reason` | policy_engine | event_service, switch_mgmt |
| `orw.policy.action.coa` | `target, action, params` | policy_engine | event_service, coa_service |
| `orw.switch.set_vlan` | `network_device_id, port, vlan_id` | policy_engine, gateway | switch_mgmt |
| `orw.switch.bounce_port` | `network_device_id, port` | gateway | switch_mgmt |
| `orw.switch.port_state_changed` | `network_device_id, port, state` | switch_mgmt | event_service |
| `orw.coa.send` | `target_type, target_value, action` | gateway, policy_engine | coa_service |
| `orw.discovery.scan_request` | `cidr, mode` | gateway | discovery |
| `orw.config.freeradius.apply` | `reason, requested_by` | gateway | freeradius_config_watcher |

## 8.3 Coupling Map (How to Read Dependencies)

### 8.3.1 Within a Service: Layered Architecture

```
┌──────────────────────────────────────────────┐
│ Routes (FastAPI handlers — gateway only)     │  ← uses Depends()
├──────────────────────────────────────────────┤
│ Use cases (composition of atoms)             │
├──────────────────────────────────────────────┤
│ Atoms (validators, builders, repositories)   │
├──────────────────────────────────────────────┤
│ Infrastructure (DB, NATS, Redis, HTTP)       │  ← injected
└──────────────────────────────────────────────┘
```

Each layer depends only on the layer below. The atoms layer does not import FastAPI.

### 8.3.2 Between Services: Event-Only

```
┌────────────────┐      NATS      ┌────────────────┐
│ Producer Svc   │ ────────────►  │ Consumer Svc   │
└────────────────┘                └────────────────┘
        │                                  │
        ▼                                  ▼
   PostgreSQL                         PostgreSQL
   (own connection)                   (own connection)
```

**Forbidden:** services calling each other's HTTP APIs. **Allowed:** all services read from the shared PostgreSQL.

### 8.3.3 Module-to-Module Compatibility Matrix

| From → To | Allowed? | Mechanism |
|-----------|----------|-----------|
| Route → Atom | ✅ | Direct call |
| Atom → Atom (same service) | ✅ | Direct call |
| Atom → DB | ✅ | Injected `db` |
| Atom → NATS | ✅ | Injected `nats` |
| Atom → another Service's HTTP | ❌ | Use NATS event instead |
| Atom → another Service's DB tables | ⚠️ | Allowed but discouraged; prefer events |
| Service A → Service B (Python import) | ❌ | Use NATS or shared/orw_common |
| All services → `shared/orw_common` | ✅ | Shared library |

## 8.4 Adding a New Feature Without Coupling

Concrete recipe to add a feature ("Feature X") without breaking existing code:

### Step 1 — Decide the boundary
- Is X a **new endpoint**? Goes in gateway/routes/
- Is X a **background reaction**? Goes in a service that subscribes to existing events
- Is X a **whole new domain**? Create a new microservice

### Step 2 — Define inputs/outputs
- HTTP: define request/response Pydantic models in `shared/orw_common/models/`
- Event: define subject + schema in §8.2.6 of this manual

### Step 3 — Compose from atoms
- Search existing atoms in §3.2 (cross-cutting) and §3.3 (per-feature)
- Reuse where possible
- Write new atoms only for genuinely new responsibilities

### Step 4 — Inject dependencies, never import singletons
- Pass `db`, `nats`, `redis` as parameters
- For FastAPI: use `Depends()`
- For workers: pass to constructor

### Step 5 — Subscribe to events, do not poll DB tables
- If feature X reacts to "device discovered", subscribe to `orw.device.discovered`
- Do NOT add a periodic `SELECT * FROM devices` poller

### Step 6 — Emit events for downstream consumers
- If feature X mutates state others care about, publish a new event
- Add the new subject to §8.2.6
- Do not call other services directly

### Step 7 — Test atoms unit-style, flow integration-style
- Pure atoms: table-driven tests
- DB atoms: against test DB
- Full flow: integration test that publishes an event and asserts downstream side effects

### Step 8 — Document the new atoms in §3.3 and any new events in §8.2.6

---

---

# Part 9 — Development Workflow

This part defines **how** features are built. Core goal: **high cohesion, low coupling** — every module is independently testable and replaceable.

## 9.1 Three-Layer Architecture

Every module belongs to exactly one of three layers:

```
┌────────────────────────────────────────────────────────────┐
│ Layer 3 — Interface Adapters (the "thin shell")            │
│ Receives external input, calls Layer 2, returns response.  │
│ Examples: REST routes, RADIUS hooks, NATS subscribers,     │
│           CLI tools, webhook receivers                     │
├────────────────────────────────────────────────────────────┤
│ Layer 2 — Service / Business Logic                         │
│ Pure functions where possible. Same input → same output.   │
│ Examples: policy evaluation, RADIUS auth method detection, │
│           AD error mapping, MAC normalization, JWT signing │
├────────────────────────────────────────────────────────────┤
│ Layer 1 — Infrastructure (the "foundation")                │
│ Talks to the outside world. NO business logic here.        │
│ Examples: DB connector, NATS client, Redis client,         │
│           Logger, Settings loader, Crypto primitives       │
└────────────────────────────────────────────────────────────┘
```

### 9.1.1 Layer 1 — Infrastructure (Foundation)

| Module | Responsibility | Replaceable with |
|--------|---------------|------------------|
| `shared/orw_common/database.py` | PostgreSQL connection pool | MySQL, CockroachDB |
| `shared/orw_common/nats_client.py` | NATS JetStream client | Redis Streams, Kafka |
| `gateway/utils/redis_client.py` | Redis async client | Memcached, in-memory dict |
| `shared/orw_common/config.py` | Pydantic Settings loader | python-decouple, env-based |
| `shared/orw_common/logging.py` | structlog logger | std logging, loguru |
| `shared/orw_common/exceptions.py` | Domain error types | (stable contract) |

**Rule:** Layer 1 modules must be importable without side effects. Connection objects are constructed via DI, not at import time.

### 9.1.2 Layer 2 — Service / Business Logic

| Module | Responsibility | Pure? |
|--------|---------------|-------|
| `shared/orw_common/policy_evaluator.py` | Match conditions → bool/actions | Yes |
| `services/auth/.../rlm_orw.py` (helpers) | `_detect_auth_method`, `_map_ad_error_code` | Yes |
| All `validate_*` atoms | Input validation | Yes |
| All `normalize_*`, `format_*`, `parse_*` atoms | Data transformation | Yes |
| All `match_*`, `compare_*` atoms | Decisions / comparisons | Yes |
| All `build_*` atoms | Compose data structures | Yes |
| Cert / JWT / hash / RSA atoms | Crypto primitives | Yes (deterministic given input) |

**Rule:** Layer 2 has no `db.execute(...)`, `nats.publish(...)`, `redis.get(...)`, or `requests.get(...)`. If a function does need I/O, it belongs in Layer 3 (or it's split — pure logic in Layer 2, effects in Layer 3).

### 9.1.3 Layer 3 — Interface Adapters

| Module | Responsibility | What it does |
|--------|---------------|--------------|
| `gateway/routes/*.py` | REST API | Receive HTTP → call Layer 2/1 → return JSON |
| `gateway/main.py` | App wiring | Mount routes, register middleware, install exception handlers |
| `services/auth/.../rlm_orw.py` (`authorize`/`post_auth`/`accounting`) | RADIUS hooks | FreeRADIUS calls these → call Layer 2/1 |
| `services/<svc>/main.py` | NATS subscriber registration | Connect → subscribe → dispatch to handlers |
| `frontend/src/pages/*.tsx` | Web UI | Render state, call API, handle user input |

**Rule:** Layer 3 should be **thin**. A REST handler that is more than ~20 lines is a code smell — extract Layer 2 atoms.

### 9.1.4 Dependency Direction

```
       Layer 3 (Interface)
            │
            ▼ depends on
       Layer 2 (Service)
            │
            ▼ depends on
       Layer 1 (Infrastructure)
```

**Forbidden:** Layer 1 importing from Layer 2 or 3. Layer 2 importing from Layer 3.

This is enforced by the `shared/orw_common` package having no FastAPI / aiohttp / route imports.

## 9.2 Task Decomposition (How to Break a Feature into Work)

When a new feature lands, decompose it like this:

### Step 1 — Capture the user-facing outcome

State the change in one sentence: *"As an admin, I want to mark a device 'compromised' so that it is automatically quarantined."*

### Step 2 — Identify the layer-3 surface

What's the trigger?

| Trigger | Layer 3 surface |
|---------|----------------|
| User clicks button in UI | New REST endpoint |
| Switch sends RADIUS request | New rlm_orw.py code path |
| Another service publishes event | New NATS subscriber |
| Scheduled / periodic | New CronJob (or interval inside service main.py) |
| External system calls webhook | New webhook route |

### Step 3 — Walk the data-flow

Trace what happens:
1. **What input arrives?** (HTTP body, RADIUS attributes, NATS message)
2. **What state must be read?** (DB tables, Redis keys, LDAP)
3. **What pure computation happens?** (validation, comparison, transformation)
4. **What state must be written?** (DB inserts, NATS publishes, Redis updates)
5. **What output goes back?** (HTTP response, RADIUS reply, NATS event)

### Step 4 — Map each step to atoms

For each step, name the atom that does it. Reuse existing atoms (search §3.2 and §3.3). Only create new atoms when no existing one fits.

### Step 5 — Decide the unit of work

Each work unit must be:
- **Independently testable** (pure atoms: unit tests; effectful atoms: integration tests with fakes)
- **Independently deployable**? Usually NO for atoms (deployed with their service); YES for new microservices
- **Sized** ≤ 1 day for atoms, ≤ 1 week for endpoints, ≤ 1 month for new services

### Step 6 — Order the work

Build bottom-up: Layer 1 → Layer 2 → Layer 3. Each layer can be tested in isolation before the next is built.

### 9.2.1 Worked Example: "Mark Device Compromised → Auto Quarantine"

| # | Layer | Atom | Status (new vs reuse) |
|---|-------|------|----------------------|
| 1 | 3 | `POST /devices/{id}/compromise` route | **New** |
| 2 | 3 | `require_operator(user)` | Reuse |
| 3 | 2 | `validate_compromise_payload(body)` | **New** (1-day) |
| 4 | 2 | `lookup_device_by_id(db, id)` | Reuse |
| 5 | 2 | `set_device_status(db, id, "compromised", reason)` | **New** repository (1-day) |
| 6 | 2 | `log_audit("device.compromise", ...)` | Reuse |
| 7 | 2 | `serialize_event_payload(...)` | Reuse |
| 8 | 2 | `publish_device_compromised(payload)` | **New** publisher (0.5-day) |
| 9 | 3 | (existing) policy_engine subscribes to `orw.device.compromised` | **New subscription** (1-day) |
| 10 | 2 | (existing) policy with condition `status=compromised → quarantine action` | **Config change** |

Total new code: 4 atoms + 1 subscription. Estimated 3.5 dev-days. No existing code modified.

## 9.3 State Management

Where does state live? Knowing this prevents accidental coupling.

| State category | Storage | Example | Lifetime |
|----------------|---------|---------|----------|
| **Authoritative business data** | PostgreSQL | devices, policies, users | Permanent |
| **Time-series logs** | TimescaleDB hypertable | radius_auth_log, audit_log, events | 1-2 years |
| **Cache / rate limit counters** | Redis | login rate, lockout, JWT denylist | TTL-based |
| **In-flight messages** | NATS JetStream | orw.device.discovered | Until acked |
| **FreeRADIUS sessions** | FreeRADIUS in-memory | active EAP handshakes | Per-handshake |
| **JWT claims** | Client-side (token) | tenant_id, role | 60 min |
| **Switch port state** | Network device (truth) | VLAN assignments, ACLs | Until next change |
| **Local file caches** | Container ephemeral disk | rendered FreeRADIUS configs | Until SIGHUP |

### 9.3.1 Rules of State

| Rule | Why |
|------|-----|
| **One source of truth per data class** | No "is the device active in DB or in Redis?" |
| **Reads can be cached, writes go to source** | Stale data is OK; lost data is not |
| **All mutations write to DB first, then publish event** | Event-driven listeners can rely on DB read |
| **Cross-service state shared only via DB or events** | No service holds another service's state in memory |
| **Locks: PostgreSQL row locks for short critical sections** | No distributed locks; design idempotently instead |
| **Idempotency: ON CONFLICT or UUID-based dedup** | Retries don't double-write |

### 9.3.2 State Ownership Per Service

| Service | Owns (writes to) | Reads from |
|---------|------------------|-----------|
| gateway | All admin-facing tables | All |
| device_inventory | devices, device_properties | tenants |
| policy_engine | policy_evaluations | policies, devices, device_properties |
| switch_mgmt | switch_ports | network_devices |
| coa_service | (none — emits events only) | radius_auth_log |
| event_service | events | (consumes NATS) |
| freeradius (rlm_orw) | radius_auth_log | mab_devices, group_vlan_mappings, ldap_servers |
| freeradius_config_watcher | freeradius_config | All RADIUS-config tables |

**Cross-cutting:** `audit_log` — every service writes its own actions. `tenants` — all services read; only gateway writes.

## 9.4 Critical-Step Validation

Some steps must succeed for the feature to be correct. Validation strategy by step type:

### 9.4.1 Validation Test Matrix

| Step type | How to validate | When |
|-----------|----------------|------|
| Pure atom (Layer 2) | Table-driven unit test | CI on every commit |
| DB write (Layer 2) | Integration test against test DB | CI on every commit |
| NATS publish (Layer 2/3) | Capture published msg, assert subject + payload | CI |
| External call (LDAP, switch SSH, CoA UDP) | Integration test against staging service | Pre-deploy |
| Cross-service flow | End-to-end test publishing event, asserting downstream effects | Pre-deploy |
| RADIUS auth flow | radclient fixture against staging FreeRADIUS | Pre-deploy |
| UI flow | Playwright / Cypress end-to-end | Pre-deploy |

### 9.4.2 Pre-Production Smoke Tests

Before marking a release "ready", these MUST all pass:

```
1.  Login as admin → token returned                          [API smoke]
2.  Create device → 201 + device shape correct               [API smoke]
3.  RADIUS PAP auth (radclient) → Access-Accept              [RADIUS smoke]
4.  RADIUS MAB (radclient with MAC username) → Accept + VLAN [RADIUS smoke]
5.  RADIUS PEAP-MSCHAPv2 (eapol_test) → Accept               [802.1X smoke]
6.  Discover ARP packet → device appears in DB ≤ 5s          [event smoke]
7.  Update policy → policy_engine evaluates next request     [config smoke]
8.  Send CoA-Disconnect → session removed within 2s          [CoA smoke]
9.  Postgres failover → reconnect within 30s                 [HA smoke]
10. Open audit log page → recent action visible              [UI smoke]
```

### 9.4.3 Health Probes (per service)

Every service exposes `/health` (or equivalent) returning:
- `db_ok` — can SELECT 1?
- `nats_ok` — connection alive?
- `redis_ok` — PING returns?
- `version` — git SHA at build time

Used by Kubernetes liveness/readiness probes and Docker Compose healthchecks.

## 9.5 Failure Recovery

Failures happen. The system must degrade gracefully.

### 9.5.1 Failure Modes & Mitigation

| Failure | Mitigation |
|---------|-----------|
| **Postgres slow query (>1s)** | asyncpg statement_timeout, retry with exponential backoff |
| **Postgres unavailable** | Read-only mode for cached data; queue writes to Redis (with TTL); alert |
| **Redis unavailable** | Fall back to no rate-limiting (open) or no cache (closed-fail-deny for security) |
| **NATS unavailable** | JetStream broker reconnect; producer retries with bounded backoff |
| **NATS message handler crashes** | Durable consumer redelivers; after max-deliver, send to DLQ subject `orw.dlq.*` |
| **LDAP unreachable** | RADIUS reject with reason `AD_CONNECT_FAILED`; cache last successful group lookup for `cache_ttl` seconds |
| **Switch SSH fails** | Retry 3× with backoff; if persistent, disable switch in DB and emit alert event |
| **CoA UDP timeout** | Retry once; record as failed CoA; do not silently succeed |
| **Container OOM** | Docker restart=always + Kubernetes liveness probe |
| **Disk full (TimescaleDB)** | Compression policy on hypertables; alarm at 80% disk |
| **Cert expiring** | Daily check + alert at 30/7/1 days; UI banner |

### 9.5.2 Idempotency for Safe Retries

Any operation that can be retried MUST be idempotent. Patterns:

| Operation | Idempotency mechanism |
|-----------|---------------------|
| Insert device | `INSERT...ON CONFLICT (mac, tenant) DO UPDATE` |
| Insert audit log | UUID-based dedup table OR pure append (allow duplicates) |
| Send CoA | Track per-session-id; skip if already disconnected |
| Apply VLAN to port | Read current VLAN first; skip if already correct |
| FreeRADIUS config write | Hash-compare; skip if unchanged |
| NATS publish | Embed `event_id` (UUID); subscribers dedup |

### 9.5.3 Dead-Letter Pattern

For NATS messages that fail handling repeatedly:

```
orw.<domain>.<event>     ← normal subject
orw.dlq.<domain>.<event> ← published after N retries
```

Operators monitor `orw.dlq.>` separately and decide on manual replay or discard.

### 9.5.4 Circuit Breakers (External Calls)

For LDAP, switch SSH, CoA UDP — wrap calls in a circuit breaker:

| State | Behavior |
|-------|---------|
| **Closed** | Normal calls; count failures |
| **Open** | All calls fail fast (don't waste resources); set after N consecutive failures |
| **Half-Open** | Periodic test call; on success → Closed; on fail → Open |

Implementation: `pybreaker` or in-memory state per host. State NOT shared across instances (intentionally — local resilience).

### 9.5.5 Rollback Strategy

If a deployment introduces a bug:

| Severity | Action |
|---------|--------|
| **Critical (auth broken)** | `docker-compose down && git checkout <prev-tag> && docker-compose up -d` (≤ 5 min) |
| **Major (one feature broken)** | Disable feature flag in `system_settings`; investigate; patch |
| **Minor (UI bug)** | Forward-fix in next release |
| **DB migration broken** | `docker-compose down`, restore DB from snapshot, redeploy old image |

Pre-flight: every deploy is preceded by `pg_dump` + tagged backup retention for 7 days.

---

# Part 10 — Unified Deployment Strategy

**Goal: Environment Consistency.** The same Docker image runs on the developer's laptop, in a Kubernetes cluster, or on an air-gapped bare-metal server. **Build once, deploy anywhere.**

## 10.1 The "Three Paths" Concept

```
                 ┌─────────────────────────────┐
                 │  Single Container Image     │
                 │  ghcr.io/.../orw-svc:1.2.3  │
                 └──────────────┬──────────────┘
                                │
        ┌───────────────────────┼───────────────────────┐
        ▼                       ▼                       ▼
  ┌──────────────┐       ┌──────────────┐       ┌──────────────┐
  │   Dev        │       │  Prod-Cloud  │       │  Prod-Edge   │
  │ Docker       │       │  Helm Chart  │       │  Ansible     │
  │ Compose      │       │  on K8s      │       │  on VM/bare  │
  └──────────────┘       └──────────────┘       └──────────────┘
       Hot-reload          HPA, Ingress,         Hardened OS,
       Sidecars (PG,       K8s Secrets,           Docker engine,
       Redis, NATS)        Persistent Volumes     Same image pull
```

| Path | Target | Tool | Use case |
|------|--------|------|----------|
| **Dev** | Developer laptop | `docker compose` | Quick iteration with hot-reload |
| **Prod-Cloud** | Kubernetes cluster | Helm | SaaS / multi-tenant production |
| **Prod-Edge** | Bare metal / VM, no K8s | Ansible | On-premise / air-gapped customer site |

## 10.2 Path 1 — Dev (Docker Compose)

Existing file: [docker-compose.yml](../docker-compose.yml).

Features:
- Sidecar containers for PostgreSQL, Redis, NATS — ephemeral
- Service code mounted as volume → **hot-reload** via `uvicorn --reload`
- Healthchecks gate startup ordering
- All ports exposed to localhost for debugging

Recommended additions for full dev experience:

```yaml
# docker-compose.override.yml (gitignored)
services:
  gateway:
    volumes:
      - ./services/gateway:/app/services/gateway
      - ./shared:/app/shared
    command: uvicorn services.gateway.main:app --host 0.0.0.0 --reload
    environment:
      - LOG_LEVEL=DEBUG
```

Quick start:

```bash
make setup        # generate .env from template
make dev          # start sidecars (postgres, redis, nats)
make up           # start all services
make logs         # follow logs
make down         # stop everything
```

## 10.3 Path 2 — Prod-Cloud (Helm Chart)

**To be created:** `deploy/helm/orw/`

### 10.3.1 Helm Chart Structure

```
deploy/helm/orw/
├── Chart.yaml
├── values.yaml              # default values (override per env)
├── templates/
│   ├── _helpers.tpl
│   ├── gateway-deployment.yaml
│   ├── gateway-service.yaml
│   ├── gateway-ingress.yaml
│   ├── gateway-hpa.yaml         # Autoscale 2-10 pods
│   ├── policy-engine-deployment.yaml
│   ├── policy-engine-hpa.yaml
│   ├── discovery-daemonset.yaml # one per node (passive listen)
│   ├── freeradius-statefulset.yaml
│   ├── postgres-statefulset.yaml
│   ├── redis-statefulset.yaml
│   ├── nats-statefulset.yaml
│   ├── secrets.yaml             # references external secrets
│   └── networkpolicy.yaml       # restrict inter-pod traffic
└── values-prod.yaml         # production overrides
```

### 10.3.2 Key Helm Values

```yaml
# values.yaml
image:
  repository: ghcr.io/acronhuang/openradiusweb
  tag: 1.2.3
  pullPolicy: IfNotPresent

gateway:
  replicas: 2
  hpa:
    enabled: true
    minReplicas: 2
    maxReplicas: 10
    targetCPUUtilizationPercentage: 70
  resources:
    requests: { cpu: 200m, memory: 256Mi }
    limits:   { cpu: 1000m, memory: 1Gi }

freeradius:
  # NOT autoscaled — RADIUS auth latency is sensitive to LB jitter
  replicas: 2
  resources:
    requests: { cpu: 500m, memory: 512Mi }

postgres:
  # External managed Postgres recommended (RDS, Cloud SQL)
  external: true
  endpoint: postgres.svc.example.com
  secretRef: orw-db-secret
```

### 10.3.3 Secrets Management

```bash
# Use Sealed Secrets / External Secrets Operator
kubectl create secret generic orw-secrets \
  --from-literal=db_password=$(openssl rand -base64 24) \
  --from-literal=jwt_secret_key=$(openssl rand -hex 32) \
  --from-literal=redis_password=$(openssl rand -base64 24) \
  --dry-run=client -o yaml | kubeseal -o yaml > orw-secrets-sealed.yaml
git add orw-secrets-sealed.yaml  # safe to commit
```

### 10.3.4 Autoscaling Strategy

| Component | Scale on | Min | Max |
|-----------|----------|-----|-----|
| gateway | CPU 70% | 2 | 10 |
| policy_engine | NATS pending msgs | 1 | 5 |
| device_inventory | NATS pending msgs | 1 | 5 |
| switch_mgmt | NATS pending msgs | 1 | 3 |
| freeradius | RADIUS auth/sec (custom metric) | 2 | 6 |
| event_service | NATS pending msgs | 1 | 3 |
| coa_service | (manual) | 1 | 2 |
| discovery | (DaemonSet, 1 per node) | — | — |

### 10.3.5 Persistent Storage

| Service | Storage | Size | Replicas |
|---------|---------|------|----------|
| postgres | StatefulSet PVC | 100 Gi | 1 (or use managed) |
| redis | StatefulSet PVC | 10 Gi | 1 |
| nats | StatefulSet PVC | 50 Gi | 3 (cluster mode) |
| freeradius certs | ConfigMap + Secret | — | — |

## 10.4 Path 3 — Prod-Edge (Ansible)

**To be created:** `deploy/ansible/`

For sites that cannot run Kubernetes (small offices, air-gapped networks, hardware appliances).

### 10.4.1 Ansible Playbook Structure

```
deploy/ansible/
├── inventory/
│   ├── production.yml      # site list + variables
│   └── group_vars/
│       └── all.yml          # common variables (no secrets)
├── playbooks/
│   ├── 01-os-hardening.yml  # disable services, kernel params, firewalld
│   ├── 02-docker-install.yml
│   ├── 03-deploy-orw.yml
│   ├── 04-configure-tls.yml
│   └── 99-uninstall.yml
├── roles/
│   ├── docker/
│   ├── orw-deploy/         # pulls image, renders compose, starts
│   └── orw-monitoring/     # node-exporter, log shipping
├── files/
│   └── docker-compose.prod.yml
└── templates/
    ├── env.j2               # generates .env.production
    └── nginx.conf.j2
```

### 10.4.2 OS Hardening (Role)

```yaml
# deploy/ansible/roles/os-hardening/tasks/main.yml
- name: Disable unused services
  service: { name: "{{ item }}", state: stopped, enabled: no }
  loop: [cups, avahi-daemon, bluetooth, postfix]

- name: Set kernel network params for RADIUS server
  sysctl: { name: "{{ item.name }}", value: "{{ item.value }}", state: present, reload: yes }
  loop:
    - { name: net.core.rmem_max, value: 16777216 }
    - { name: net.core.wmem_max, value: 16777216 }
    - { name: net.ipv4.udp_mem, value: "65536 131072 262144" }
    - { name: net.ipv4.ip_local_port_range, value: "30000 65000" }

- name: Configure firewalld zones
  firewalld:
    port: "{{ item }}"
    state: enabled
    permanent: yes
  loop: ["1812/udp", "1813/udp", "3799/udp", "8000/tcp", "8888/tcp"]

- name: Install fail2ban for SSH
  package: { name: fail2ban, state: present }
```

### 10.4.3 Single-Command Deployment

```bash
# From operator workstation
ansible-playbook -i inventory/production.yml playbooks/01-os-hardening.yml
ansible-playbook -i inventory/production.yml playbooks/02-docker-install.yml
ansible-playbook -i inventory/production.yml playbooks/03-deploy-orw.yml \
  -e orw_image_tag=1.2.3 \
  -e orw_db_password="$(pass openradiusweb/db)" \
  -e orw_jwt_secret="$(pass openradiusweb/jwt)"
```

### 10.4.4 Air-Gapped Variant

For sites with no internet:

1. On a connected machine: `docker save ghcr.io/.../orw:1.2.3 -o orw-1.2.3.tar`
2. Transfer .tar via USB / approved transfer
3. Add to ansible: `docker load -i {{ image_tar_path }}`
4. Same playbook proceeds

## 10.5 Build Pipeline (CI)

The single image is built and tagged once:

```
┌──────────────────────────────────────────────┐
│ git push to main                             │
├──────────────────────────────────────────────┤
│ CI:                                          │
│  1. Run tests (unit + integration)           │
│  2. Build Docker image (multi-stage)         │
│  3. Tag: <semver>, <git-sha>, latest         │
│  4. Push to ghcr.io / registry               │
│  5. Update Helm chart version (if release)   │
│  6. Trigger ArgoCD sync (Prod-Cloud)         │
│  7. Notify Ansible operators (Prod-Edge)     │
└──────────────────────────────────────────────┘
```

## 10.6 Standard Directory Structure (Feature-Oriented, Recursively Modular)

**The "small modules compose into large modules" principle is enforced by laying out each service as `features/<name>/` self-contained folders.** This is the standard for new features and refactors; the existing flat `routes/` is a transitional state, migrated incrementally per §10.6.3.

```
services/gateway/
├── main.py                       # app wiring (Layer 3 entry)
├── middleware/                   # cross-feature (Layer 3) — auth, request_id, etc.
├── features/                     # ← Standard: one self-contained folder per feature
│   ├── auth/
│   │   ├── routes.py             # Layer 3 — REST routes
│   │   ├── service.py            # Layer 2 — use-case composition
│   │   ├── repository.py         # Layer 2 (DB read/write only)
│   │   ├── schemas.py            # Pydantic request/response models
│   │   ├── __init__.py           # Public API (what other features may import)
│   │   └── tests/
│   │       ├── test_service.py
│   │       └── test_routes.py
│   ├── devices/
│   │   ├── routes.py
│   │   ├── service.py
│   │   ├── repository.py
│   │   ├── events.py             # NATS publishers/subscribers (Layer 2)
│   │   ├── schemas.py
│   │   ├── __init__.py
│   │   └── tests/
│   └── policies/
│       ├── routes.py
│       ├── service.py
│       ├── evaluator.py          # Layer 2 (pure compute)
│       ├── repository.py
│       ├── schemas.py
│       ├── __init__.py
│       └── tests/
└── utils/                        # service-local helpers (rare; prefer placing inside the relevant feature/)
```

Non-gateway services use the same `features/` layout, just without `routes.py` (no HTTP for background services); they expose `subscribers.py` to register NATS handlers, and `main.py` is the composition root.

### 10.6.1 Why This Structure

| Benefit | Detail |
|---------|--------|
| **Locality** | Everything for "auth" lives in one folder; reviewer scrolls less |
| **Bounded blast radius** | Change to "devices" can't accidentally touch "policies" files |
| **Recursive** | Sub-features inside a feature get their own subfolder (e.g., `policies/actions/vlan_assign/`) |
| **Ownership** | A team / person can own `features/auth/` end-to-end |
| **Clear public API** | `__init__.py` exposes only what other features may import |
| **Aligns with atomic philosophy** | Each `features/<name>/` is the "large module"; its files are composed of §3 atoms |

### 10.6.2 Cross-Feature Communication

| Method | When to use |
|--------|------------|
| Import from `shared/orw_common/*` | Cross-cutting atoms (MAC, time, crypto) |
| Import another feature's `__init__.py` public symbols | Direct call within same service — **rare**; prefer events |
| Publish NATS event | Default for cross-feature communication |
| Read another feature's DB tables | Allowed but discouraged; prefer repository in own feature |
| Import another feature's `service.py` internal symbols | **Forbidden** — go through `__init__.py` public contract |

### 10.6.3 Migration Path for the Existing Flat `routes/`

The existing `services/gateway/routes/<resource>.py` files are transitional. Migrate incrementally; do not do a big-bang rewrite:

1. **New features must** use the `features/<name>/` layout — adding files to `routes/` is not allowed (enforced by `make lint-features`; see [scripts/check_no_new_routes.py](../scripts/check_no_new_routes.py)).
2. Existing routes trigger migration when:
   - The feature receives non-trivial changes (new endpoints, structural changes)
   - A PR for the feature touches ≥ 3 files (routes + utils + tests)
   - Unit-test coverage is being expanded
3. Steps to migrate a single feature:
   - Create `services/<svc>/features/<name>/`
   - Move endpoints from `routes/<name>.py` into `features/<name>/routes.py`
   - Split out `service.py` (use-case composition), `repository.py` (DB atoms), `schemas.py` (Pydantic)
   - Update import paths in `gateway/main.py`
   - Delete the old `routes/<name>.py` (and remove its entry from `LEGACY_ROUTES` in [scripts/check_no_new_routes.py](../scripts/check_no_new_routes.py))
4. **One feature per PR** to keep review surface bounded.
5. Migration progress is tracked in [docs/migration-features.md](migration-features.md).

---

## Appendix A — Document History

| Version | Date | Change |
|---------|------|--------|
| 1.0 | 2026-04-27 | Initial consolidated manual |
| 1.1 | 2026-04-27 | Added Part 7 (API Spec) and Part 8 (Decoupling Design); bilingual EN/ZH |
| 1.2 | 2026-04-27 | Added Part 9 (Development Workflow) and Part 10 (Unified Deployment Strategy) |
| 1.3 | 2026-04-28 | §5.4 and §10.6: promoted feature-oriented (`features/<name>/`) directory layout to standard; flat `routes/` reframed as transitional with concrete migration triggers; lint enforcement added (`make lint-features`) |
| 1.4 | 2026-04-29 | Added "Development Principles" preface ("build features as the smallest possible modules") above §1, with cross-links to §3 / §5.1 / §9.1 / §10.6 |

This manual supersedes the earlier separate analyses (`roadmap.md`, `feature-breakdown.md`, `atomic-modules.md` — now removed).
