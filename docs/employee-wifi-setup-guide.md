# MDS 員工 WiFi 連線設定手冊

連線到公司 WiFi `MDS-01`。請務必照下面**每個欄位**設定 — 預設值
（PEAP-MSCHAPv2）連不上，是已知問題。

> **5 秒摘要**：EAP 方法選 **TTLS**、Phase 2 選 **PAP**、隱私選**使用裝置 MAC**、
> 身分填 `你的帳號@mds.local`、密碼用 AD 密碼。

---

## 必填欄位（不分手機 / 筆電）

| 欄位 | 設定值 |
|------|--------|
| SSID | `MDS-01` |
| 安全性 | WPA2-Enterprise (802.1X) |
| **EAP 方法** | **TTLS**（不是 PEAP）|
| **階段 2 驗證 / Phase 2** | **PAP**（不是 MSCHAPv2 / GTC）|
| CA 憑證 | `不要驗證` 或 `不指定`（不能留空白）|
| **隱私 / Privacy** | **使用裝置 MAC**（不是隨機 MAC）|
| **身分 / Identity** | `你的AD帳號@mds.local`（例如 `ming@mds.local`）|
| 匿名身分 | （留空）|
| 密碼 | 你的 AD 密碼 |

⚠️ **常見錯誤**：手機預設 EAP 方法是 PEAP、Phase 2 是 MSCHAPv2 — **這個組合
連不上 MDS-01**。一定要手動改成 TTLS + PAP。

---

## Android 設定步驟（POCO F5 Pro / Samsung / Pixel 等都類似）

### 第 1 步：開啟 WiFi 設定

設定 → WiFi → 從掃到的清單點 `MDS-01`

> 📷 截圖位置：[android-step1-ssid-list.png](images/wifi-setup/android-step1-ssid-list.png)

### 第 2 步：填入 EAP 設定（上半部分）

點 SSID 之後跳出設定對話框：

> 📷 截圖位置：[android-step2-eap-form.png](images/wifi-setup/android-step2-eap-form.png)
>
> 範例值：EAP 方法=TTLS、階段 2 驗證=PAP、CA 憑證=不驗證、
> 隱私=使用裝置 MAC、身分=ming@mds.local、匿名身分=空白

依序設定：

1. **EAP 方法**：點開選單 → 選 `TTLS`
2. **階段 2 驗證**：點開選單 → 選 `PAP`
3. **CA 憑證**：點開 → 選 `不要驗證`（或 `請選取` / `Do not validate`）
4. **隱私**：往下滑找到 → 點開 → 選 `使用裝置 MAC`
5. **身分**：填 `你的帳號@mds.local`（例如 `ming@mds.local`）
6. **匿名身分**：留空

### 第 2b 步：填密碼（往下滑）

> 📷 截圖位置：[android-step2b-password.png](images/wifi-setup/android-step2b-password.png)

7. **密碼**：往下滑找到 → 填你的 AD 密碼
   - 第一次設定：直接輸入
   - 修改既有 profile：欄位顯示「(未變更)」表示保留原密碼，要改就點開重打

### 第 3 步：連線

點「連線」。約 5-10 秒後應該看到 `MDS-01` 顯示「**已連線**」。

> 📷 截圖位置：[android-step3-connected.png](images/wifi-setup/android-step3-connected.png)

### Android 故障排除

| 症狀 | 解法 |
|------|------|
| 一直顯示「正在驗證」 | 帳密錯，或忘記改 TTLS+PAP — 「忘記網路」重設 |
| 「已連線（無網際網路）」 | DHCP 沒拿到 IP — 通報 IT |
| 設定按了沒反應 | 「身分」欄位空的，必填 |
| 改完設定還是用舊行為 | 一定要「忘記網路」徹底刪掉再重新加，Android profile 有 cache |

---

## iOS 設定步驟（iPhone / iPad）

iOS 沒有完整 EAP 設定 UI — 必須用 **Apple Configurator** 製作 profile，
或請 IT 派送 mobileconfig 描述檔。

### 方法 A：自動派送（推薦）

聯絡 IT，會 email 寄一份 `MDS-01.mobileconfig` 給你，點開安裝即可。

### 方法 B：手動連線（簡易但每次重連可能要重設）

1. 設定 → Wi-Fi → 點 `MDS-01`
2. 帳號（Username）：`你的帳號@mds.local`
3. 密碼：你的 AD 密碼
4. 點「加入」
5. 跳出憑證信任視窗 → 點「**信任**」

> 📷 截圖位置：[ios-step1-credentials.png](images/wifi-setup/ios-step1-credentials.png)
> 📷 截圖位置：[ios-step2-trust-cert.png](images/wifi-setup/ios-step2-trust-cert.png)

iOS 預設用 PEAP，但收到 freeradius 的 EAP-TLS handshake 後會自動 fallback
試 TTLS，所以**通常不用手動改 EAP method**。如果還是失敗請改用 mobileconfig。

---

## Windows 10 / 11 設定步驟

### 步驟

Windows 11 把所有欄位塞在「**新增網路**」一個對話框裡 — 一次填完按
「儲存」即可。

1. **設定 → 網路與網際網路 → Wi-Fi → 管理已知的網路 → 新增網路**
2. 在彈出的對話框依序填：
   - **網路名稱**：`MDS-01`
   - **安全性類型**：`WPA2-Enterprise AES`
   - **EAP 方法**：`EAP-TTLS`（不是 PEAP）
   - **驗證方法**：`未加密的密碼 (PAP)`（不是 MSCHAPv2）
   - **您的私人識別碼**：`你的帳號@mds.local`（例：`ming@mds.local`）
   - 受信任的伺服器：留空（公司 AD CS 簽的 cert，預設信任）
3. 按「儲存」→ 系統自動連線，會問密碼時填 AD 密碼

> 📷 截圖位置：[windows-add-network.png](images/wifi-setup/windows-add-network.png)
>
> Windows 11 「新增網路」對話框完整填寫範例 — 注意 EAP 方法是 `EAP-TTLS`、
> 驗證方法是 `未加密的密碼 (PAP)`、識別碼是 `ming@mds.local`

### 進階：用 GPO 派送（IT 操作）

公司有 AD GPO 的話，用 **Wireless Network (IEEE 802.11) Policies** 派送，
員工不用手動設。設定要點：
- Authentication Method: WPA2-Enterprise
- EAP type: EAP-TTLS
- Inner method: PAP

---

## macOS 設定步驟

1. **系統設定 → Wi-Fi → 點 `MDS-01`**
2. 模式：`自動`
3. 帳號名稱：`你的帳號@mds.local`
4. 密碼：你的 AD 密碼
5. 點「加入」
6. 信任伺服器憑證視窗：點「**繼續**」（首次連線會問）

> 📷 截圖位置：[macos-step1-credentials.png](images/wifi-setup/macos-step1-credentials.png)

macOS 跟 iOS 一樣會自動嘗試多種 EAP method，通常 TTLS 會被試到並成功。

---

## 為什麼要用 TTLS + PAP（給好奇的人）

> 不關心可以跳過。

| 項目 | PEAP-MSCHAPv2（手機預設）| EAP-TTLS+PAP（公司用）|
|------|------------------------|---------------------|
| 後端架構 | 需 freeradius join AD domain | freeradius LDAPS 到 AD |
| 公司現況 | ❌ 不通 | ✅ 通 |
| 安全性 | TLS 包 MSCHAPv2 hash（hash 已被 DEFCON 20 破解）| TLS 包 PAP 明文（TLS 不破，密碼也看不到）|

PAP 看起來「明文不安全」是常見誤解 — 它在 TTLS 場景下被 TLS 包覆，
攻擊者監聽到的是亂碼。實際上**比 PEAP-MSCHAPv2 更安全**。

詳細解釋見 [troubleshooting-8021x-ad.md PEAP vs TTLS+PAP 章節](troubleshooting-8021x-ad.md#peap-mschapv2-vs-eap-ttlspap為何重要)。

---

## 連不上請聯絡 IT

如果照本手冊設完還是連不上，請提供下列資訊給 IT：

1. 設備品牌型號（iPhone 14 / Galaxy S23 / Win11 ThinkPad 等）
2. WiFi 設定截圖（注意遮住密碼）
3. 連線時看到的錯誤訊息
4. 你的 AD 帳號（如 `ming@mds.local`）

IT 會用這些資訊查 freeradius log 找原因。常見原因：
- 帳密錯
- AD 帳號被鎖（連續錯誤太多次）
- 設備硬體不支援 TTLS（極罕見）
- 後端 RADIUS 服務暫停

---

## 已知裝置相容性

| 設備 / OS | TTLS+PAP 支援 | 備註 |
|-----------|--------------|------|
| Android 9+ | ✅ 原生支援 | 設定路徑可能不同 |
| iOS 14+ | ✅ 自動 fallback | 建議用 mobileconfig |
| Windows 10 22H2+ | ✅ 原生支援 | 老版本可能要 supplicant 套件 |
| Windows 7 | ⚠️ 不建議 | 已 EOL，原生不支援 TTLS |
| macOS 11+ | ✅ 自動 fallback | 同 iOS |
| Linux (NetworkManager) | ✅ | EAP=ttls, phase2=pap |
| 印表機 / IoT | ❌ 不能用 802.1X | 走 `MAB_Auth` SSID（IT 加白名單）|

---

## MAB_Auth SSID（給不打帳密的設備）

公司另有一個 `MAB_Auth` SSID，給**印表機、IP 攝影機、IoT 感測器**等不能打
帳密的設備。**員工手機/筆電請走 MDS-01，不要連這個**。

### 工作原理

`MAB_Auth` 是 **Open** SSID（無加密、無密碼），但只有 IT 預先白名單的
**MAC 位址**才能拿到 IP。沒在白名單的設備可以掃到 SSID、可以 association，
但會被 RADIUS 拒絕、上不了網。

### IT 流程：把新設備加進白名單

1. 取得設備真實 MAC（**不是隨機 MAC**）— 印在設備底部的標籤、或設備管理頁面
2. 打開 `http://<openradiusweb-server>:8888` → 側邊選單 RADIUS → **MAB Devices** → Add Device
3. 填：
   - MAC Address: 設備真實 MAC（小寫 colon 格式，例如 `3c:13:5a:cc:21:21`）
   - Name: 識別用名稱（例如 `Printer-Lobby` / `IPCam-MeetingRoom-3F`）
   - Device Type: phone / printer / iot / camera
   - Assigned VLAN: IoT 網段對應的 VLAN ID
   - Enabled: ✓
4. 設備重新掃 WiFi、連 `MAB_Auth` → 應該自動連上

### 設備端設定（給配發設備的人）

1. 設備的 WiFi → 連 `MAB_Auth` SSID
2. 不會問密碼，連線後等待拿 IP
3. **重要：手機/平板若用此 SSID，必須關閉「使用隨機 MAC」**，改用「使用裝置 MAC」
   — 因為 IT 白名單的是真實 MAC，隨機 MAC 連不上

### 連上後的截圖（範例）

> 📷 截圖位置：[android-mab-connected.png](images/wifi-setup/android-mab-connected.png)
>
> 連上 MAB_Auth 後 Android 顯示：技術標準=第 4 代（WiFi 4）、安全性=無、
> IP 設定=DHCP、隱私=使用裝置 MAC

### 連不上常見原因

| 症狀 | 原因 / 解法 |
|------|------------|
| 設備掃不到 `MAB_Auth` SSID | AP 沒廣播此 SSID — 通報 IT |
| 看到 SSID 但連不上 / 拿不到 IP | MAC 沒在白名單，或設備用了隨機 MAC — 通報 IT 確認 |
| 連上但無網路 | VLAN 設定錯（`assigned_vlan` 不是有 DHCP 的 VLAN）— 通報 IT |

---

## 文件版本

- v1.1 — 2026-05-03 加入 MAB_Auth section + 部分 Android 截圖
- v1.0 — 2026-05-02 初版
- 後續更新請至 [openradiusweb repo docs/](https://github.com/acronhuang/openradiusweb/tree/main/docs)
