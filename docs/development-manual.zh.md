# OpenRadiusWeb — 開發手冊

**版本：** 1.3
**日期：** 2026-04-28
**對象：** 開發者、Code Reviewer、架構師
**語言：** 中文（本文件）· English ([development-manual.md](development-manual.md))

本手冊是 OpenRadiusWeb 專案的整合參考文件，說明專案的目的、現有功能，以及程式碼如何被拆解成單一職責的原子級模組。

---

## 目錄

1. [專案需求](#第-1-部分--專案需求)
2. [核心功能盤點](#第-2-部分--核心功能盤點)
3. [原子模組目錄](#第-3-部分--原子模組目錄)
4. [組合流程](#第-4-部分--組合流程)
5. [開發規範](#第-5-部分--開發規範)
6. [快速參考索引](#第-6-部分--快速參考索引)
7. [API 規格 (OpenAPI)](#第-7-部分--api-規格-openapi)
8. [解耦設計（DI + 事件驅動）](#第-8-部分--解耦設計di--事件驅動)
9. [開發流程](#第-9-部分--開發流程)
10. [統一部署策略](#第-10-部分--統一部署策略)

---

# 第 1 部分 — 專案需求

## 1.1 本專案做什麼

OpenRadiusWeb 是一套**網路存取控制（NAC）系統**，用於控管哪些使用者與裝置可以連上企業網路。系統執行三項主要任務：

1. **認證**使用者與裝置（透過 802.1X RADIUS/EAP 或 MAC 旁路 MAB）
2. **授權**將其分派至特定 VLAN/ACL（依據策略與 AD 群組成員資格）
3. **執行**透過 Change-of-Authorization (CoA) 在條件變化時即時調整

技術架構為以 **FreeRADIUS 3.2.3** 為核心、Docker Compose 部署的多微服務系統，搭配 React/Ant Design 前端與 FastAPI Gateway。

## 1.2 功能性需求（已實作）

| 編號 | 需求 | 優先序 |
|------|------|--------|
| F1 | 透過 802.1X 認證使用者（PEAP, EAP-TLS, EAP-TTLS, MSCHAPv2） | 核心 |
| F2 | 透過 MAB 認證裝置（MAC 白名單） | 核心 |
| F3 | 在 LDAP/Active Directory 查詢使用者身份 | 核心 |
| F4 | 依 AD 群組成員資格動態指派 VLAN | 核心 |
| F5 | 發送 CoA（RFC 5176）以中斷／重認證／變更 VLAN | 核心 |
| F6 | 被動（ARP/DHCP）與主動（Nmap/SNMP）裝置探索 | 核心 |
| F7 | 維護裝置清冊與指紋識別 | 核心 |
| F8 | 透過 UI 管理 NAS Client、VLAN、Realm、憑證 | 核心 |
| F9 | 對每個裝置執行策略（條件 → 行動） | 核心 |
| F10 | 記錄每一次 RADIUS 認證嘗試與失敗原因 | 核心 |
| F11 | 維護不可竄改的管理員操作審計記錄 | 核心 |
| F12 | 多租戶資料隔離 | 核心 |
| F13 | 角色式存取控制（admin / operator / viewer） | 核心 |
| F14 | 透過 SSH 管理交換器（Cisco / Aruba / Juniper / HP / Dell / Extreme） | 核心 |
| F15 | 透過 SNMP v2c/v3 管理交換器 | 核心 |

## 1.3 非功能性需求

| 編號 | 需求 | 實作方式 |
|------|------|----------|
| NF1 | API 回應時間 < 500ms (p95) | FastAPI + asyncpg pool=20 |
| NF2 | RADIUS 認證延遲 < 100ms（不含 LDAP） | rlm_orw.py 使用連接池 |
| NF3 | 容忍單一 PostgreSQL 慢查詢 | Redis 速率限制快取 |
| NF4 | 審計記錄保留 ≥ 1 年 | TimescaleDB hypertable |
| NF5 | 認證記錄保留 ≥ 1 年，可依 MAC/使用者查詢 | TimescaleDB hypertable |
| NF6 | 機密資料絕不以明文存於原始碼／設定 | env 檔 + Vault（進行中） |
| NF7 | 登入暴力破解防護 | Redis token bucket + 帳號鎖定 |
| NF8 | 容器化、可重現的部署 | docker-compose.prod.yml |
| NF9 | 所有管理員操作記錄含使用者與 IP | log_audit() helper 全程使用 |
| NF10 | 租戶資料於 SQL 層隔離 | 全部查詢均加 tenant_id WHERE |

## 1.4 範圍外（目前未實作）

下列功能**目前不在範圍內**：

- 訪客入口頁（Captive Portal）／訪客自助註冊
- BYOD 入網（mobileconfig、ONC、Win Profile）
- 端點合規性／姿態檢查
- 高可用性／叢集
- 多因素認證（管理員登入）
- SAML SSO（管理員登入）
- 防火牆 SSO 推送（Palo Alto、Forti 等）
- MDM 整合（Intune、JAMF）
- TACACS+ 設備管理 AAA

## 1.5 架構約束

| 約束 | 理由 |
|------|------|
| 每個微服務有自己的 DB 連接池 | 獨立擴充；DB 負載隔離 |
| 服務間僅透過 NATS JetStream 通訊 | 服務間禁用 HTTP；可靠投遞 |
| 前端使用配備 JWT 攔截器的 axios 實例 | 集中認證；無需逐一處理 |
| 所有變更操作寫入 audit_log | 法規合規與鑑識 |
| 所有時序資料使用 TimescaleDB hypertable | 快速時間區間查詢；自動分區 |
| 所有策略／機密資料以租戶為單位儲存 | 多租戶隔離 |

---

# 第 2 部分 — 核心功能盤點

## 2.1 程式碼盤點統計

| 指標 | 數量 |
|------|------|
| HTTP 路由處理器 | 107 |
| NATS 事件通道 | 8 |
| 微服務 | 8（gateway, discovery, device_inventory, policy_engine, switch_mgmt, freeradius, freeradius_config_watcher, coa_service, event_service） |
| 前端頁面 | 18 |
| Pydantic 領域模型 | 14 |
| 資料庫表 | 17 |
| DB Migration | 5 |
| FreeRADIUS Jinja2 樣板 | 7 |
| 交換器供應商轉接器 | 7 |
| 拆解後原子模組總數 | ~600 |

## 2.2 功能對應表（16 個頂層群組）

| # | 功能群組 | 後端路由 | 前端頁面 | NATS 通道 | DB 表 |
|---|----------|----------|----------|-----------|-------|
| 1 | 認證與使用者管理 | auth.py, profile.py | LoginPage, ProfilePage, UserManagement | — | users, tenants |
| 2 | 裝置清冊 | devices.py | Devices | orw.device.* | devices, device_properties |
| 3 | 裝置探索 | — | — | orw.discovery.* | events |
| 4 | 策略引擎 | policies.py | Policies | orw.policy.* | policies, policy_evaluations |
| 5 | RADIUS 認證 | radius_auth_log.py | AccessTracker | — | radius_auth_log |
| 6 | 動態 VLAN | group_vlan_mappings.py | GroupVlanMappings | — | group_vlan_mappings |
| 7 | MAB | mab_devices.py | MabDevices | — | mab_devices |
| 8 | CoA | coa.py | CoAPage | orw.coa.* | （使用 radius_auth_log + audit_log） |
| 9 | RADIUS 設定 | ldap_servers.py, radius_realms.py, nas_clients.py, vlans.py, freeradius_config.py | LdapServers, Realms, NasClients, VlanManagement, FreeRadiusConfig | orw.config.freeradius.apply | ldap_servers, radius_realms, nas_clients, vlans, freeradius_config |
| 10 | 憑證 | certificates.py | CertificatesPage | — | certificates |
| 11 | 交換器管理 | network_devices.py | Switches | orw.switch.* | network_devices, switch_ports |
| 12 | 審計與記錄 | audit.py | AuditLog | — | audit_log |
| 13 | 802.1X 總覽 | dot1x_overview.py | Dot1xOverview | — | （彙總查詢） |
| 14 | 事件服務 | — | — | （消費所有事件） | events |
| 15 | 系統設定 | settings.py | SystemSettings | — | system_settings |
| 16 | 健康／監控 | health.py | Dashboard | — | — |

## 2.3 端點目錄（後端）

### 2.3.1 認證 (`/auth`)

| 方法 | 路徑 | 權限 | 用途 |
|------|------|------|------|
| POST | /auth/login | 公開 | 登入並回傳 JWT |
| GET | /auth/me | 已登入 | 目前使用者資訊 |
| POST | /auth/users | 管理員 | 建立使用者 |
| GET | /auth/users | operator+ | 列出使用者 |
| GET | /auth/users/{id} | operator+ | 取得使用者 |
| PUT | /auth/users/{id} | 管理員 | 更新使用者 |
| DELETE | /auth/users/{id} | 管理員 | 刪除使用者 |
| POST | /auth/users/{id}/reset-password | 管理員 | 重設密碼 |
| GET | /auth/roles | 已登入 | RBAC 矩陣 |

### 2.3.2 個人檔案 (`/profile`)、裝置 (`/devices`)、策略 (`/policies`)、CoA (`/coa`)

各群組遵循標準 5 端點 CRUD 模式（`GET 列表`, `POST`, `GET {id}`, `PUT/PATCH {id}`, `DELETE {id}`）並加上各自特殊端點（test、lookup、generate、simulate 等）。

### 2.3.3 RADIUS 設定群組

`/ldap-servers`, `/radius/realms`, `/nas-clients`, `/vlans`, `/mab-devices`, `/group-vlan-mappings`, `/certificates`, `/network-devices`, `/freeradius-config`, `/settings`

每組遵循標準 5 端點 CRUD 模式。

### 2.3.4 RADIUS 認證記錄、CoA、審計

| 方法 | 路徑 | 用途 |
|------|------|------|
| GET | /radius/auth-log | 認證嘗試歷史，含篩選 |
| POST | /coa/by-mac | 依 MAC 發送 CoA |
| POST | /coa/by-username | 依使用者發送 |
| POST | /coa/by-session | 依 session ID 發送 |
| POST | /coa/bulk | 批次最多 100 個目標 |
| GET | /coa/history | CoA 事件審計 |
| GET | /coa/active-sessions | 即時 session 清單 |
| GET | /audit-log | 審計記錄 |
| GET | /audit-log/export | 匯出 JSON / CSV |
| GET | /health | 存活檢查 |
| GET | /dot1x-overview | 802.1X 儀表板資料 |

完整 OpenAPI 規格見 [docs/api/openapi.yaml](api/openapi.yaml) 或啟動後造訪 `/docs`。

## 2.4 NATS Subject 目錄

| Subject | 發佈者 | 訂閱者 | 用途 |
|---------|--------|--------|------|
| orw.device.discovered | discovery | device_inventory | 發現新裝置 |
| orw.device.evaluated | policy_engine | event_service | 策略結果 |
| orw.policy.evaluate_device | gateway, device_inventory | policy_engine | 觸發評估 |
| orw.policy.action.* | policy_engine | event_service | 策略行動 |
| orw.switch.set_vlan | policy_engine, gateway | switch_mgmt | 變更 VLAN |
| orw.switch.bounce_port | gateway | switch_mgmt | 震盪埠口 |
| orw.switch.poll_requested | gateway | switch_mgmt | 輪詢埠口狀態 |
| orw.discovery.scan_request | gateway | discovery | 主動掃描 |
| orw.coa.send | gateway, policy_engine | coa_service | RADIUS CoA |
| orw.config.freeradius.apply | gateway | freeradius_config_watcher | 重生產設定 |

## 2.5 資料庫表目錄

| 表 | 用途 | 類型 |
|----|------|------|
| tenants | 多租戶根 | 標準 |
| users | 本地使用者帳戶 | 標準 |
| devices | 裝置清冊 | 標準 |
| device_properties | EAV 可擴充屬性 | 標準 |
| policies | 存取策略 | 標準 |
| policy_evaluations | 評估歷史 | 標準 |
| network_devices | 交換器／NAS | 標準 |
| switch_ports | 各埠狀態 | 標準 |
| nas_clients | RADIUS NAS 註冊 | 標準 |
| ldap_servers | LDAP/AD 設定 | 標準 |
| radius_realms | Realm 鏈 | 標準 |
| vlans | VLAN 註冊 | 標準 |
| mab_devices | MAB 白名單 | 標準 |
| group_vlan_mappings | 動態 VLAN 對應 | 標準 |
| certificates | PKI / EAP-TLS | 標準 |
| system_settings | 租戶設定 | 標準 |
| radius_auth_log | 認證嘗試 | TimescaleDB hypertable |
| events | 跨服務事件 | TimescaleDB hypertable |
| audit_log | 管理員操作軌跡 | TimescaleDB hypertable |

---

# 第 3 部分 — 原子模組目錄

**原子模組**＝具有**單一變更原因**、**單一輸入形狀**、**單一輸出形狀**、**單一副作用類別**的函式或類別。

## 3.1 19 種原子模式

| 模式 | 命名後綴慣例 | 副作用 | 測試難度 |
|------|--------------|--------|----------|
| **Validator（驗證器）** | `validate_*` | 無（純函式） | 簡單 |
| **Normalizer（正規化器）** | `normalize_*` | 無（純函式） | 簡單 |
| **Parser（解析器）** | `parse_*` | 無（純函式） | 簡單 |
| **Formatter（格式化器）** | `format_*` | 無（純函式） | 簡單 |
| **Mapper（轉換器）** | `map_*`, `*_to_*` | 無（純函式） | 簡單 |
| **Comparator（比較器）** | `match_*`, `compare_*` | 無（純函式） | 簡單 |
| **Builder（建構器）** | `build_*` | 無（純函式） | 簡單 |
| **Serializer（序列化器）** | `serialize_*`, `*_to_dict` | 無（純函式） | 簡單 |
| **Resolver（解析查詢器）** | `lookup_*`, `resolve_*` | DB 讀取 | 容易 |
| **Query（查詢器）** | `query_*`, `count_*` | DB 讀取 | 容易 |
| **Repository（儲存庫）** | `save_*`, `update_*`, `delete_*` | DB 寫入 | 容易 |
| **Command（命令）** | 動作動詞 | DB 寫入 + 事件 | 中等 |
| **Publisher（發佈者）** | `publish_*` | NATS 發佈 | 容易（mock NATS） |
| **Subscriber（訂閱者）** | `handle_*`, `on_*` | 視情況 | 中等 |
| **Authorizer（授權檢查）** | `require_*`, `authorize_*` | 無或拋出 | 容易 |
| **Auditor（審計器）** | `log_audit*` | DB 寫入 | 容易 |
| **Generator（產生器）** | `generate_*` | 加密／RNG | 容易 |
| **Hasher（雜湊器）** | `hash_*`, `verify_*` | 純 CPU | 容易 |
| **Counter（計數器）** | `check_*`, `increment_*` | Redis I/O | 容易 |

## 3.2 跨領域原子（共用程式庫）

這些原子被多個功能使用，置於 `shared/orw_common/` 中，方便發現與重用。

### 3.2.1 MAC 位址原子

| 原子 | 模式 | 輸入 → 輸出 | 單一職責 |
|------|------|-------------|----------|
| `validate_mac_address` | Validator | `str → bool` | 是否為有效 MAC？ |
| `normalize_mac_to_colon` | Normalizer | `str → str` | 任意格式 → `aa:bb:cc:dd:ee:ff` |
| `normalize_mac_to_dashed` | Normalizer | `str → str` | 任意格式 → `aa-bb-cc-dd-ee-ff` |
| `normalize_mac_to_dotted` | Normalizer | `str → str` | 任意格式 → `aabb.ccdd.eeff`（Cisco 格式） |
| `mac_to_oui` | Mapper | `str → str` | 取前 3 個 octet |
| `lookup_vendor_by_oui` | Resolver | `oui, db → str?` | 廠商名稱 |

### 3.2.2 使用者名稱／Realm 原子

| 原子 | 模式 | 單一職責 |
|------|------|----------|
| `parse_username_realm` | Parser | `user@realm` 或 `DOM\user` → (user, realm, format) |
| `strip_realm` | Mapper | `user@realm` → `user` |
| `extract_realm` | Mapper | `user@realm` → `realm` |
| `format_upn` | Formatter | (user, realm) → `user@realm` |
| `format_downlevel` | Formatter | (domain, user) → `DOMAIN\user` |

### 3.2.3 Distinguished Name (DN) 原子

| 原子 | 模式 | 單一職責 |
|------|------|----------|
| `parse_dn` | Parser | `CN=x,OU=y,DC=z` → `[(attr, value), ...]` |
| `extract_cn` | Mapper | DN → 第一個 CN |
| `extract_ou_path` | Mapper | DN → OU 列表 |
| `format_dn` | Formatter | 列表 → DN 字串 |

### 3.2.4 時間原子

| 原子 | 模式 | 單一職責 |
|------|------|----------|
| `now_utc` | Generator | UTC datetime |
| `to_iso8601` | Formatter | datetime → ISO 字串 |
| `parse_iso8601` | Parser | ISO 字串 → datetime |
| `is_expired` | Comparator | dt < 現在？ |
| `add_minutes` | Mapper | dt + n 分鐘 |
| `to_unix_timestamp` | Mapper | datetime → epoch |

### 3.2.5 加密原子

| 原子 | 模式 | 單一職責 |
|------|------|----------|
| `hash_password` | Hasher | bcrypt(12) |
| `verify_password` | Hasher | 等時比對 |
| `generate_jwt` | Generator | HS256 簽章 |
| `verify_jwt` | Hasher | HS256 驗章 + 解碼 claims |
| `generate_random_token` | Generator | secrets.token_urlsafe(n) |
| `generate_uuid4` | Generator | UUID v4 |
| `compute_sha256` | Hasher | SHA-256 摘要 |
| `compute_cert_fingerprint` | Hasher | DER → SHA-256 hex |
| `generate_rsa_keypair` | Generator | 新 RSA 金鑰 |
| `sign_certificate` | Generator | CA 簽發憑證 |
| `parse_pem_certificate` | Parser | PEM → x509 |

### 3.2.6 資料庫原子

| 原子 | 模式 | 單一職責 |
|------|------|----------|
| `get_db` | Resolver | DI 依賴 |
| `get_db_context` | Resolver | 非同步 context manager |
| `build_safe_set_clause` | Builder | 安全 `SET col=val,...` |
| `build_pagination` | Builder | `LIMIT x OFFSET y` |
| `build_order_by` | Builder | 受驗證的 `ORDER BY` |
| `coerce_ip_address` | Mapper | IPv4Address → str |
| `coerce_macaddr` | Mapper | EUI → str |
| `coerce_uuid` | Mapper | UUID → str |
| `apply_tenant_filter` | Mapper | 加上 `WHERE tenant_id=$N` |

### 3.2.7 NATS 原子

| 原子 | 模式 | 單一職責 |
|------|------|----------|
| `nats_connect` | Resolver | 取得連線 |
| `nats_publish` | Publisher | 發後即忘 |
| `nats_subscribe` | Subscriber | 綁定處理器 + queue group |
| `ensure_jetstream_stream` | Command | 不存在時建立 |
| `delete_stale_consumer` | Command | 清除舊 durable |
| `serialize_event` | Mapper | dict → JSON bytes |
| `deserialize_event` | Mapper | JSON bytes → dict |

### 3.2.8 認證／RBAC 原子

| 原子 | 模式 | 單一職責 |
|------|------|----------|
| `extract_bearer_token` | Parser | Authorization header → token |
| `decode_token_claims` | Mapper | JWT → claims |
| `get_current_user` | Resolver | Token → User |
| `require_admin` | Authorizer | 非管理員則拋出 |
| `require_operator` | Authorizer | 非 operator 以上則拋出 |
| `require_self_or_admin` | Authorizer | 自己或管理員 |
| `check_login_rate` | Counter | 回傳 bool 並遞增 |
| `check_lockout` | Counter | 回傳 bool |
| `record_failed_login` | Counter | 遞增 |
| `clear_lockout` | Counter | 重設 |

### 3.2.9 審計／日誌原子

| 原子 | 模式 | 單一職責 |
|------|------|----------|
| `log_audit` | Auditor | 寫入審計列 |
| `extract_client_ip` | Parser | 由 header 解出 IP |
| `format_log_record` | Formatter | 結構化 JSON 行 |
| `get_logger` | Resolver | 取得各模組 logger |

### 3.2.10 錯誤 → HTTP 對應原子

| 原子 | 模式 | 單一職責 |
|------|------|----------|
| `map_not_found_to_404` | Mapper | NotFoundError → 404 |
| `map_conflict_to_409` | Mapper | ConflictError → 409 |
| `map_validation_to_422` | Mapper | ValidationError → 422 |
| `map_auth_to_401` | Mapper | AuthenticationError → 401 |
| `map_authz_to_403` | Mapper | AuthorizationError → 403 |
| `map_rate_limit_to_429` | Mapper | RateLimitError → 429 |
| `map_unhandled_to_500` | Mapper | 通用 500 + 記錄 |

### 3.2.11 分頁／篩選原子

| 原子 | 模式 | 單一職責 |
|------|------|----------|
| `parse_pagination` | Validator | 限制 limit/offset 範圍 |
| `parse_time_range` | Validator | 建立時間篩選 |
| `wrap_paginated_response` | Mapper | 標準分頁封包 |

**跨領域原子小計：~80 個**

## 3.3 各功能群組原子（簡述，詳見英文版 §3.3）

| 群組 | 原子數 | 涵蓋內容 |
|------|--------|----------|
| 群組 1 — 認證／使用者 | 17 | 登入、CRUD、密碼重設、RBAC |
| 群組 2 — 裝置清冊 | 15 | CRUD、屬性 EAV、上插 |
| 群組 3 — 探索 | 15 | 被動 ARP/DHCP、主動 Nmap/SNMP、指紋 |
| 群組 4 — 策略引擎 | 40 | 評估、模擬、行動派發 |
| 群組 5 — RADIUS 認證 | 31 | FreeRADIUS Python hook、AD 錯誤對應 |
| 群組 6 — 動態 VLAN | 11 | 群組對應查詢 |
| 群組 7 — MAB | 10 | 白名單、過期檢查 |
| 群組 8 — CoA | 17 | 封包建構、簽章、UDP/3799 |
| 群組 9 — RADIUS 設定 | 57 | LDAP / Realm / NAS / VLAN / FR Config |
| 群組 10 — 憑證 | 20 | PKI、CA 簽發、CSR |
| 群組 11 — 交換器管理 | 28 | SSH/SNMP、各廠牌轉接器 |
| 群組 12 — 審計 | 6 | 查詢、匯出 |
| 群組 13 — 802.1X 總覽 | 10 | 彙總統計 |
| 群組 14 — 事件服務 | 7 | 事件聚合、外部整合 |
| 群組 15 — 設定 | 4 | 系統參數 |
| 群組 16 — 健康 | 4 | 存活檢查 |
| **小計** | **~310** | 各功能特有原子 |

加上前端 React 原子（每頁約 10 個 hook/handler/formatter，18 頁共 ~150 個）。

## 3.4 原子模組統計總覽

| 類別 | 數量 |
|------|------|
| 跨領域（共用程式庫） | ~80 |
| 各功能特有 | ~310 |
| 前端 | ~150 |
| **原子模組總計** | **~600** |
| — 純函式（無 I/O） | ~180 |
| — DB 讀取 | ~70 |
| — DB 寫入 | ~50 |
| — NATS 發佈／訂閱 | ~35 |
| — 網路（LDAP/SNMP/SSH/UDP） | ~25 |
| — 加密 | ~12 |

---

# 第 4 部分 — 組合流程

本節說明原子如何組合成使用者面對的功能。每個流程是一連串原子呼叫的有序清單。

## 4.1 範例：`POST /devices`

```
請求：POST /devices
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

共 16 個原子。7 個純函式、4 個 DB 操作、1 個 NATS。
```

## 4.2 範例：`POST /auth/login`

```
  1. parse_login_payload(body)              [Validator]
  2. extract_client_ip(req)                 [Parser]
  3. check_login_rate(ip)                   [Counter, Redis]
  4. check_lockout(username)                [Counter, Redis]
  5. lookup_user_by_username(db, name)      [Resolver, DB]
  6. verify_password(plain, hash)           [Hasher]
  7a. （失敗）record_failed_login(name)      [Counter, Redis]
  7b. （成功）clear_lockout(name)            [Counter, Redis]
  8. update_last_login(db, user_id)         [Repository, DB]
  9. generate_jwt({sub, role, tenant})      [Generator, Crypto]
 10. log_audit("login","user",id,user)      [Auditor, DB]
 11. serialize_login_response(token, user)  [Serializer]
 12. wrap_response(200, body)               [Mapper]
```

## 4.3 範例：`POST /coa/by-mac`

```
  1. require_operator(user)                  [Authorizer]
  2. parse_coa_payload(body)                 [Validator]
  3. validate_coa_action(action)             [Validator]
  4. normalize_mac_to_colon(mac)             [Normalizer]
  5. find_active_sessions_by_mac(db, mac)    [Query, DB]
  6. （每個 session）
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

## 4.4 範例：RADIUS Access-Request → Access-Accept

```
階段：authorize()
  1. extract_attrs(p)                        [Mapper]
  2. parse_username_components(name)         [Parser]
  3. detect_auth_method(req, {})             [Mapper]
  4. （若為 MAB）
       4a. normalize_mac_to_colon(mac)       [Normalizer]
       4b. lookup_mab_device(db, mac, tenant)[Resolver, DB]
       4c. is_mab_currently_valid(device)    [Comparator]
       4d. build_mab_accept_reply(vlan_id)   [Builder]
       4e. 回傳 RLM_OK + reply               [native]

階段：post_auth()（成功時）
  1. extract_attrs(p)                        [Mapper]
  2. detect_auth_method(req, {})             [Mapper]
  3. （若為 802.1X）
       3a. lookup_ldap_server(db, tenant)    [Resolver, DB]
       3b. bind_ldap(server, dn, pw)         [Resolver, NET]
       3c. search_user_groups(conn, ...)     [Resolver, NET]
       3d. extract_groups_from_memberOf(...) [Parser]
       3e. lookup_vlan_for_groups(db, grps)  [Resolver, DB]
       3f. build_vlan_assign_reply(vlan)     [Builder]
  4. build_auth_log_row(req, reply, ...)     [Builder]
  5. insert_auth_log(db, row)                [Repository, DB]
  6. radlog_emit("INFO", msg)                [Publisher, FR]
  7. 回傳 RLM_UPDATED + reply                [native]
```

## 4.5 範例：裝置被探索 → 策略行動

```
觸發：PassiveMonitor 看到 ARP 回應

discovery 服務：
  1. parse_arp_packet(frame)                 [Parser]
  2. build_device_payload(raw)               [Builder]
  3. publish_device_discovered(payload)      [Publisher, NATS]

device_inventory 服務：
  4. handle_device_discovered(msg)           [Subscriber]
  5. deserialize_event(bytes)                [Mapper]
  6. normalize_mac_to_colon(mac)             [Normalizer]
  7. upsert_device(db, payload)              [Repository, DB]
  8. add_device_property(...)                [Repository, DB]
  9. record_event(db, "device_discovered")   [Repository, DB]
 10. publish_evaluate_device(device_id)      [Publisher, NATS]

policy_engine 服務：
 11. handle_evaluate_device(msg)             [Subscriber]
 12. load_device_context(device_id)          [Query, DB]
 13. load_active_policies(tenant_id)         [Query, DB]
 14. （依優先序逐一策略）
       14a. evaluate_policy_and(conds, ctx)  [Mapper]
       14b. record_policy_evaluation(...)    [Repository, DB]
       14c. （若匹配）select_actions()        [Mapper]
       14d. （每個行動）dispatch_action()    [Command]
              → dispatch_vlan_assign         [Publisher, NATS]
              → dispatch_coa                 [Publisher, NATS]

switch_mgmt 服務：
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

此 23 個原子的流程跨越 4 個微服務及 5 種副作用類別（DB 讀、DB 寫、NATS 發、NATS 收、網路）。

---

# 第 5 部分 — 開發規範

## 5.1 原子化程式的 8 條規則

| # | 規則 | 為何 |
|---|------|------|
| 1 | 每個原子**只有一個變更原因** | 改動不會擴散 |
| 2 | 每個原子**只有一個回傳形狀** | 不可多型回傳 |
| 3 | 每個原子**只有一個副作用類別** | DB-讀 或 DB-寫 或 NATS，不可混用 |
| 4 | **純原子放於 `shared/orw_common`** | 容易發現與重用 |
| 5 | **有副作用的原子放於各服務本地** | 擁有權清楚 |
| 6 | **以職責命名，而非實作** | `validate_mac_address`，非 `is_six_octets_hex` |
| 7 | **函式名稱不可包含「and」** | 如有，請拆成兩個原子 |
| 8 | **純原子徹底測試，I/O 邊界以 mock 取代** | 用簡單測試達 80% 覆蓋率 |

## 5.2 反模式（看到立即重構）

| 壞味道 | 重構為 |
|--------|--------|
| 函式既驗證又寫 DB | validator + repository |
| 函式既回傳又發 NATS | command（回傳） + publisher |
| Helper 參數超過 5 個 | 抽出 value object |
| 函式名稱含「and」 | 拆成兩個原子 |
| 函式同時用 `db` 和 `redis` | 拆成兩個原子 |
| 函式同時做 DB 讀、LDAP、DB 寫 | 拆成三個原子 |
| 一個函式既寫審計又呼叫 logger | auditor 和 logger 是不同職責 |

## 5.3 新功能開發檢查清單

新增功能時請依照下列流程：

- [ ] **定義使用者面對的端點**（HTTP method + path）
- [ ] 在寫程式碼前，**先繪製原子流程**（如第 4 部分編號清單）
- [ ] **搜尋共用程式庫**找可重用原子
- [ ] **辨識需要的新原子**（每個依職責命名）
- [ ] **先寫純原子**（validator、builder、mapper）
- [ ] **單元測試每個純原子**
- [ ] **接著寫有副作用的原子**（repository、publisher）
- [ ] **整合測試完整流程**（一個正向＋一個負向）
- [ ] 每個變更操作都加上 **audit log 原子呼叫**
- [ ] 在本手冊對應群組**寫上文件**

## 5.4 檔案配置

**標準佈局為功能導向 (`features/<name>/`)。** 新增功能必須採用，既有扁平 `routes/` 為過渡狀態，依 §10.6.3 逐步遷移。

```
shared/orw_common/          # 跨領域原子（純 + 加密）
  ├── config.py             # 設定（無原子 — 只 DI）
  ├── database.py           # DB resolver
  ├── nats_client.py        # NATS publisher/subscriber 原子
  ├── exceptions.py         # 錯誤型別
  ├── logging.py            # Logger resolver
  ├── policy_evaluator.py   # 策略比較原子（純）
  └── models/               # Pydantic value objects

services/<svc>/
  ├── main.py               # 進入點（gateway 為 FastAPI app；其他為 NATS subscriber 註冊）
  ├── features/             # 功能導向配置（標準佈局）
  │   └── <feature>/
  │       ├── routes.py     # 第 3 層 — REST 路由（僅 gateway）
  │       ├── service.py    # 第 2 層 — 用例組合
  │       ├── repository.py # 第 2 層 — DB 讀寫原子
  │       ├── events.py     # 第 2 層 — NATS publisher/subscriber 原子
  │       ├── schemas.py    # Pydantic 請求／回應／事件 model
  │       ├── __init__.py   # 明確公開 API（僅暴露其他功能可 import 的部分）
  │       └── tests/        # 單元 + 整合測試
  ├── middleware/           # 跨功能（僅 gateway） — auth、request_id 等
  └── utils/                # 服務本地 helper（罕見；偏好放進對應 feature/）
```

每個 `features/<name>/` 自包含、單一團隊可端到端擁有。詳細理由與跨功能溝通規則見 §10.6。

## 5.5 命名慣例

| 型別 | 前綴／後綴 | 範例 |
|------|------------|------|
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
| Repository | 動詞（insert/update/delete/upsert/save） | `insert_device` |
| Command | 動作動詞 | `disconnect_session` |
| Publisher | `publish_*` | `publish_device_discovered` |
| Subscriber | `handle_*`, `on_*` | `handle_evaluate_device` |
| Authorizer | `require_*` | `require_admin` |
| Auditor | `log_audit*` | `log_audit` |
| Generator | `generate_*` | `generate_jwt` |
| Hasher | `hash_*`, `verify_*` | `hash_password` |
| Counter | `check_*`, `increment_*` | `check_login_rate` |

## 5.6 各原子類別的測試策略

| 原子類別 | 測試策略 | 覆蓋率目標 |
|----------|----------|------------|
| Validator | 表格驅動（好/壞配對） | 100% |
| Normalizer | 表格驅動（輸入 → 輸出） | 100% |
| Parser | 表格驅動 + 模糊測試 | 95% |
| Formatter | 表格驅動 | 100% |
| Mapper | 表格驅動 | 100% |
| Comparator | 表格驅動 | 100% |
| Builder | 表格驅動 | 100% |
| Serializer | Snapshot 測試 | 90% |
| Resolver | Mock DB / fake repo | 80% |
| Query | 對測試 DB 整合測試 | 70% |
| Repository | 對測試 DB 整合測試 | 80% |
| Command | 整合測試（DB + NATS） | 70% |
| Publisher | Mock NATS，斷言 subject + payload | 90% |
| Subscriber | 注入假訊息 | 80% |
| Authorizer | 表格驅動（角色 × 資源） | 100% |
| Auditor | 驗證 INSERT 發生 | 90% |
| Generator | Mock RNG；驗證形狀 + 簽章 | 95% |
| Hasher | 驗證 roundtrip + 等時 | 95% |
| Counter | Mock Redis；驗證遞增 | 90% |

---

# 第 6 部分 — 快速參考索引

## 6.1 依領域查找原子

**MAC 位址** → §3.2.1
**使用者名稱／Realm** → §3.2.2
**LDAP DN** → §3.2.3
**時間／日期** → §3.2.4
**加密／JWT／憑證** → §3.2.5
**資料庫** → §3.2.6
**NATS** → §3.2.7
**認證／RBAC** → §3.2.8
**審計／日誌** → §3.2.9
**錯誤 → HTTP** → §3.2.10
**分頁** → §3.2.11

## 6.2 統計快速一覽

| 指標 | 數量 |
|------|------|
| HTTP 端點 | 107 |
| NATS subject | 8 |
| 微服務 | 8 |
| 前端頁面 | 18 |
| 資料庫表 | 17 |
| Pydantic 模型 | 14 |
| FreeRADIUS 樣板 | 7 |
| 交換器供應商轉接器 | 7 |
| **原子模組（總計）** | **~600** |

---

# 第 7 部分 — API 規格 (OpenAPI)

HTTP API 以 **OpenAPI 3.0** 格式記錄。共有三個來源：

| 來源 | URL／路徑 | 用途 |
|------|-----------|------|
| **自動產生（即時）** | `http://<host>:8000/docs`（Swagger UI） | 互動式探索 |
| **自動產生（即時）** | `http://<host>:8000/openapi.json` | 工具／SDK 產生 |
| **靜態規格檔** | [`docs/api/openapi.yaml`](api/openapi.yaml) | 版本控管的參考 |

## 7.1 慣例

| 慣例 | 細節 |
|------|------|
| **Base URL** | `/api/v1` |
| **認證** | `Authorization: Bearer <jwt>`（HS256，60 分鐘有效） |
| **Content-Type** | `application/json`（請求與回應） |
| **分頁** | `?limit=<n>&offset=<n>`（最大 200，預設 50） |
| **時間格式** | ISO 8601 UTC（`2026-04-27T12:34:56Z`） |
| **識別碼** | UUID v4 |
| **錯誤封包** | `{"detail": "<訊息>", "code": "<錯誤碼>"}` |

## 7.2 標準 HTTP 狀態碼

| 代碼 | 意義 | 觸發情境 |
|------|------|----------|
| 200 | OK | 讀取／更新成功 |
| 201 | Created | 建立成功 |
| 204 | No Content | 刪除成功 |
| 400 | Bad Request | 格式錯誤 |
| 401 | Unauthorized | 缺少／無效 JWT |
| 403 | Forbidden | RBAC 拒絕 |
| 404 | Not Found | 資源不存在 |
| 409 | Conflict | 唯一性衝突 |
| 422 | Unprocessable Entity | Pydantic 驗證失敗 |
| 429 | Too Many Requests | 觸及速率限制 |
| 500 | Internal Server Error | 未處理例外 |

## 7.3 端點摘要

完整端點列表請見 [`docs/api/openapi.yaml`](api/openapi.yaml)。重點摘要如下：

### 7.3.1 Auth

| 方法 | 路徑 | 請求 | 回應 |
|------|------|------|------|
| POST | /api/v1/auth/login | `{ username, password }` | `{ access_token, expires_in, user }` |
| GET | /api/v1/auth/me | （已登入） | `User` |
| POST | /api/v1/auth/users | `UserCreate`（管理員） | `User`（201） |

### 7.3.2 Devices / Policies / CoA

每個資源遵循標準 5 端點 CRUD。

## 7.4 產生客戶端 SDK

OpenAPI 規格即為合約。產生客戶端：

```bash
# TypeScript
openapi-typescript http://localhost:8000/openapi.json -o frontend/src/api-types.ts

# Python
openapi-python-client generate --url http://localhost:8000/openapi.json

# Postman / Insomnia
# 直接匯入 http://localhost:8000/openapi.json
```

## 7.5 新增端點

每新增端點，FastAPI 會自動發佈到 `/openapi.json`。要保持靜態規格檔同步：

```bash
# 從容器取出
curl http://localhost:8000/openapi.json > docs/api/openapi.yaml.tmp
yq -y . docs/api/openapi.yaml.tmp > docs/api/openapi.yaml
rm docs/api/openapi.yaml.tmp
git diff docs/api/openapi.yaml  # 檢視
```

新端點必備：

- [ ] Pydantic 請求／回應 model 含描述
- [ ] `responses=` 中設定狀態碼（建立用 201，刪除用 204）
- [ ] `tags=` 設為資源群組
- [ ] 路由含 `summary`（單行）和 `description`（多行）
- [ ] Model 透過 `Field(..., example=...)` 提供範例

---

# 第 8 部分 — 解耦設計（DI + 事件驅動）

OpenRadiusWeb 透過**兩種互補的解耦策略**避免「改 A 壞 B」：

1. **依賴注入 (Dependency Injection, DI)** — 在單一程序內（gateway、各服務）
2. **事件驅動訊息 (NATS)** — 在程序之間（微服務）

## 8.1 Gateway 中的依賴注入

### 8.1.1 FastAPI 的 `Depends()` 模式

每個路由處理器透過建構子式注入接收依賴：

```python
# services/gateway/routes/devices.py

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from orw_common.database import get_db
from gateway.middleware.auth import get_current_user, require_operator

router = APIRouter()

@router.get("/devices")
async def list_devices(
    db: AsyncSession = Depends(get_db),          # ← 注入
    user: dict = Depends(get_current_user),      # ← 注入
    _ = Depends(require_operator),               # ← 注入（被拒則拋例外）
    limit: int = 50,
    offset: int = 0,
):
    return await query_devices_by_filter(db, user["tenant_id"], limit, offset)
```

**為何重要：** 處理器不會 import 或建構 DB session、認證 token、RBAC 檢查。測試透過 `app.dependency_overrides[get_db] = fake_db` 注入假物件。

### 8.1.2 五種標準可注入物件

| 依賴 | 提供者 | 回傳 | 測試覆寫 |
|------|--------|------|----------|
| `Depends(get_db)` | `shared/orw_common/database.py` | `AsyncSession` | 記憶體 SQLite 或測試 DB |
| `Depends(get_redis_client)` | `gateway/utils/redis_client.py` | `aioredis.Redis` | `fakeredis` |
| `Depends(get_current_user)` | `gateway/middleware/auth.py` | `User dict` | 假使用者 |
| `Depends(require_admin)` | 同上 | None 或 403 | 測試時 no-op |
| `Depends(get_settings)` | `shared/orw_common/config.py` | `Settings` | 覆寫值 |

### 8.1.3 跨服務邊界的分層 DI

對於背景服務（非 FastAPI），使用 **Composition Root** 模式：依賴在 `main()` 建構一次，再向下傳遞。

```python
# services/policy_engine/main.py

async def main():
    db_pool = await create_db_pool(settings.database_url)
    nats_conn = await nats.connect(settings.nats_url)

    # 建立 worker 並注入所有依賴
    worker = PolicyWorker(db=db_pool, nats=nats_conn)

    await worker.subscribe()
    await worker.run()
```

`PolicyWorker` 透過建構子接收 `db` 和 `nats`，從不全域 import。意義：
- 測試可注入 `MockNATS()` 和 `MockDB()`，直接測試 `worker.handle_evaluate_device()`
- 未來更換 NATS（例如改用 Redis Streams）只需改 `main.py`

### 8.1.4 DI 反模式

| 反模式 | 重構為 |
|--------|--------|
| 模組頂層 `db = create_engine(...)` | 透過 `Depends(get_db)` 注入 |
| 路由內 `import requests; requests.get(...)` | 注入 HTTP client interface |
| 函式內 `os.environ["DB_URL"]` | 注入 `Settings` |
| 寫死 singleton `nats_client.publish(...)` | 注入 `Publisher` interface |

## 8.2 透過 NATS 事件驅動解耦

### 8.2.1 為何用 NATS 而非 HTTP

| 面向 | 同步 HTTP | 非同步 NATS |
|------|-----------|-------------|
| 耦合 | 直接（呼叫者知道被呼叫者 URL） | 間接（subject 命名空間） |
| 失敗模式 | 呼叫者卡住 / 5xx | 在 JetStream 中緩衝 |
| 可測試性 | 需要 mock HTTP server | 注入假訊息 |
| 可擴展性 | 一對一 | 一對多 |
| 適用場景 | 讀取、即時回應 | 變更、扇出、非同步 pipeline |

### 8.2.2 Subject 命名空間慣例

```
orw.<領域>.<事件>
```

| 領域 | 範例 | 發佈者 | 訂閱者 |
|------|------|--------|--------|
| `device` | `discovered`, `evaluated`, `deleted` | discovery, gateway | device_inventory, event_service, policy_engine |
| `policy` | `evaluate_device`, `action.vlan_assign`, `action.coa` | gateway, device_inventory | policy_engine, event_service |
| `switch` | `set_vlan`, `bounce_port`, `port_state_changed` | policy_engine, gateway | switch_mgmt |
| `coa` | `send` | gateway, policy_engine | coa_service |
| `discovery` | `scan_request` | gateway | discovery |
| `config` | `freeradius.apply` | gateway | freeradius_config_watcher |
| `security` | `wazuh.alert` | event_service | （外送：Wazuh） |
| `system` | `health.heartbeat` | 所有服務 | 監控 |

### 8.2.3 發佈者／訂閱者獨立性

關鍵解耦特性：**發佈者不知道有哪些訂閱者**。

```python
# 發佈者（任意服務）
await nats_publish("orw.device.discovered", {"mac": "...", "ip": "..."})
# 不會直接呼叫 device_inventory。

# 訂閱者（device_inventory）
await nats_subscribe("orw.device.discovered", handle_device_discovered, queue="device-inventory")
# 不知道誰發佈了。

# 新增一個訂閱者（例如未來的 risk_scoring 服務）
await nats_subscribe("orw.device.discovered", handle_device_for_risk, queue="risk-scoring")
# 發佈者完全不需更動。
```

### 8.2.4 Durable Consumer (JetStream)

每個訂閱者有 **durable name**。如訂閱者離線，JetStream 保留訊息並在重連後重播。

| 服務 | Durable 名稱 | Stream | 重啟後重播 |
|------|--------------|--------|------------|
| device_inventory | `device-inventory` | orw | 是 |
| policy_engine | `policy-engine` | orw | 是 |
| switch_mgmt | `switch-mgmt` | orw | 是 |
| event_service | `event-service` | orw | 是 |
| coa_service | `coa-service` | orw | 是 |
| freeradius_config_watcher | `freeradius-config-watcher` | orw | 是 |

### 8.2.5 事件 Schema 穩定性

為避免破壞訂閱者，遵守以下規則：

| 規則 | 理由 |
|------|------|
| **只能加欄位，絕不刪除** | 舊訂閱者可忽略新欄位 |
| **絕不更改欄位型別** | 若型別需改，使用新欄位名 |
| **絕不更改 subject 的語意** | 若語意需改，使用新 subject |
| **以 `schema_version` 欄位版本化** | 訂閱者可依版本分支 |
| **每個事件必須記錄於本手冊** | 所有團隊可見 |

### 8.2.6 事件清單（目前）

| Subject | Schema（關鍵欄位） | 發佈者 | 訂閱者 |
|---------|--------------------|--------|--------|
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

## 8.3 耦合圖（如何看依賴）

### 8.3.1 服務內部：分層架構

```
┌──────────────────────────────────────────────┐
│ Routes（FastAPI 處理器 — 僅 gateway）        │  ← 使用 Depends()
├──────────────────────────────────────────────┤
│ Use cases（原子組合）                        │
├──────────────────────────────────────────────┤
│ Atoms（驗證器、建構器、儲存庫）              │
├──────────────────────────────────────────────┤
│ Infrastructure（DB, NATS, Redis, HTTP）      │  ← 注入
└──────────────────────────────────────────────┘
```

每層僅依賴下一層。Atoms 層不會 import FastAPI。

### 8.3.2 服務之間：僅事件

```
┌────────────────┐      NATS      ┌────────────────┐
│ Producer Svc   │ ────────────►  │ Consumer Svc   │
└────────────────┘                └────────────────┘
        │                                  │
        ▼                                  ▼
   PostgreSQL                         PostgreSQL
   （自有連線）                       （自有連線）
```

**禁止：** 服務之間互呼 HTTP API。**允許：** 所有服務從共用的 PostgreSQL 讀取。

### 8.3.3 模組間相容矩陣

| 從 → 到 | 允許？ | 機制 |
|---------|--------|------|
| Route → Atom | ✅ | 直接呼叫 |
| Atom → Atom（同服務） | ✅ | 直接呼叫 |
| Atom → DB | ✅ | 注入 `db` |
| Atom → NATS | ✅ | 注入 `nats` |
| Atom → 另一服務的 HTTP | ❌ | 改用 NATS 事件 |
| Atom → 另一服務的 DB 表 | ⚠️ | 允許但不建議；偏好事件 |
| Service A → Service B（Python import） | ❌ | 改用 NATS 或 shared/orw_common |
| 所有服務 → `shared/orw_common` | ✅ | 共用程式庫 |

## 8.4 不耦合的新增功能流程

新增「功能 X」而不破壞既有程式碼的具體做法：

### 步驟 1 — 決定邊界
- X 是**新端點**？放在 gateway/routes/
- X 是**背景反應**？放在訂閱既有事件的服務中
- X 是**全新領域**？建立新微服務

### 步驟 2 — 定義輸入／輸出
- HTTP：在 `shared/orw_common/models/` 定義請求／回應 Pydantic model
- 事件：在本手冊 §8.2.6 定義 subject + schema

### 步驟 3 — 由原子組合
- 在 §3.2（跨領域）和 §3.3（各功能）搜尋既有原子
- 盡量重用
- 只為真正新的職責寫新原子

### 步驟 4 — 注入依賴，不要 import singleton
- 將 `db`, `nats`, `redis` 作為參數傳入
- FastAPI：使用 `Depends()`
- Worker：傳給建構子

### 步驟 5 — 訂閱事件，不要輪詢 DB 表
- 若 X 對「裝置被探索」反應，訂閱 `orw.device.discovered`
- 不要新增定期 `SELECT * FROM devices` 輪詢

### 步驟 6 — 為下游消費者發佈事件
- 若 X 變更其他服務關心的狀態，發佈新事件
- 在 §8.2.6 加上新 subject
- 不要直接呼叫其他服務

### 步驟 7 — 原子用單元測試，流程用整合測試
- 純原子：表格驅動測試
- DB 原子：對測試 DB
- 完整流程：發佈事件並斷言下游副作用的整合測試

### 步驟 8 — 在 §3.3 記錄新原子，在 §8.2.6 記錄新事件

---

---

# 第 9 部分 — 開發流程

本部分定義**如何**開發功能。核心目標：**高內聚、低耦合** — 每個模組獨立可測試與可替換。

## 9.1 三層架構

每個模組必須屬於下列三層之一：

```
┌────────────────────────────────────────────────────────────┐
│ 第 3 層 — 介面適配（薄殼）                                 │
│ 接收外部輸入，呼叫第 2 層，回傳回應。                      │
│ 範例：REST 路由、RADIUS hook、NATS 訂閱者、CLI、Webhook    │
├────────────────────────────────────────────────────────────┤
│ 第 2 層 — 服務／業務邏輯                                   │
│ 盡量為純函式。相同輸入恆得相同輸出。                       │
│ 範例：策略評估、RADIUS 認證方法判斷、AD 錯誤對應、         │
│       MAC 正規化、JWT 簽章                                 │
├────────────────────────────────────────────────────────────┤
│ 第 1 層 — 基礎設施（地基）                                 │
│ 與外部世界溝通。**這層不能有業務邏輯**。                   │
│ 範例：DB 連接器、NATS client、Redis client、               │
│       Logger、Settings loader、加密原語                    │
└────────────────────────────────────────────────────────────┘
```

### 9.1.1 第 1 層 — 基礎設施

| 模組 | 職責 | 可替換為 |
|------|------|----------|
| `shared/orw_common/database.py` | PostgreSQL 連接池 | MySQL, CockroachDB |
| `shared/orw_common/nats_client.py` | NATS JetStream client | Redis Streams, Kafka |
| `gateway/utils/redis_client.py` | Redis async client | Memcached, in-memory dict |
| `shared/orw_common/config.py` | Pydantic Settings loader | python-decouple, env-based |
| `shared/orw_common/logging.py` | structlog logger | std logging, loguru |
| `shared/orw_common/exceptions.py` | 領域錯誤型別 | （穩定合約） |

**規則：** 第 1 層模組必須可被 import 且無副作用。連線物件透過 DI 建構，不在 import 時建立。

### 9.1.2 第 2 層 — 服務／業務邏輯

| 模組 | 職責 | 純？ |
|------|------|------|
| `shared/orw_common/policy_evaluator.py` | 條件比對 → bool/動作 | 是 |
| `services/auth/.../rlm_orw.py`（helper） | `_detect_auth_method`, `_map_ad_error_code` | 是 |
| 所有 `validate_*` 原子 | 輸入驗證 | 是 |
| 所有 `normalize_*`, `format_*`, `parse_*` 原子 | 資料轉換 | 是 |
| 所有 `match_*`, `compare_*` 原子 | 決策／比對 | 是 |
| 所有 `build_*` 原子 | 組合資料結構 | 是 |
| 憑證／JWT／hash／RSA 原子 | 加密原語 | 是（給定輸入為決定性） |

**規則：** 第 2 層不可有 `db.execute(...)`, `nats.publish(...)`, `redis.get(...)`, `requests.get(...)`。若函式需要 I/O，應放在第 3 層（或拆分 — 純邏輯放第 2 層、副作用放第 3 層）。

### 9.1.3 第 3 層 — 介面適配

| 模組 | 職責 | 做什麼 |
|------|------|--------|
| `gateway/routes/*.py` | REST API | 接收 HTTP → 呼叫第 2/1 層 → 回傳 JSON |
| `gateway/main.py` | App 組裝 | 掛載路由、註冊 middleware、安裝例外處理 |
| `services/auth/.../rlm_orw.py`（`authorize`/`post_auth`/`accounting`） | RADIUS hook | FreeRADIUS 呼叫這些 → 呼叫第 2/1 層 |
| `services/<svc>/main.py` | NATS 訂閱者註冊 | 連線 → 訂閱 → 派發到 handler |
| `frontend/src/pages/*.tsx` | Web UI | 渲染狀態、呼叫 API、處理使用者輸入 |

**規則：** 第 3 層應**薄**。REST handler 超過 ~20 行就是 code smell — 抽取成第 2 層原子。

### 9.1.4 依賴方向

```
       第 3 層（介面）
            │
            ▼ 依賴
       第 2 層（服務）
            │
            ▼ 依賴
       第 1 層（基礎設施）
```

**禁止：** 第 1 層 import 第 2 或 3 層；第 2 層 import 第 3 層。

由 `shared/orw_common` 套件不 import FastAPI/aiohttp/路由來強制執行。

## 9.2 任務分解（如何將功能拆成工作）

新功能進來時，依以下方式拆解：

### 步驟 1 — 描述使用者面對的結果

用一句話陳述變更：*「身為管理員，我想標記裝置為 compromised，使其自動被隔離。」*

### 步驟 2 — 識別第 3 層介面

觸發來源是什麼？

| 觸發 | 第 3 層介面 |
|------|-------------|
| 使用者點擊 UI 按鈕 | 新 REST 端點 |
| 交換器送 RADIUS 請求 | 新 rlm_orw.py 程式路徑 |
| 其他服務發佈事件 | 新 NATS 訂閱者 |
| 排程／週期性 | 新 CronJob（或服務 main.py 內的 interval） |
| 外部系統呼叫 webhook | 新 webhook 路由 |

### 步驟 3 — 走過資料流

追蹤過程：
1. **何種輸入到達？**（HTTP body、RADIUS attributes、NATS 訊息）
2. **必須讀取何種狀態？**（DB 表、Redis key、LDAP）
3. **發生何種純運算？**（驗證、比對、轉換）
4. **必須寫入何種狀態？**（DB insert、NATS 發佈、Redis 更新）
5. **回傳什麼輸出？**（HTTP 回應、RADIUS 回覆、NATS 事件）

### 步驟 4 — 將每步映射到原子

每步指定執行的原子。重用既有原子（搜尋 §3.2 與 §3.3）。只在沒有合適原子時才新建。

### 步驟 5 — 決定工作單位

每個工作單位必須：
- **可獨立測試**（純原子用單元測試；副作用原子用整合測試＋假物件）
- **可獨立部署？** 對原子通常**否**（隨服務部署）；對新微服務**是**
- **大小** 原子 ≤ 1 天；端點 ≤ 1 週；新服務 ≤ 1 個月

### 步驟 6 — 排序工作

由下而上：第 1 層 → 第 2 層 → 第 3 層。每層在下一層建好前可獨立測試。

### 9.2.1 範例：「標記裝置 compromised → 自動隔離」

| # | 層 | 原子 | 狀態（新建 vs 重用） |
|---|----|------|---------------------|
| 1 | 3 | `POST /devices/{id}/compromise` 路由 | **新建** |
| 2 | 3 | `require_operator(user)` | 重用 |
| 3 | 2 | `validate_compromise_payload(body)` | **新建**（1 天） |
| 4 | 2 | `lookup_device_by_id(db, id)` | 重用 |
| 5 | 2 | `set_device_status(db, id, "compromised", reason)` | **新建** repository（1 天） |
| 6 | 2 | `log_audit("device.compromise", ...)` | 重用 |
| 7 | 2 | `serialize_event_payload(...)` | 重用 |
| 8 | 2 | `publish_device_compromised(payload)` | **新建** publisher（0.5 天） |
| 9 | 3 | （既有）policy_engine 訂閱 `orw.device.compromised` | **新訂閱**（1 天） |
| 10 | 2 | （既有）策略含條件 `status=compromised → quarantine` | **設定變更** |

新程式碼總量：4 個原子 + 1 個訂閱。預估 3.5 開發人日。**沒有修改任何既有程式碼。**

## 9.3 狀態管理

狀態存在哪裡？知道這個能避免意外耦合。

| 狀態類別 | 儲存 | 範例 | 生命週期 |
|----------|------|------|----------|
| **權威業務資料** | PostgreSQL | devices, policies, users | 永久 |
| **時序日誌** | TimescaleDB hypertable | radius_auth_log, audit_log, events | 1-2 年 |
| **快取／速率計數器** | Redis | login rate、lockout、JWT denylist | TTL |
| **傳輸中訊息** | NATS JetStream | orw.device.discovered | 直到 ack |
| **FreeRADIUS session** | FreeRADIUS 記憶體 | 進行中 EAP handshake | 每次 handshake |
| **JWT claims** | 客戶端（token） | tenant_id, role | 60 分鐘 |
| **交換器 port 狀態** | 網路設備（真相源） | VLAN 指派、ACL | 直到下次變更 |
| **本地檔案快取** | 容器暫存磁碟 | 已產生的 FreeRADIUS config | 直到 SIGHUP |

### 9.3.1 狀態規則

| 規則 | 為何 |
|------|------|
| **每類資料只有一個真相源** | 不會發生「裝置在 DB 還是 Redis 是啟用？」 |
| **讀取可快取，寫入到真相源** | 過時資料 OK；資料遺失不 OK |
| **所有變更先寫 DB，再發事件** | 事件監聽者可信賴 DB 讀取 |
| **跨服務狀態僅透過 DB 或事件分享** | 任何服務不在記憶體保留另一服務的狀態 |
| **鎖：用 PostgreSQL row lock 處理短臨界區** | 不用分散式鎖；以冪等性設計 |
| **冪等性：ON CONFLICT 或 UUID 去重** | 重試不會重複寫入 |

### 9.3.2 各服務狀態擁有權

| 服務 | 擁有（寫入） | 讀取 |
|------|--------------|------|
| gateway | 所有管理面表 | 全部 |
| device_inventory | devices, device_properties | tenants |
| policy_engine | policy_evaluations | policies, devices, device_properties |
| switch_mgmt | switch_ports | network_devices |
| coa_service | （無 — 只發事件） | radius_auth_log |
| event_service | events | （消費 NATS） |
| freeradius (rlm_orw) | radius_auth_log | mab_devices, group_vlan_mappings, ldap_servers |
| freeradius_config_watcher | freeradius_config | 所有 RADIUS-config 表 |

**跨領域：** `audit_log` — 每個服務寫入自己的動作。`tenants` — 全部讀取；只 gateway 寫入。

## 9.4 關鍵步驟驗證

某些步驟必須成功，功能才正確。依步驟類型的驗證策略：

### 9.4.1 驗證測試矩陣

| 步驟類型 | 如何驗證 | 何時 |
|----------|----------|------|
| 純原子（第 2 層） | 表格驅動單元測試 | 每次 commit CI |
| DB 寫入（第 2 層） | 對測試 DB 的整合測試 | 每次 commit CI |
| NATS 發佈（第 2/3 層） | 捕獲已發佈訊息，斷言 subject + payload | CI |
| 外部呼叫（LDAP, switch SSH, CoA UDP） | 對 staging 服務整合測試 | 部署前 |
| 跨服務流程 | 端到端測試（發事件、斷言下游影響） | 部署前 |
| RADIUS 認證流程 | radclient fixture 對 staging FreeRADIUS | 部署前 |
| UI 流程 | Playwright / Cypress 端到端 | 部署前 |

### 9.4.2 上正式前 Smoke Test

正式發版前以下**全部**必須通過：

```
1.  以 admin 登入 → 取得 token                                [API smoke]
2.  建立 device → 201 + 結構正確                              [API smoke]
3.  RADIUS PAP 認證（radclient）→ Access-Accept               [RADIUS smoke]
4.  RADIUS MAB（radclient 用 MAC username）→ Accept + VLAN    [RADIUS smoke]
5.  RADIUS PEAP-MSCHAPv2（eapol_test）→ Accept                [802.1X smoke]
6.  探索 ARP 封包 → device 出現於 DB ≤ 5 秒                   [event smoke]
7.  更新策略 → policy_engine 評估下個請求                     [config smoke]
8.  發送 CoA-Disconnect → session 於 2 秒內移除               [CoA smoke]
9.  Postgres failover → 30 秒內重連                           [HA smoke]
10. 開啟審計記錄頁 → 看到最近動作                             [UI smoke]
```

### 9.4.3 健康探針（每服務）

每服務暴露 `/health`（或對應端點），回傳：
- `db_ok` — `SELECT 1` 可執行？
- `nats_ok` — 連線存活？
- `redis_ok` — PING 回應？
- `version` — 建置時的 git SHA

供 Kubernetes liveness/readiness probe 與 Docker Compose healthcheck 使用。

## 9.5 失敗復原

失敗會發生。系統必須優雅降級。

### 9.5.1 失敗模式與緩解

| 失敗 | 緩解 |
|------|------|
| **Postgres 慢查詢（>1s）** | asyncpg statement_timeout、指數退避重試 |
| **Postgres 不可用** | 對快取資料進入唯讀模式；寫入排到 Redis（含 TTL）；告警 |
| **Redis 不可用** | 退回無速率限制（開放）或無快取（fail-deny 鎖死，安全考量） |
| **NATS 不可用** | JetStream broker 重連；發佈者帶上限退避 |
| **NATS 訊息 handler 當機** | Durable consumer 重送；達 max-deliver 後送 DLQ subject `orw.dlq.*` |
| **LDAP 不可達** | RADIUS reject 並標 reason `AD_CONNECT_FAILED`；最後一次成功的群組查詢結果快取 `cache_ttl` 秒 |
| **交換器 SSH 失敗** | 退避重試 3 次；持續失敗則於 DB 停用該交換器並發告警事件 |
| **CoA UDP 逾時** | 重試一次；記錄為失敗 CoA；不可靜默成功 |
| **Container OOM** | Docker restart=always + Kubernetes liveness probe |
| **磁碟滿（TimescaleDB）** | hypertable 壓縮政策；磁碟 80% 告警 |
| **憑證過期** | 每日檢查 + 30/7/1 天告警；UI banner |

### 9.5.2 重試安全的冪等性

可重試的操作**必須**冪等。模式：

| 操作 | 冪等機制 |
|------|----------|
| Insert device | `INSERT...ON CONFLICT (mac, tenant) DO UPDATE` |
| Insert audit log | UUID 去重表，或純 append（允許重複） |
| 發 CoA | 依 session-id 追蹤；已斷線則跳過 |
| 套用 VLAN 到 port | 先讀目前 VLAN；已正確則跳過 |
| FreeRADIUS config 寫入 | Hash 比對；無變更則跳過 |
| NATS 發佈 | 內嵌 `event_id`（UUID）；訂閱者去重 |

### 9.5.3 Dead-Letter 模式

NATS 訊息反覆處理失敗時：

```
orw.<domain>.<event>     ← 正常 subject
orw.dlq.<domain>.<event> ← N 次重試後發到此
```

維運者另外監控 `orw.dlq.>` 並決定手動重播或丟棄。

### 9.5.4 Circuit Breaker（外部呼叫）

對 LDAP、交換器 SSH、CoA UDP — 將呼叫包進 circuit breaker：

| 狀態 | 行為 |
|------|------|
| **Closed** | 正常呼叫；計算失敗 |
| **Open** | 所有呼叫快速失敗（不浪費資源）；連續 N 次失敗後進入 |
| **Half-Open** | 週期性測試呼叫；成功 → Closed；失敗 → Open |

實作：`pybreaker` 或每主機記憶體狀態。**不**跨實例共享狀態（刻意 — 本地韌性）。

### 9.5.5 Rollback 策略

若部署引入 bug：

| 嚴重度 | 動作 |
|--------|------|
| **嚴重（認證壞）** | `docker-compose down && git checkout <prev-tag> && docker-compose up -d`（≤ 5 分鐘） |
| **重大（單一功能壞）** | 在 `system_settings` 關閉 feature flag；調查；修補 |
| **次要（UI bug）** | 下個版本 forward-fix |
| **DB migration 壞** | `docker-compose down`，從快照還原 DB，重新部署舊 image |

部署前置：每次部署前執行 `pg_dump` 並保留標記備份 7 天。

---

# 第 10 部分 — 統一部署策略

**目標：環境一致性。** 同一個 Docker image 在開發者筆電、Kubernetes 叢集、隔離網段的實體機上執行。**Build 一次，到處部署。**

## 10.1 「三條路徑」概念

```
                 ┌─────────────────────────────┐
                 │ 單一容器映像                │
                 │ ghcr.io/.../orw-svc:1.2.3   │
                 └──────────────┬──────────────┘
                                │
        ┌───────────────────────┼───────────────────────┐
        ▼                       ▼                       ▼
  ┌──────────────┐       ┌──────────────┐       ┌──────────────┐
  │   Dev        │       │  Prod-Cloud  │       │  Prod-Edge   │
  │ Docker       │       │  Helm Chart  │       │  Ansible     │
  │ Compose      │       │  on K8s      │       │  on VM/實體  │
  └──────────────┘       └──────────────┘       └──────────────┘
       熱重載              HPA、Ingress、         OS 強化、
       Sidecar（PG、       K8s Secrets、          Docker engine、
       Redis、NATS）       Persistent Volume      pull 同一 image
```

| 路徑 | 目標 | 工具 | 使用情境 |
|------|------|------|----------|
| **Dev** | 開發者筆電 | `docker compose` | 快速迭代＋熱重載 |
| **Prod-Cloud** | Kubernetes 叢集 | Helm | SaaS／多租戶 production |
| **Prod-Edge** | 實體機／VM、無 K8s | Ansible | 客戶自建／隔離網段 |

## 10.2 路徑 1 — Dev (Docker Compose)

既有檔案：[docker-compose.yml](../docker-compose.yml)。

特性：
- PostgreSQL、Redis、NATS 為 sidecar 容器 — 暫存
- 服務程式碼以 volume 掛載 → **熱重載** 透過 `uvicorn --reload`
- Healthcheck 控制啟動順序
- 所有 port 暴露到 localhost 方便除錯

完整 dev 體驗建議加上：

```yaml
# docker-compose.override.yml（gitignored）
services:
  gateway:
    volumes:
      - ./services/gateway:/app/services/gateway
      - ./shared:/app/shared
    command: uvicorn services.gateway.main:app --host 0.0.0.0 --reload
    environment:
      - LOG_LEVEL=DEBUG
```

快速開始：

```bash
make setup        # 從樣板產生 .env
make dev          # 啟動 sidecar（postgres, redis, nats）
make up           # 啟動所有服務
make logs         # 追蹤日誌
make down         # 停止全部
```

## 10.3 路徑 2 — Prod-Cloud (Helm Chart)

**待建立：** `deploy/helm/orw/`

### 10.3.1 Helm Chart 結構

```
deploy/helm/orw/
├── Chart.yaml
├── values.yaml              # 預設值（每個環境覆寫）
├── templates/
│   ├── _helpers.tpl
│   ├── gateway-deployment.yaml
│   ├── gateway-service.yaml
│   ├── gateway-ingress.yaml
│   ├── gateway-hpa.yaml         # 自動擴展 2-10 pods
│   ├── policy-engine-deployment.yaml
│   ├── policy-engine-hpa.yaml
│   ├── discovery-daemonset.yaml # 每節點一個（被動監聽）
│   ├── freeradius-statefulset.yaml
│   ├── postgres-statefulset.yaml
│   ├── redis-statefulset.yaml
│   ├── nats-statefulset.yaml
│   ├── secrets.yaml             # 引用外部 secrets
│   └── networkpolicy.yaml       # 限制 pod 間流量
└── values-prod.yaml         # production 覆寫
```

### 10.3.2 主要 Helm 值

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
  # 不自動擴展 — RADIUS 認證延遲對 LB jitter 敏感
  replicas: 2
  resources:
    requests: { cpu: 500m, memory: 512Mi }

postgres:
  # 建議使用外部受管 Postgres（RDS, Cloud SQL）
  external: true
  endpoint: postgres.svc.example.com
  secretRef: orw-db-secret
```

### 10.3.3 Secrets 管理

```bash
# 使用 Sealed Secrets / External Secrets Operator
kubectl create secret generic orw-secrets \
  --from-literal=db_password=$(openssl rand -base64 24) \
  --from-literal=jwt_secret_key=$(openssl rand -hex 32) \
  --from-literal=redis_password=$(openssl rand -base64 24) \
  --dry-run=client -o yaml | kubeseal -o yaml > orw-secrets-sealed.yaml
git add orw-secrets-sealed.yaml  # 可安全 commit
```

### 10.3.4 自動擴展策略

| 元件 | 依據 | Min | Max |
|------|------|-----|-----|
| gateway | CPU 70% | 2 | 10 |
| policy_engine | NATS pending msgs | 1 | 5 |
| device_inventory | NATS pending msgs | 1 | 5 |
| switch_mgmt | NATS pending msgs | 1 | 3 |
| freeradius | RADIUS auth/sec（自訂指標） | 2 | 6 |
| event_service | NATS pending msgs | 1 | 3 |
| coa_service | （手動） | 1 | 2 |
| discovery | （DaemonSet，每節點 1 個） | — | — |

### 10.3.5 持久儲存

| 服務 | 儲存 | 大小 | 副本 |
|------|------|------|------|
| postgres | StatefulSet PVC | 100 Gi | 1（或受管服務） |
| redis | StatefulSet PVC | 10 Gi | 1 |
| nats | StatefulSet PVC | 50 Gi | 3（cluster 模式） |
| freeradius certs | ConfigMap + Secret | — | — |

## 10.4 路徑 3 — Prod-Edge (Ansible)

**待建立：** `deploy/ansible/`

適用於無法執行 Kubernetes 的場域（小辦公室、隔離網段、硬體 appliance）。

### 10.4.1 Ansible Playbook 結構

```
deploy/ansible/
├── inventory/
│   ├── production.yml      # 站點清單 + 變數
│   └── group_vars/
│       └── all.yml          # 共用變數（不含 secret）
├── playbooks/
│   ├── 01-os-hardening.yml  # 停用服務、kernel 參數、firewalld
│   ├── 02-docker-install.yml
│   ├── 03-deploy-orw.yml
│   ├── 04-configure-tls.yml
│   └── 99-uninstall.yml
├── roles/
│   ├── docker/
│   ├── orw-deploy/         # pull image、產生 compose、啟動
│   └── orw-monitoring/     # node-exporter、log shipping
├── files/
│   └── docker-compose.prod.yml
└── templates/
    ├── env.j2               # 產生 .env.production
    └── nginx.conf.j2
```

### 10.4.2 OS 強化（Role）

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

### 10.4.3 單一指令部署

```bash
# 從維運工作站
ansible-playbook -i inventory/production.yml playbooks/01-os-hardening.yml
ansible-playbook -i inventory/production.yml playbooks/02-docker-install.yml
ansible-playbook -i inventory/production.yml playbooks/03-deploy-orw.yml \
  -e orw_image_tag=1.2.3 \
  -e orw_db_password="$(pass openradiusweb/db)" \
  -e orw_jwt_secret="$(pass openradiusweb/jwt)"
```

### 10.4.4 隔離網段變體

無網際網路的場域：

1. 在連網機器：`docker save ghcr.io/.../orw:1.2.3 -o orw-1.2.3.tar`
2. 透過 USB／核可的傳輸方式搬運 .tar
3. 加入 ansible：`docker load -i {{ image_tar_path }}`
4. 同 playbook 繼續執行

## 10.5 建置流水線（CI）

單一映像建置與 tag 一次：

```
┌──────────────────────────────────────────────┐
│ git push 至 main                             │
├──────────────────────────────────────────────┤
│ CI:                                          │
│  1. 跑測試（單元 + 整合）                    │
│  2. 建置 Docker image（多階段）              │
│  3. Tag: <semver>, <git-sha>, latest         │
│  4. 推送至 ghcr.io / registry                │
│  5. 更新 Helm chart 版本（若為 release）     │
│  6. 觸發 ArgoCD sync（Prod-Cloud）           │
│  7. 通知 Ansible 維運（Prod-Edge）           │
└──────────────────────────────────────────────┘
```

## 10.6 標準目錄結構（功能導向，遞迴模組化）

**「小模組組成大模組」的落實方式：每個服務以 `features/<name>/` 自包含資料夾組成。**這是新功能與重構的標準佈局；既有扁平 `routes/` 為過渡狀態，依 §10.6.3 逐步遷移。

```
services/gateway/
├── main.py                       # app 組裝（第 3 層進入點）
├── middleware/                   # 跨功能（第 3 層）— auth、request_id 等
├── features/                     # ← 標準：每個功能一個自包含資料夾
│   ├── auth/
│   │   ├── routes.py             # 第 3 層 — REST 路由
│   │   ├── service.py            # 第 2 層 — 用例組合
│   │   ├── repository.py         # 第 2 層（只 DB 讀寫）
│   │   ├── schemas.py            # Pydantic 請求／回應 model
│   │   ├── __init__.py           # 公開 API（其他功能可 import 的）
│   │   └── tests/
│   │       ├── test_service.py
│   │       └── test_routes.py
│   ├── devices/
│   │   ├── routes.py
│   │   ├── service.py
│   │   ├── repository.py
│   │   ├── events.py             # NATS publisher/subscriber（第 2 層）
│   │   ├── schemas.py
│   │   ├── __init__.py
│   │   └── tests/
│   └── policies/
│       ├── routes.py
│       ├── service.py
│       ├── evaluator.py          # 第 2 層（純運算）
│       ├── repository.py
│       ├── schemas.py
│       ├── __init__.py
│       └── tests/
└── utils/                        # 服務本地 helper（罕見；偏好放進對應 feature/）
```

非 gateway 服務同樣採 `features/`，僅省略 `routes.py`（背景服務無 HTTP），改以 `subscribers.py` 註冊 NATS handler；`main.py` 為 composition root。

### 10.6.1 為何此結構

| 好處 | 細節 |
|------|------|
| **就近原則** | 「auth」相關全部在一個資料夾；reviewer 捲動較少 |
| **影響範圍受限** | 改 `devices` 不會意外動到 `policies` 檔案 |
| **遞迴** | 功能內的子功能可有自己的子資料夾（例：`policies/actions/vlan_assign/`） |
| **擁有權** | 一個團隊／人可端到端擁有 `features/auth/` |
| **明確公開 API** | `__init__.py` 只暴露其他功能可 import 的部分 |
| **對齊原子哲學** | 每個 `features/<name>/` 是大模組，內部檔案皆由 §3 原子組成 |

### 10.6.2 跨功能溝通

| 方法 | 何時使用 |
|------|----------|
| 從 `shared/orw_common/*` import | 跨領域原子（MAC、時間、加密） |
| import 其他功能的 `__init__.py` 公開符號 | 同服務內直接呼叫 — **罕見**；偏好事件 |
| 發佈 NATS 事件 | 跨功能溝通預設 |
| 讀其他功能的 DB 表 | 允許但不建議；偏好在自己的功能內寫 repository |
| import 其他功能的 `service.py` 內部符號 | **禁止** — 透過 `__init__.py` 公開合約 |

### 10.6.3 既有扁平 `routes/` 的遷移路徑

既有 `services/gateway/routes/<resource>.py` 為過渡狀態。**逐步**遷移，不做 big-bang 重寫：

1. **新功能必須**使用 `features/<name>/` 配置 — 不允許新增檔案到 `routes/`（由 `make lint-features` 強制；見 [scripts/check_no_new_routes.py](../scripts/check_no_new_routes.py)）。
2. 既有路由在以下時機觸發遷移：
   - 該功能有非微小修改（新端點、結構變更）
   - 該功能的 PR 涉及 ≥ 3 個檔案（routes + utils + tests）
   - 進行單元測試補強時
3. 遷移單一功能的步驟：
   - 建立 `services/<svc>/features/<name>/`
   - 把 `routes/<name>.py` 的端點搬到 `features/<name>/routes.py`
   - 拆出 `service.py`（用例組合）、`repository.py`（DB 原子）、`schemas.py`（Pydantic）
   - 在 `gateway/main.py` 改 import 路徑
   - 刪除舊的 `routes/<name>.py`（並從 [scripts/check_no_new_routes.py](../scripts/check_no_new_routes.py) 的 `LEGACY_ROUTES` 移除該項目）
4. **每個 PR 只搬一個功能**，避免 review 負擔擴散。
5. 遷移進度追蹤於 [docs/migration-features.md](migration-features.md)。

---

## 附錄 A — 文件版本歷程

| 版本 | 日期 | 變更 |
|------|------|------|
| 1.0 | 2026-04-27 | 初版整合手冊 |
| 1.1 | 2026-04-27 | 新增第 7 部分（API 規格）與第 8 部分（解耦設計）；中英雙語 |
| 1.2 | 2026-04-27 | 新增第 9 部分（開發流程）與第 10 部分（統一部署策略） |
| 1.3 | 2026-04-28 | §5.4 與 §10.6：將功能導向（`features/<name>/`）目錄結構升為標準佈局；既有扁平 `routes/` 列為過渡狀態並補上遷移觸發條件 |

本手冊取代先前獨立的分析（`roadmap.md`, `feature-breakdown.md`, `atomic-modules.md` — 已移除）。
