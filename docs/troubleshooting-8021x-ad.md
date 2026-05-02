# 802.1X + Active Directory 故障排除指南

本文檔記錄 OpenRadiusWeb 整合 FreeRADIUS + Active Directory + 802.1X 客戶端
（手機、筆電、AP/Switch）時，實際遇到的問題與解法。內容來自 2026-05-02
ming@mds.local 從零到通的完整 debug session。

如果你正在 debug WiFi/有線 802.1X 認證失敗，從 [快速分流](#快速分流) 開始。

---

## 目錄

1. [快速分流](#快速分流)
2. [架構與資料流](#架構與資料流)
3. [問題索引](#問題索引)
4. [問題詳解](#問題詳解)
5. [常用 debug 指令](#常用-debug-指令)
6. [手機端 802.1X 設定](#手機端-8021x-設定)
7. [PEAP-MSCHAPv2 vs EAP-TTLS+PAP（為何重要）](#peap-mschapv2-vs-eap-ttlspap為何重要)

---

## 快速分流

先看 freeradius log，根據關鍵字跳到對應章節：

```bash
sudo docker logs --tail=100 -f orw-freeradius
```

| Log 關鍵字 | 大概率原因 | 跳到 |
|-----------|----------|------|
| `Ignoring request from unknown client` | NAS 沒登錄 / clients.conf 路徑錯 | [#1](#1-ignoring-request-from-unknown-client) |
| `tls_max_version = "1.3"` warning | 802.1X 客戶端不支援 TLS 1.3 | [#2](#2-tls_max_version--13-警告) |
| `Strong(er) authentication required` | AD 強制 LDAPS，FreeRADIUS 卻用明文 LDAP | [#3](#3-strong-authentication-required) |
| `CERTIFICATE_VERIFY_FAILED` | LDAP 沒信任 AD CA | [#4](#4-certificate_verify_failed-ldaps) |
| `error:05800074:x509 ... key values mismatch` | server.pem 跟 server.key 不是一對 | [#5](#5-eap-cert-mismatch) |
| `No Auth-Type found: rejecting` | LDAP 找到使用者但沒設 Auth-Type | [#6](#6-no-auth-type-found-rejecting) |
| `mschap: FAILED: No NT-Password` | 手機用 PEAP-MSCHAPv2 但 AD 沒給 NT-Hash | [#7](#7-mschap-failed-no-nt-password) |
| `Search returned no results` | LDAP search base 不對 | [#8](#8-ldap-search-returned-no-results) |
| Filter `{username}` 沒展開（log 出現原文 `{username}`） | filter 用了非 freeradius 語法 | [#9](#9-ldap-filter-沒展開) |
| 設定改了但行為沒變 | 手機 cache 住舊 EAP profile | [#10](#10-手機-cache-住舊-profile) |

---

## 架構與資料流

```
[Phone]
  │  EAPOL（802.1X over WiFi/Ethernet）
  ▼
[FortiGate 60C / Switch / AP]   ← NAS / Authenticator
  │  RADIUS (port 1812 + shared secret)
  ▼
[orw-freeradius container]
  │  ① clients.conf 認 NAS
  │  ② EAP outer：PEAP / TTLS / TLS（伺服器憑證 server.pem/key）
  │  ③ EAP inner：MSCHAPv2 / PAP / GTC
  │  ④ 後端驗證：rlm_ldap → LDAPS bind 到 AD
  ▼
[Active Directory DC] (LDAPS 636)
  │  使用者搜尋 + bind 驗證
  ▼
回傳 Access-Accept / Reject 給 FortiGate
  │
回傳 EAP-Success / Failure 給手機
```

每一層任一環節錯，整條鏈就斷。下面的問題就是從外到內、從底到頂依序破解。

---

## 問題索引

按照從外到內的順序：

| # | 問題 | 影響層 |
|---|------|--------|
| 1 | NAS 連不到 freeradius | 網路 / clients.conf |
| 2 | TLS 版本不相容 | EAP outer |
| 3 | LDAPS 強制 | LDAP 連線 |
| 4 | LDAP 憑證驗證失敗 | LDAP 連線 |
| 5 | EAP 伺服器憑證不匹配 | EAP outer |
| 6 | freeradius 不知道用哪個 Auth-Type | freeradius 設定 |
| 7 | PEAP-MSCHAPv2 + AD 架構不相容 | EAP inner |
| 8 | LDAP 找不到使用者 | LDAP 查詢 |
| 9 | LDAP filter 語法錯 | LDAP 查詢 |
| 10 | 客戶端 cache | 手機端 |

---

## 問題詳解

### 1. `Ignoring request from unknown client`

**症狀**

```
Sat May 2 10:15:23 : Auth: Ignoring request from unknown client 192.168.0.100 port 1812
```

**根因**

FreeRADIUS 容器內部讀的設定路徑跟 entrypoint 寫入的路徑不一致。
Debian 套件版的 freeradius 讀 `/etc/freeradius/3.0/clients.conf`，但
舊版 entrypoint 把 symlink 建在 `/etc/freeradius/clients.conf`。

**修法**

容器內：

```bash
sudo docker exec orw-freeradius ls -la /etc/freeradius/3.0/clients.conf
# 如果指向 stock Debian 預設（只有 localhost），就是路徑問題
```

修 `services/auth/freeradius_entrypoint.sh`，把路徑統一到 `/etc/freeradius/3.0/`
（PR #56 已合）。

如果是 NAS 真的沒登錄：到 OpenRadiusWeb UI → NAS Clients → 新增該 IP 與
shared secret，存檔後 watcher 會 trigger HUP。

**驗證**

```bash
sudo docker exec orw-freeradius cat /etc/freeradius/3.0/clients.conf | grep -A2 "client "
```

應該看到你的 NAS IP。

---

### 2. `tls_max_version = "1.3"` 警告

**症狀**

freeradius 啟動 log：

```
WARNING: TLS 1.3 is enabled. Most 802.1X supplicants do not support it.
```

**根因**

舊版 FreeRADIUS 預設啟用 TLS 1.3。但是 Android 14 以下、iOS 16 以下、
Windows 10 內建 supplicant 對 EAP-TLS 1.3 支援度差。

**修法**

OpenRadiusWeb UI → System Settings → RADIUS tab → TLS Max Version 設 `1.2`
（PR #57 加的可編輯 UI）。如果是 CLI：

```sql
UPDATE system_settings SET setting_value='1.2'
WHERE category='radius' AND setting_key='tls_max_version';
```

然後重啟 freeradius：`sudo docker restart orw-freeradius`。

**何時可以開 1.3**：你環境內所有 802.1X 客戶端都是 Win11 22H2+ / iOS 17+ /
Android 15+ 才考慮。

---

### 3. `Strong (authentication) required`

**症狀**

freeradius log：

```
rlm_ldap: ldap_bind: Strong(er) authentication required
```

**根因**

AD 預設策略要求 LDAPS（port 636 + TLS）或 LDAP+StartTLS，
不接受 plain LDAP（port 389）的 bind。

**修法**

```sql
UPDATE ldap_servers SET
  use_tls = true,
  port = 636,
  tls_require_cert = 'never'
WHERE server_name = 'mds.local';
```

`tls_require_cert='never'` 是先讓認證跑起來；正式環境應該匯入 AD 的根 CA 並
改 `'demand'`。

---

### 4. `CERTIFICATE_VERIFY_FAILED` (LDAPS)

**症狀**

```
rlm_ldap: CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate
```

**根因**

LDAPS 連線時，FreeRADIUS 不信任 AD DC 的伺服器憑證。

**修法（2 選 1）**

A. **暫解** — `tls_require_cert='never'`（如 [#3](#3-strong-authentication-required)）。
   只跳過驗證，連線仍是 TLS 加密。適合 lab / 內網。

B. **正解** — 把 AD CA 匯入 FreeRADIUS 容器：

```bash
# 從 AD 匯出 root CA（PEM 格式）
# 放到 services/auth/freeradius/certs/trusted-cas/mds-rootca.pem
# 然後 commit + 重 deploy
```

設定 `tls_require_cert='demand'` 並指向 CA 檔。

---

### 5. EAP cert mismatch

**症狀**

freeradius 啟動就 fail：

```
error:05800074:x509 certificate routines::key values mismatch
```

**根因**

`server.pem` 跟 `server.key` 不是同一對 keypair。常見於：手動換了其中一個檔
但忘了換另一個；或 cert manager 中途出錯產生不一致的檔。

**修法**

驗證是否成對：

```bash
sudo docker exec orw-freeradius bash -c '
  openssl x509 -noout -modulus -in /etc/freeradius/certs/server.pem | openssl md5
  openssl rsa -noout -modulus -in /etc/freeradius/certs/server.key | openssl md5
'
```

兩個 md5 應該一樣。不一樣就重新匯入正確的 keypair（從 AD CS 申請）：

1. 在 AD CS 重新簽發伺服器憑證（CN = freeradius FQDN，含 SAN）。
2. 匯出 `.cer` + `.key`（PEM 格式）。
3. UI → Certificates → 上傳，或直接 SCP 進容器 `/etc/freeradius/certs/`。
4. `sudo docker restart orw-freeradius`。

---

### 6. `No Auth-Type found: rejecting`

**症狀**

```
(5) WARNING: No Auth-Type found: rejecting the user via Post-Auth-Type = Reject
```

**根因**

`rlm_ldap` 在 `authorize` section 找到使用者後，必須**明確**告訴 freeradius
「下一步用我（ldap）來 bind 驗證密碼」。如果沒有設 `Auth-Type`，
freeradius 找不到驗證模組，就 reject。

**修法**

`services/auth/freeradius/templates/site_inner_tunnel.j2` 跟 `site_default.j2`
裡，在 LDAP module call 後加：

```jinja
{% for ldap_mod in ldap_modules %}
        {{ ldap_mod.module_name }}

        if ((ok || updated) && User-Password) {
            update control {
                &Auth-Type := {{ ldap_mod.module_name }}
            }
        }
{% endfor %}
```

PR #56 已合。重生成設定：

```bash
sudo docker exec orw-freeradius python3 /opt/orw/freeradius_config_manager.py --generate-and-apply
sudo docker restart orw-freeradius
```

---

### 7. `mschap: FAILED: No NT-Password`

**症狀**

```
Auth: Login incorrect (mschap: FAILED: No NT-Password.  Cannot perform
authentication): [ming@mds.local/<via Auth-Type = EAP>]
(from client X port 0 cli AA-BB-CC-DD-EE-FF via TLS tunnel)
```

注意 `via TLS tunnel` —— 表示是在 PEAP 內層發生的。

**根因**

PEAP-MSCHAPv2 需要 freeradius 拿到使用者的 **NT-Hash** 才能算
challenge/response。NT-Hash 不能透過普通 LDAP 取得（AD 不會吐
`unicodePwd` 給 LDAP query），只能透過：
- Samba/winbind + `ntlm_auth` 橋接到 AD（需要把 freeradius 容器加入 AD domain）
- 或從本地 SQL/檔案讀 NT-Hash（不適用 AD）

**修法（兩條路）**

A. **快路 — 改用 EAP-TTLS+PAP**（推薦，PRD 簡單）

   伺服器端不用改任何設定（templates 已經支援）。手機端把 EAP method
   從 `PEAP` 改成 `TTLS`，Phase 2 從 `MSCHAPV2` 改成 `PAP`。

   PAP 模式下，密碼會在 TLS tunnel 內直接送給 freeradius，
   freeradius 拿明文密碼去 LDAPS bind 到 AD —— 完全不需要 NT-Hash。

   見 [手機端 802.1X 設定](#手機端-8021x-設定)。

B. **慢路 — 在 freeradius 容器設定 ntlm_auth**

   把 freeradius 容器 join 到 AD domain（Samba + winbind），
   設定 mschap module 用 `ntlm_auth` 去問 AD。
   工程量大，目前 OpenRadiusWeb 沒原生支援。

**為何選 A**：見 [PEAP-MSCHAPv2 vs EAP-TTLS+PAP](#peap-mschapv2-vs-eap-ttlspap為何重要)。

---

### 8. LDAP `Search returned no results`

**症狀**

```
rlm_ldap (mds.local): Search returned no results
```

**根因**

`user_search_base` 不對。例如 ming 在 `OU=IT,OU=MDS,DC=mds,DC=local`，但
search base 設成 `CN=Users,DC=mds,DC=local`（AD 預設 container），
就找不到。

**修法**

```sql
UPDATE ldap_servers SET user_search_base = 'DC=mds,DC=local'
WHERE server_name = 'mds.local';
```

從 domain root 開始搜，覆蓋所有 OU。如果擔心效能，搜尋特定 OU：
`OU=IT,OU=MDS,DC=mds,DC=local`。

驗證：

```bash
sudo docker exec orw-freeradius ldapsearch -H ldaps://172.16.x.x:636 \
  -D "CN=svcRadius,CN=Users,DC=mds,DC=local" -w 'BindPwd' \
  -b "DC=mds,DC=local" "(sAMAccountName=ming)" dn
```

應該回傳 ming 的 DN。

---

### 9. LDAP filter 沒展開

**症狀**

freeradius log 出現原文 `{username}` 沒被替換：

```
rlm_ldap: filter = (sAMAccountName={username})
```

**根因**

filter 語法用錯。`{username}` 是某些工具（例如 ClearPass）的語法，
**freeradius 用 unlang 變數展開語法**：

```
%{User-Name}                                   # 完整 User-Name（含 @realm）
%{Stripped-User-Name}                          # 去掉 realm 後
%{%{Stripped-User-Name}:-%{User-Name}}         # 優先用 Stripped，沒有就用 User-Name
```

**修法**

```sql
UPDATE ldap_servers SET
  user_search_filter = '(sAMAccountName=%{%{Stripped-User-Name}:-%{User-Name}})'
WHERE server_name = 'mds.local';
```

這個 filter 對 `ming` 跟 `ming@mds.local` 兩種 User-Name 都 work。

---

### 10. 手機 cache 住舊 profile

**症狀**

伺服器端設定改完、freeradius 重啟、log 顯示新行為，但手機連線結果跟改之前
一模一樣（同樣的錯誤訊息、同樣的 EAP method）。

**根因**

Android / iOS 對 WiFi profile 有強烈 cache。改 EAP method、密碼、CA 設定
後，**舊的 profile 還在背景活著**，下次連線可能用舊 profile 也可能用新的，
取決於系統。

**修法**

每次改 802.1X 設定後一定要：

1. WiFi 設定 → 長按 SSID → 「忘記網路」/ Forget Network
2. 完全關掉 WiFi（飛航模式 ON 也行）
3. 等 5 秒
4. 重新開 WiFi、重新加入網路、重設所有欄位

---

## 常用 debug 指令

### Tail freeradius log

```bash
# 看新進來的請求
sudo docker logs --tail=0 -f orw-freeradius

# 看歷史 + tail
sudo docker logs --tail=200 -f orw-freeradius

# 只看 Auth 結果
sudo docker logs --tail=200 orw-freeradius | grep "Auth:"
```

### 開 freeradius debug 模式（最詳細）

```bash
sudo docker exec orw-freeradius radiusd -X
```

注意：這會把容器內的 freeradius 多開一個 instance（port 衝突會失敗）。
需要先停容器內的 service。

更好的做法是改 docker-compose 暫時把 entrypoint 換成 `radiusd -X`，
debug 完改回來。

### 手動測試 LDAP bind

```bash
sudo docker exec orw-freeradius ldapsearch -H ldaps://AD-IP:636 \
  -D "CN=BindUser,CN=Users,DC=mds,DC=local" -w 'BindPwd' \
  -b "DC=mds,DC=local" "(sAMAccountName=USERNAME)" dn
```

### 手動測試 RADIUS（從 freeradius 容器發）

```bash
sudo docker exec orw-freeradius radtest \
  -t mschap USERNAME PASSWORD localhost 0 testing123
```

### 重生成 freeradius 設定

```bash
sudo docker exec orw-freeradius python3 \
  /opt/orw/freeradius_config_manager.py --generate-and-apply
sudo docker restart orw-freeradius
```

---

## 手機端 802.1X 設定

**Android（推薦設定）**

| 欄位 | 值 |
|------|---|
| EAP 方法 | `TTLS` |
| 階段 2 驗證 / Phase 2 | `PAP` |
| CA 憑證 | `不要驗證` 或選擇匯入的 MDS Root CA |
| 線上憑證狀態 | `不要驗證` |
| 網域 | （留空，除非設了 EAP-TLS） |
| 身份 | `ming@mds.local`（含 realm） |
| 匿名身份 | 留空 |
| 密碼 | AD 密碼 |

**iOS**

iOS 沒有獨立 EAP 設定，需要用 **Apple Configurator** 製作 mobileconfig
profile，內含 `EAPClientConfiguration` block 指定 EAP type。

**Windows**

WLAN profile XML 內 `<authMode>` 跟 `<EAPMethod>` 設定。
推薦透過 GPO 派送，不要靠使用者手動設。

---

## PEAP-MSCHAPv2 vs EAP-TTLS+PAP（為何重要）

兩者都建立 TLS tunnel 包住內層認證，差別在內層協定：

| 項目 | PEAP-MSCHAPv2 | EAP-TTLS+PAP |
|------|---------------|--------------|
| 內層協定 | MSCHAPv2 (challenge/response) | PAP (明文，但在 TLS 內) |
| 伺服器需要 | NT-Hash | 明文密碼 |
| 對 AD 的整合 | 需要 ntlm_auth + Samba/winbind | 直接 LDAPS bind |
| 設定複雜度 | 高（freeradius 容器要 join AD） | 低（只要 LDAPS） |
| 安全性 | 內層雜湊，但 MSCHAPv2 已被破解 | 內層明文，但被 TLS 包住 |
| 客戶端支援 | Windows 內建支援度最佳 | Android/Linux 支援度最佳 |

**為何 OpenRadiusWeb 預設走 EAP-TTLS+PAP**

OpenRadiusWeb 用 `rlm_ldap` 對 AD 做 LDAPS bind 驗證。LDAPS bind 拿到的是
**明文密碼經 TLS 加密送過去由 AD 比對**，回傳 success/fail —— freeradius
全程不持有明文密碼，也不需要 NT-Hash。這跟 EAP-TTLS+PAP 的模型 1:1 對應，
所以 work。

PEAP-MSCHAPv2 則需要 freeradius 自己算 challenge/response，必須持有
NT-Hash —— 這條路 OpenRadiusWeb 目前沒走。

**結論**：在 OpenRadiusWeb 的部署上，**手機/筆電 802.1X profile 必須選
EAP-TTLS + PAP**，不能用 PEAP-MSCHAPv2。

---

## 設定一個純 MAB SSID（給不能打帳密的設備）

**何時需要這個**：印表機、IP 攝影機、IoT 感測器等不能輸入 802.1X
帳密的設備。802.1X 本身要求 supplicant 主動跑 EAP，這些設備做不到 →
要走 MAB（MAC Authentication Bypass）。

### 重要前提

**WPA2/3-Enterprise SSID 不能做純 MAB**。Enterprise 模式強制 supplicant
跑 EAP，AP 不會 fallback 到 MAC-only 路徑。要純 MAB 必須**另開一個 Open
或 WPA2-Personal 的 SSID**。

| SSID 加密 | 純 MAB 可行？ | 說明 |
|----------|--------------|------|
| Open + radius-mac-auth | ✅ | 設備一 association 就送 MAC 給 RADIUS |
| WPA2-Personal + radius-mac-auth | ✅ | 多一層 PSK 門檻，過 PSK 後 MAC 驗證 |
| WPA2-Enterprise + radius-mac-auth | ❌ | 變成「802.1X 之前先檢查 MAC」雙重驗證，仍要打帳密 |

如果你的場景是「802.1X 通過後依設備 MAC 分到不同 VLAN」（同一個 SSID
不同設備進不同網段），那不是 MAB —— 用 PR #59 的 per-MAC VLAN override
（`mab_devices.assigned_vlan_id` 在 802.1X post-auth 階段套用）。

### FortiWiFi 60C 端設定（FortiOS 5.2.x，在 192.168.0.x 部署實測通過）

#### 1. 建 VAP（SSID）

```
config wireless-controller vap
    edit "MAB_Auth"
        set ssid "MAB_Auth"
        set security open
        set radius-mac-auth enable
        set radius-mac-auth-server "Radius"
    next
end
```

關鍵：
- `security open` —— 不要 `wpa2-only-enterprise`，那會強制 802.1X
- `radius-mac-auth enable` —— AP 把連線設備的 MAC 當 User-Name 送 RADIUS
- `radius-mac-auth-server "Radius"` —— 指向你的 RADIUS server entry

⚠️ **不要用** `mac-auth-bypass`（那是有線 switch port 的指令，
WiFi VAP 沒有這個欄位，FortiOS CLI 會直接 parse error）。WiFi MAB 的關鍵字
就是 `radius-mac-auth`。

#### 2. 把 SSID 綁到 radio（讓它廣播）

先看你 wtp-profile 名字：

```
config wireless-controller wtp-profile
    show
end
```

找到 profile 後加上 MAB_Auth：

```
config wireless-controller wtp-profile
    edit "<your-profile-name>"
        config radio-1
            set vaps "Radius" "MAB_Auth"
        end
        config radio-2
            set vaps "Radius" "MAB_Auth"
        end
    next
end
```

#### 3. system interface（給 IP）

```
config system interface
    edit "MAB_Auth"
        set vdom "root"
        set type vap-switch
        set ip 192.168.99.1 255.255.255.0
        set allowaccess ping
        set role lan
    next
end
```

#### 4. DHCP server

```
config system dhcp server
    edit 0
        set interface "MAB_Auth"
        set default-gateway 192.168.99.1
        set netmask 255.255.255.0
        set dns-service default
        config ip-range
            edit 1
                set start-ip 192.168.99.100
                set end-ip 192.168.99.200
            next
        end
    next
end
```

#### 5. Firewall policy（MAB_Auth → WAN）

```
config firewall policy
    edit 0
        set name "MAB_Auth_to_WAN"
        set srcintf "MAB_Auth"
        set dstintf "wan1"
        set srcaddr "all"
        set dstaddr "all"
        set action accept
        set schedule "always"
        set service "ALL"
        set nat enable
    next
end
```

把 `wan1` 換成你實際 WAN 介面名稱（`show system interface | grep -i wan`）。

### OpenRadiusWeb 端：把設備 MAC 加進白名單

打開 `http://<server>:8888` → 側邊選單 RADIUS → **MAB Devices** → Add Device

| 欄位 | 範例 |
|------|------|
| MAC Address | `3c:13:5a:cc:21:21`（設備真實 MAC，**不是隨機 MAC**）|
| Name | POCO F5 Pro |
| Device Type | phone / printer / iot |
| Assigned VLAN | 留空，或填 IoT VLAN ID |
| Enabled | ✓ |

### 測試 + 預期 log

設備連 MAB_Auth SSID（**不需要打密碼**），server 端 tail：

```bash
sudo docker logs --tail=0 -f orw-freeradius
```

成功時：

```
OpenRadiusWeb MAB request: 3c:13:5a:cc:21:21
OpenRadiusWeb MAB approved: 3c:13:5a:cc:21:21 (POCO F5 Pro) -> VLAN 15
Auth: Login OK: [3C-13-5A-CC-21-21/3C-13-5A-CC-21-21]
```

### 兩個高機率踩雷點

#### 雷 1：手機隨機 MAC

Android / iOS 對每個 SSID 預設用「隨機 MAC」（隨機 MAC 第一個 byte
通常是 02/06/0A/0E 結尾的 6 種，例如 `0e:9a:05:d2:bb:b2`）。隨機 MAC
**重設可能換**，下次連同一個 SSID 用的 MAC 跟你白名單的對不上 →
`MAB not in whitelist` reject。

修法：手機 WiFi → 該 SSID → **隱私 → 改「使用裝置 MAC」**，然後在
mab_devices 用真實 MAC（通常 `3c:`/`40:`/`fc:` 等開頭非隨機）。

#### 雷 2：MAC 格式不一致

freeradius 收到的 MAC 可能是 `3c-13-5a-cc-21-21`（dash）或
`3c:13:5a:cc:21:21`（colon）或 `3c135acc2121`（無分隔符）。
orw 模組的 `_normalize_mac()` 會統一成 colon-lowercase 格式存進
mab_devices，但**手動在 UI 輸入時要用 colon-lowercase**，不要用
大寫或 dash。

實際從 RADIUS log 看當前 MAC 格式：
```bash
sudo docker logs --tail=20 orw-freeradius | grep "MAB request"
```

---

## 已知未解問題

### `Failed to find attribute config:OpenRadiusWeb-Realm`

```
Error: authorize - Failed to find attribute config:OpenRadiusWeb-Realm
```

每個請求都會印一次。`orw` rlm_python 模組嘗試讀的 attribute 沒有被
freeradius dict 註冊，但不影響認證流程，只是洗 log。

待修：在 `services/auth/freeradius/dictionary.openradiusweb` 註冊
`OpenRadiusWeb-Realm` attribute，或修 rlm_python 程式不要找這個 attribute。

---

## 附錄：本文檔來源

2026-05-02 ming@mds.local debug session。
從 `Ignoring request from unknown client` 開始，到 `Login OK` 為止，
共 10 個獨立問題、4+ 個小時、4 個 PR（#56 #57 #58 #59）、若干 SQL hotfix，
最後加上純 MAB SSID 從零做到通（FortiWiFi 60C + OpenRadiusWeb mab_devices
表 + POCO F5 Pro 真實 MAC `3c:13:5a:cc:21:21` → VLAN 15）。
