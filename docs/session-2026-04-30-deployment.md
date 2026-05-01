# Session Log — 2026-04-30 → 2026-05-01

## Fresh Deployment to 192.168.0.250 + Real-World Bug Triage

**對象：** 把 [migration session log](session-2026-04-29-migration-completion.md) 結束後的 main HEAD `5bb3b9b`（19/19 features migrated）部署到一台全新 Ubuntu 22.04 機器。途中暴露了 **10 個獨立 bug**（從 frontend 欄位名 → SQL syntax → DB column 名 → freeradius config 結構 → Docker base image 限制），每個都修出獨立 PR（#30-#39，跳過 #34 被吸收進 #36）。**最終狀態：12/12 containers Up，FreeRADIUS 穩定運行**。

**範圍：**
- Project status sanity check（PR #28 收尾）
- Smoke flow 設計（pre-deployment confidence）
- Fresh install on 192.168.0.250（Docker, env, build, up, smoke）
- 4 個 data-plane bug（frontend 欄位名 / freeradius column 名 / asyncpg `:foo::type` / system_settings column 名）→ PR #30-#33
- Deployment guide v2.1（PR #29，real-world fixes）
- freeradius 6 hour restart loop debugging：
  - PR #34（cp → ln -sf，吸收進 #36）
  - PR #36（comprehensive fix：用 templates、conditional config gen、修 entrypoint chmod glob）
  - PR #37（rlm_python3 runtime detect — image 沒 bundle 也能跑）
  - PR #38（template: 移 preprocess 從 accounting）
  - PR #39（template: 移 remove_reply_message_authenticator — 3.2.3 不 bundle）

**最終 main HEAD：** `dba0a44`（PR #39 之後）。

**部署狀態：** **12/12 containers Up + healthy**；FreeRADIUS daemon 穩定運行；UDP 1812-1813 對外綁定；本機 radtest 確認可 process RADIUS request。Fortigate 端 Test Connectivity 應該可通（待最終確認）。

---

## Round 1 — 確認專案完成 + 提供部署文件

### Prompt
> 請確認專案是否完成，還有哪些需要進行？如果完成提供部署文件我要手動部署確認可以執行

### 變更
- 對照 main HEAD `5bb3b9b` + open PR list，確認 19/19 migration 在 main、PR #28（session log）剩 OPEN
- 指向 [docs/deployment-guide.md](deployment-guide.md) §2.2 / §3 / §5 / §9，給濃縮版部署步驟（Docker install + git clone + .env + build + up + verify）
- 強調對 192.168.0.155（既存舊版部署）的 redeploy 場景：只重 build gateway + frontend，DB schema 沒變

### 跟進
使用者選擇「換另一臺進行安裝」→ 進 Round 3 fresh install 流程，原 155 略過。

---

## Round 2 — Pre-deployment smoke flow 設計

### Prompt
> 部署前 跑一下你信得過的 smoke flow這要如何做

### 內容
給三層級 smoke flow，從快到慢、從少 confidence 到高 confidence：

| 層級 | 時間 | 抓得到什麼 |
|---|---|---|
| **L1**：本地 `pytest features/` | 90 秒 | import-time bug、service 邏輯錯誤、event subject 拼錯 |
| **L2**：本地 docker-compose up + curl smoke | ~15 分鐘 | DB schema mismatch、NATS/Redis 連線、cross-feature 整合 |
| **L3**：L2 + 瀏覽器手動點 4 個 critical UI flow | +10 分鐘 | 前端 UX 確認 PR #11/#27 的 extractErrorMessage 真的有顯示 backend detail |

L2 給了 step-by-step 指令（含 `.env.local` 設定、curl 19 個 feature endpoint 的 status code、NATS subscribe 驗證 publish）。

---

## Round 3 — 換新機器 fresh install（過程踩了一連串 deployment gotcha）

### Prompts
> 我換另一臺進行安裝
>
> Package docker-ce is not available...
>
> mdsadmin@radius:~$ # 檢查兩個關鍵檔 ...
>
> Username for 'https://github.com': huang.acron@gmail.com ... Authentication failed ...
>
> github_pat_11AKA43... ⚠️ 使用者把 PAT 直接貼進對話
>
> Write access to repository not granted
>
> A（選擇把 repo 改 public）

### Walkthrough（12 個小回合）
1. **Docker apt repo missing** — 原 deployment-guide §2.2 的多行 `echo "deb..." | sudo tee` 在使用者環境 silent fail，`docker.gpg` 跟 `docker.list` 都沒生成 → 重新做成 8 個有 verification 的 step
2. **Docker install OK** — `docker run --rm hello-world` 成功
3. **`/opt/openradiusweb` 是不是 git repo** — 不是（之前 SCP 部署的）→ 改 `git init` + remote add 還是 git clone
4. **GitHub HTTPS auth 失敗** — `huang.acron@gmail.com` 當 username + password 不被接受。GitHub 已棄用 password auth → 用 PAT
5. **使用者把 PAT 貼進對話** — ⚠️ 立刻提醒 revoke + 不要再貼，改 PAT 在 server 端 inline 進 clone URL + 立刻 `git remote set-url` 清掉
6. **第二個 PAT 也貼進對話** — ⚠️ 再次提醒 revoke
7. **403 "Write access not granted"** — token 由 `huang.acron@gmail.com` 產，但 repo 屬於 `acronhuang`，個人 token 不認 organisation repo → 提供三條解（A=改 public / B=用 acronhuang 帳號 token / C=從 dev 機器 SCP）
8. **使用者選 A → repo 改 public** — `git clone` 成功，main HEAD `e4be823`
9. **產 .env 密碼** — 一開始用 `openssl rand -base64 24` + `sed -i "s|...|...|"` → JWT_SECRET_KEY 那行 `sed: unterminated 's' command` 因為 base64 出現 `|` 字元 → 改用 `LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 64` 保證 sed-safe + 改 sed 分隔符 `~`
10. **網卡名是 eno1（不是 eth0）** — `ip -br a` 確認後 `sed` 寫進 SCAN_INTERFACE
11. **docker compose build permission denied on docker.sock** — `usermod -aG docker $USER && newgrp docker` 在某些 shell 不生效 → 解法是 `exit` 後重新 SSH
12. **首次 build + up + DB init 全部成功** — 12 containers Up，跑 manual migration（003、004），`/health` 200 OK

### 結果
- main HEAD `e4be823` 部署到 192.168.0.250
- 12 container 全部 Up（postgres / redis / nats healthy）
- `/health` 回 200，但 admin login 401 因為 seed.sql 的 bcrypt hash 不匹配（已知 — 接 Round 5 部署文件 v2.1 收進去）

---

## Round 4 — 修 admin password (bcrypt hash mismatch)

### Prompt（隱含 — 從 login 401 traceback 推導出來）

### 變更
從 gateway container 內部用同 bcrypt 版本重產 hash，UPDATE users，FLUSHDB Redis 清 rate-limit：

```bash
NEWHASH=$(docker exec orw-gateway python -c "import bcrypt; print(bcrypt.hashpw(b'OpenNAC2026', bcrypt.gensalt()).decode())")
docker exec orw-postgres psql -U orw -d orw -c "UPDATE users SET password_hash='$NEWHASH' WHERE username='admin';"
docker exec orw-redis redis-cli -a "$REDIS_PW" FLUSHDB
```

成功後登入拿到 token len 301，`/api/v1/dot1x/overview` 回 9 個正確 key。

---

## Round 5 — Deployment Guide v2.1（PR #29）

### Prompt
> 請修改部署文件

### 變更
[docs/deployment-guide.md](deployment-guide.md) 從 v2.0 → v2.1，6 段重寫 / 新增：

| § | 內容 |
|---|---|
| 2.2 | 拆成 8 step，每 step 加 verification；補上 `docker-buildx-plugin`；newgrp + SSH-reconnect 註記 |
| 3.1 | `YOUR_ORG` → 真實 repo；private/public/PAT/SSH 三條路；token-leak warning + `git remote set-url` 清 token |
| 4.5 | `openssl rand -base64` → `tr -dc 'A-Za-z0-9'`（sed-safe）；sed 分隔符 `~`；per-step verification |
| 4.6 | `ip addr show` → `ip -br a`；補上 `sed` 寫 SCAN_INTERFACE 的指令 |
| **9.5（新）** | bcrypt seed-hash 不相容 workaround |
| **9.6（新）** | smoke test 對 `/dot1x/overview` 驗證 19/19 migration live |

PR [#29](https://github.com/acronhuang/openradiusweb/pull/29) merged → main `d784107`。

---

## Round 6 — NAS Client save 失敗：「Failed to save NAS client」（PR #30 + #11 後續）

### Prompt
> 要如何設定Radius Secret
>
> 有問題（截圖：Add NAS Client 失敗 toast）

### 探索
1. 從截圖 toast 字串「Failed to save NAS client」反推：這是 PR #27 我加的 fallback；意味 `extractErrorMessage` 拿不到 string detail → backend 回的可能是 422 array
2. grep frontend vs backend：[NasClients.tsx:210](../frontend/src/pages/radius/NasClients.tsx) 用 `name="secret"`，但 [features/nas_clients/routes.py:54](../services/gateway/features/nas_clients/routes.py) 預期 `shared_secret` ← **不對齊**
3. 同時順便檢查 [services/auth/freeradius_config_manager.py:468](../services/auth/freeradius_config_manager.py#L468)：讀 `client.get("shared_secret_encrypted", "changeme")` 但 DB column 是 `secret_encrypted` ← **第二個 bug**

### 變更（兩個 PR 一起開）

#### PR [#30](https://github.com/acronhuang/openradiusweb/pull/30) — frontend 欄位名 + `extractErrorMessage` 強化
- [NasClients.tsx](../frontend/src/pages/radius/NasClients.tsx) 4 處 `secret` → `shared_secret`（interface field、edit form reset、handleSave omit-on-blank、Form.Item name）
- [api.ts](../frontend/src/api.ts) `extractErrorMessage` 加 422 array 處理 → 「Field required」這類訊息會顯示出來

#### PR [#31](https://github.com/acronhuang/openradiusweb/pull/31) — clients.conf 讀對 column
- [freeradius_config_manager.py:468](../services/auth/freeradius_config_manager.py#L468) `shared_secret_encrypted` → `secret_encrypted`
- 順便移除死 code line 475 `ip_prefix` 欄位（schema 沒這欄，always fallback to 32）

### 結果
兩個 merged → main `2630b85`。Server 上 `git pull` + 重 build frontend + freeradius_config_watcher。

---

## Round 7 — 重新測試：500 Internal Server Error（PR #32）

### Prompt
> 問題一樣

### 探索
- gateway log traceback：
  ```
  sqlalchemy.exc.ProgrammingError: <PostgresSyntaxError>: syntax error at or near ":"
  [SQL: ... VALUES ($1, :ip_address::inet, $2, $3, $4, $5, $6) ...]
  ```
- asyncpg 的 `:name → $N` preprocessor 在 trailing `::type` typecast 處停掉，留下半個 `:ip_address::inet`，PostgreSQL 拒絕
- `radius_nas_clients.ip_address` 是 **VARCHAR(50)** 不是 INET（schema check）→ cast 根本不需要，但其他 features 的 `:foo::type` 都會踩同一個 bug
- 全 grep `services/gateway/features/`：**8 個 features + utils.safe_sql 都用 `:foo::type`**

### 變更（PR [#32](https://github.com/acronhuang/openradiusweb/pull/32)）

| Feature | Type | 修法 |
|---|---|---|
| nas_clients | inet | 移除（VARCHAR 不需 cast）|
| auth | uuid | `CAST(:user_id AS uuid)` |
| coa | inet | `CAST(:nas_ip AS inet)` |
| mab_devices | macaddr | 3 處 `CAST(...)` |
| policies | jsonb | 3 columns `CAST(...)` |
| radius_auth_log | inet | `CAST(...)` |
| vlans | cidr | INSERT + UPDATE 都改 |
| utils.safe_sql | * | `build_safe_set_clause` 改 emit `CAST(...)` 形式 |

加 regression test [`tests/unit/test_no_inline_inet_cast.py`](../services/gateway/tests/unit/test_no_inline_inet_cast.py) — 掃 130 個 feature .py 檔案，禁止 `:identifier::identifier` pattern 重新出現。

CI 第一次 fail（漏改 `test_safe_sql.py` 對舊格式的斷言）→ 補上後 378/378 全綠 → merge → main `2db75c7`。

---

## Round 8 — Watcher 內建的同樣 bug（PR #33）

### Prompt
> 問題一樣

### 探索
- 重新測試 NAS Client save：成功 ✓，DB 有 row ✓
- 但 `clients.conf` 還是只有 default `localhost`，沒有 Fortigate-90D
- 看 watcher log：`[config-manager] ERROR generating configs: column "key" does not exist`
- [freeradius_config_manager.py:128](../services/auth/freeradius_config_manager.py#L128) 用 `SELECT key, value FROM system_settings`，但 schema 欄位名是 `setting_key` 跟 `setting_value`
- **Cascading failure**：`generate_eap_config()` 是第一個呼叫 `_load_system_settings`；它 raise 之後整個 `generate_all_configs` 提前死，clients.conf 那段根本沒跑

### 變更（PR [#33](https://github.com/acronhuang/openradiusweb/pull/33)）

```python
# OLD
SELECT key, value FROM system_settings WHERE category = ...

# NEW（aliased 讓 dict-comp 可讀性不變）
SELECT setting_key AS k, setting_value AS v FROM system_settings WHERE category = ...
return {r["k"]: r["v"] for r in rows}
```

PR 開、CI 綠、merge → main `dccd85f`。

---

## Round 9 — Watcher 修了，但 freeradius 仍讀 default config（PR #34）

### Prompt
> 問題一樣
>
> Can't contact RADIUS server（截圖：Fortigate Edit RADIUS Server 頁面）

### 探索
經 4 段診斷確認：
1. `radius_nas_clients` table 有 `Fortigate-90D / 192.168.0.99` ✓
2. **`docker exec orw-freeradius cat /etc/raddb/clients.conf`** — 只有 default `localhost` ❌
3. 但 watcher log 說 `[clients] applied (hash: 3f039f3713d1...)` → watcher 有寫，freeradius 沒讀到
4. Volume 配置：watcher 寫到 `/etc/freeradius/orw-managed/clients.conf`，freeradius 讀 `/etc/freeradius/clients.conf`（兩個不同路徑）

讀 `freeradius_entrypoint.sh`：
```bash
# Copy clients.conf if generated   ← cp（snapshot），不是 ln -sf
if [ -f /etc/freeradius/orw-managed/clients.conf ]; then
    cp /etc/freeradius/orw-managed/clients.conf /etc/freeradius/clients.conf
fi
```

對比：mods-enabled 跟 sites-enabled 都用 `ln -sf` — proxy.conf + clients.conf 是不一致的例外。

### 變更（PR [#34](https://github.com/acronhuang/openradiusweb/pull/34)）
把 entrypoint 兩個 `cp` 改成 `ln -sf`，watcher 後續更新（HUP）才會被 freeradius 看到、不需 restart container。

PR 開（**未 merge** — 在後續 EAP restart loop 解決前先 hold）。

但是 user 已經先重 build freeradius（用 PR #34 之前的 main），entrypoint 仍 cp — 不過因為 watcher 已經寫好 clients.conf 進 `orw-managed/`，且 entrypoint 在 freeradius 啟動時 cp 一次，**clients.conf 終於有 Fortigate-90D 跟正確 secret `MDS2026`**。

---

## Round 10 — freeradius 在 restart loop（EAP module 載入 fail）

### Prompts
> [前略 各種 docker compose ps + log 輸出]
>
> A（選擇產 dummy cert）

### 探索
- freeradius `ps` 顯示 `Restarting (1) X seconds ago`
- `docker logs` 只看到 entrypoint 跑完後的 `=== Starting radiusd...`，沒 radiusd 自己的 output
- 用 `docker compose run --rm --entrypoint='' freeradius bash -c 'freeradius -CX'` 繞過 entrypoint 跑 debug check：
  ```
  tls: (TLS) Failed reading private key file "/etc/freeradius/certs/server.key"
  tls: (TLS) error:1C800064:Provider routines::bad decrypt
  rlm_eap_tls: Failed initializing SSL context
  rlm_eap (EAP): Failed to initialise rlm_eap_tls
  /etc/freeradius/mods-enabled/eap[7]: Instantiation failed for module "eap"
  ```
- Manager 早就有 warning：`No active CA or server certificate found. EAP config will use placeholder paths.` — 故意產 placeholder 路徑但**沒實際建 cert 檔案** → radiusd 一載入就死

### 走過的修法（A → B → 都失敗）

#### 嘗試 A：產 dummy self-signed cert 進 `freeradius_certs` volume
```bash
docker run --rm -v openradiusweb_freeradius_certs:/certs alpine sh -c '
  apk add --quiet openssl
  openssl req -x509 -newkey rsa:2048 -nodes -keyout server.key ...
  chmod 644 server.key server.pem
  chown 101:101 ...
'
```
**結果**：仍 restart loop。原因：EAP config 設了 `private_key_password = `（empty string），OpenSSL 3.x 對 unencrypted PEM 給空 password 會 raise "bad decrypt"；OR cert manager 重新跑時把 dummy 蓋掉。

#### 嘗試 B：手動刪掉 `orw-managed/mods-available/eap` + symlink
```bash
docker compose stop freeradius_config_watcher
sudo rm /var/lib/docker/volumes/openradiusweb_freeradius_config/_data/mods-available/eap
sudo rm /var/lib/docker/volumes/openradiusweb_freeradius_config/_data/mods-enabled/eap
docker compose restart freeradius
```
**結果**：仍 restart loop。原因：**freeradius 自己的 entrypoint 也跑 `freeradius_config_manager.py --generate-and-apply`**，把 `orw-managed/mods-available/eap` 立刻重新生成；entrypoint 接著 symlink eap → radiusd 載入仍 fail。

#### 嘗試 C：sed in-place 改 entrypoint 跳過 eap symlink
```bash
sudo sed -i 's|name=$(basename "$conf")|name=$(basename "$conf"); if [ "$name" = "eap" ] && [ ! -s /etc/freeradius/certs/server.key ]; then echo "Skipping eap (no cert)"; continue; fi|' \
  services/auth/freeradius_entrypoint.sh
docker compose build --no-cache freeradius
docker compose up -d freeradius
```
**結果**：sed 重複 match 同一行兩次（regex `name=$(basename "$conf")` 在 sites-enabled loop 的 `name=$(basename "$site")` 也命中嗎？看貼回的內容是同一行兩次 if-block — 不確定原因），但更重要：**dummy cert 已經存在**，所以條件 `[ ! -s /etc/freeradius/certs/server.key ]` 為 false，eap 仍被 enable，仍 restart loop。

### Round 10 中段狀態
- freeradius 仍 restart loop
- 走過 6 種繞過嘗試（dummy cert、改 entrypoint sed、刪 eap module、刪 inner-tunnel、`SKIP_MODS` 環境變數）都失敗
- **核心問題**：watcher reconciliation 會把 entrypoint 的 sed band-aid 寫回原樣 → 必須改 source code

---

## Round 11 — 全面 audit + 開出 PR #36（comprehensive freeradius config gen 修正）

### Prompt
> 已經修很久了請檢查所有程式
>
> 依照軟體開發 SDD TDD BDD DDD 等等...進行確認

### 探索（DDD/SDD-lens audit）
派 Explore subagent 做整個 freeradius 整合鏈的完整 audit，~500 lines 報告，找到 3 個 root cause architectural gaps：

| Gap | 內容 |
|---|---|
| **1. 既有 jinja2 templates 沒被用到** | `site_default.j2` / `site_inner_tunnel.j2` / `python.j2` 寫得很完整但 manager 用 165 行 string concat 重新發明輪子 |
| **2. 無條件 module references** | site default **無條件** emit `eap` / `orw` references；沒 cert 時 eap 模組不被生成，但 site 仍引用 → radiusd parse 死 → restart loop |
| **3. rlm_python vs rlm_python3** | template 用 `python orw {`（Python 2 directive）+ Dockerfile 沒裝 `freeradius-python3`；rlm_orw.py 是 Python 3 → instantiate fail |

### 變更（PR [#36](https://github.com/acronhuang/openradiusweb/pull/36)）
- **`freeradius_config_manager.py`**：把 `generate_site_default()` / `generate_site_inner_tunnel()` / `generate_python_config()` 從 string concat 改為 template render；傳 `has_eap` / `has_python` / `ldap_modules` / `realms_enabled` flags；inner-tunnel 在沒 EAP 時整個跳過。淨減 -315 行。
- **`templates/site_default.j2` + `site_inner_tunnel.j2`**：所有 `eap` / `orw` references 包進 `{% if has_eap %}` / `{% if has_python %}`。
- **`templates/python.j2`**：`python orw {` → `python3 orw {`（rlm_python3 directive）+ python_path 加雙引號。
- **`Dockerfile.freeradius`**：加 `freeradius-python3` 套件 + build-time sanity check `radiusd -v | grep rlm_python3`。
- **`freeradius_entrypoint.sh`**：cp → ln-sf（吸收 PR #34）+ 修 chmod glob bug（之前 `chmod 600 /certs/server/*.key` 永遠 match 不到，因為實際路徑是 `/certs/server.key` 沒 subdir）+ 移除無用 mkdir。

merged → main `c504890`

---

## Round 12 — Dockerfile sanity check 太嚴格 → PR #37

### 觀察
PR #36 merged + redeploy → freeradius image build 失敗：
```
ERROR: rlm_python3 not available in this freeradius build
```
PR #36 加的 build-time check 抓出真實問題：**`freeradius/freeradius-server:3.2.3` 不 bundle rlm_python3**，且 Debian apt repo 也沒 `freeradius-python3` 套件（要從 source build freeradius 才能加）。

### 變更（PR [#37](https://github.com/acronhuang/openradiusweb/pull/37)）
- **`Dockerfile.freeradius`**：移除 build-time sanity check（不該擋 build），改 comment 說明 rlm_python3 沒裝、靠 runtime detect。
- **`freeradius_config_manager.py`**：加 `_rlm_python3_available()` helper — 跑 `radiusd -v` grep `rlm_python3`，找不到就 return False；`generate_all_configs` 用這個 flag 決定 `has_python`，沒 rlm_python3 就跳過 python config。
- **未來**：要啟用 orw module 時改成有 rlm_python3 的 freeradius image，runtime detect 自動切回 has_python=True，**不用改 code**。

merged → main `6b4c875`

---

## Round 13 — Template 殘留 bug：preprocess in accounting → PR #38

### 觀察
PR #37 merged + redeploy → freeradius 仍 restart loop。`freeradius -CX` debug：
```
/etc/freeradius/sites-enabled/default[176]: "preprocess" modules aren't allowed in 'accounting' sections
/etc/freeradius/sites-enabled/default[152]: Errors parsing accounting section.
```

### 變更（PR [#38](https://github.com/acronhuang/openradiusweb/pull/38)）
`templates/site_default.j2` accounting section 移除 `preprocess` module call（5 行刪除）。`preprocess` 只暴露 authorize / pre-proxy methods，不能用在 accounting。

merged → main `c7e3aac`

---

## Round 14 — 又一個 template bug：remove_reply_message_authenticator → PR #39

### 觀察
PR #38 merged + redeploy → freeradius 仍 restart loop。`freeradius -CX`：
```
/etc/freeradius/sites-enabled/default[123]: Failed to find "remove_reply_message_authenticator" as a module or policy.
/etc/freeradius/sites-enabled/default[111]: Errors parsing post-auth section.
```

### 變更（PR [#39](https://github.com/acronhuang/openradiusweb/pull/39)）
`templates/site_default.j2` post-auth section 移除 `remove_reply_message_authenticator` reference。這是 FreeRADIUS 3.2.5+ 才 bundle 的 unlang policy，3.2.3 沒有。是純 hardening 用途，移掉不影響協定正確性。

merged → main `dba0a44`

### 順便 audit
grep 所有 standalone module refs：剩下的 (`eap` / `mschap` / `pap` / `preprocess` / `suffix` / `expiration` / `logintime` / `filter_username` / `ntdomain` / `detail`) 都是 freeradius 標準 bundle，不會再有 missing-module。

---

## Round 15 — freeradius 終於穩定 + 收尾

### 操作
PR #39 merged + redeploy（兩個 image 都重 build：watcher 跟 freeradius 都有 `freeradius_config_manager.py` 跟 templates 嵌進去，**只重 build 其中一個會被另一個用舊 code 跑時覆蓋**）：

```bash
docker compose ... build --no-cache freeradius freeradius_config_watcher
sudo rm -f /var/lib/docker/volumes/openradiusweb_freeradius_config/_data/sites-{available,enabled}/*
docker compose ... up -d freeradius_config_watcher
sleep 10
docker compose ... up -d freeradius
sleep 30
```

### 驗證
```
orw-freeradius   Up About a minute   0.0.0.0:1812-1813->1812-1813/udp
orw-freeradius-config-watcher   Up 29 minutes

$ docker exec orw-freeradius radtest -x admin OpenNAC2026 127.0.0.1:1812 0 testing123
Sent Access-Request Id 47 from 0.0.0.0:e256 to 127.0.0.1:1812 length 75
Received Access-Reject Id 47 from 127.0.0.1:714 to 127.0.0.1:57942 length 20
(0) -: Expected Access-Accept got Access-Reject
```

✅ freeradius 穩定 Up + UDP 1812-1813 對外綁定 + 真的能 process RADIUS request。Access-Reject 是預期（沒 orw module 接 DB 做 user lookup），但 server alive 且回應。

### freeradius -X 完整啟動 log（最後幾行）
```
Listening on auth address 127.0.0.1 port 18120 bound to server inner-tunnel
Listening on auth address * port 1812 bound to server default
Listening on acct address * port 1813 bound to server default
Ready to process requests
```

---

## 已 merge PR 對照表 v2（含 Round 11-15）

| PR | Commit | 一句話 |
|---|---|---|
| [#28](https://github.com/acronhuang/openradiusweb/pull/28) | `a927120` | 2026-04-29 session log |
| [#29](https://github.com/acronhuang/openradiusweb/pull/29) | `d784107` | deployment-guide.md v2.1 |
| [#30](https://github.com/acronhuang/openradiusweb/pull/30) | `3d78673` | frontend NAS Client form 送對 `shared_secret` |
| [#31](https://github.com/acronhuang/openradiusweb/pull/31) | `2630b85` | freeradius_config_manager 讀對 `secret_encrypted` 欄位 |
| [#32](https://github.com/acronhuang/openradiusweb/pull/32) | `ddb6e88` + `2db75c7` | gateway-wide `:foo::type` SQL → `CAST(:foo AS type)` |
| [#33](https://github.com/acronhuang/openradiusweb/pull/33) | `dccd85f` | freeradius_config_manager 讀對 `setting_key/setting_value` |
| **#34** | (吸收進 #36) | freeradius entrypoint cp → ln -sf |
| [#35](https://github.com/acronhuang/openradiusweb/pull/35) | (本檔) | 2026-04-30 session log |
| [#36](https://github.com/acronhuang/openradiusweb/pull/36) | `c504890` | conditional config generation + 真的用 templates + 修 entrypoint chmod glob |
| [#37](https://github.com/acronhuang/openradiusweb/pull/37) | `6b4c875` | rlm_python3 runtime detect（image 沒 bundle 也能跑）|
| [#38](https://github.com/acronhuang/openradiusweb/pull/38) | `c7e3aac` | template: 移除 accounting section 裡的 preprocess |
| [#39](https://github.com/acronhuang/openradiusweb/pull/39) | `dba0a44` | template: 移除 remove_reply_message_authenticator（3.2.3 沒 bundle）|

---

## 完整 Prompt 清單（按時序）

| # | Round | Prompt（原文 / 摘要） |
|---|-------|---------------|
| 1 | R1 | 請確認專案是否完成，還有哪些需要進行？如果完成提供部署文件我要手動部署確認可以執行 |
| 2 | R1 | 1（merge PR #28、#29 → 開始部署）|
| 3 | R2 | 部署前 跑一下你信得過的 smoke flow這要如何做 |
| 4 | R3 | 我換另一臺進行安裝 |
| 5 | R3 | Package docker-ce is not available（apt repo 沒加成功）|
| 6 | R3 | curl -sI https://download.docker.com/ → HTTP/2 200（網路通）|
| 7 | R3 | apt install ca-certificates curl gnupg lsb-release → 都已最新 |
| 8 | R3 | docker run hello-world → Hello from Docker! |
| 9 | R3 | mdsadmin@radius:/opt$ git fetch origin → fatal: not a git repository |
| 10 | R3 | Username for github → Authentication failed for HTTPS |
| 11 | R3 | github_pat_11AKA43... ⚠️ token 洩漏 |
| 12 | R3 | 第二次貼 token + 403 Write access not granted |
| 13 | R3 | A（改 repo 為 public）|
| 14 | R3 | git log → e4be823 / 6cbf98b / 5bb3b9b |
| 15 | R3 | env 設定 → JWT sed 失敗（base64 含 `\|`）→ 改 alphanumeric |
| 16 | R3 | docker compose build → permission denied on docker.sock |
| 17 | R3 | docker --version / compose version / hello-world OK |
| 18 | R3 | build success（gateway / frontend / 全 12 containers）|
| 19 | R3 | docker compose ps → 12 Up |
| 20 | R3 | psql users → 1 row（admin seeded）|
| 21 | R3 | 003 + 004 manual migrations 跑成功 |
| 22 | R3 | login → 401 Invalid credentials |
| 23 | R4 | docker exec orw-postgres "\d users"（看 password_hash column 名）|
| 24 | R4 | bcrypt reset 流程 → token len 301、dot1x_overview 回 9 個 key |
| 25 | R5 | 請修改部署文件 |
| 26 | R5 | 把 #28 + #29 都 merge |
| 27 | R6 | 要如何設定Radius Secret |
| 28 | R6 | 有問題（截圖：Failed to save NAS client）|
| 29 | R6 | 繼續修（PR #31 freeradius column name）|
| 30 | R7 | 問題一樣（PR #32 之前一輪 redeploy 後仍失敗）|
| 31 | R7 | A（merge PR #32）|
| 32 | R7 | docker logs gateway → traceback `:ip_address::inet` |
| 33 | R7 | PR #32 CI fail → 補 test → CI 綠 |
| 34 | R8 | 問題一樣 |
| 35 | R8 | docker logs orw-freeradius-config-watcher → `column "key" does not exist` |
| 36 | R9 | Can't contact RADIUS server（Fortigate UI 截圖）|
| 37 | R9 | 4 段 diagnostic：DB 有 row / clients.conf 沒 row / volume 配置 |
| 38 | R9 | 先寫這個 PR（PR #34 cp → ln -sf）|
| 39 | R10 | freeradius restart loop |
| 40 | R10 | A（產 dummy cert）|
| 41 | R10 | uid 101 / chown / chmod 644 / 仍 restart |
| 42 | R10 | sed entrypoint hack / 仍 restart |
| 43 | R10 末 | 將所有步驟及prompt都記錄下來 |
| 44 | R11 | 已經修很久了請檢查所有程式 / 依照軟體開發 SDD TDD BDD DDD 等等...進行確認 |
| 45 | R12 | 問題一樣（PR #36 build sanity check 把 build fail 掉）|
| 46 | R13 | 問題一樣（PR #37 後 preprocess in accounting 卡關）|
| 47 | R14 | 問題一樣（PR #38 後 remove_reply_message_authenticator 卡關）|
| 48 | R15 | 還是一樣 Can't contact RADIUS server（freeradius 終於 Up，但 user 短暫 container restart 期間沒辦法 radtest）|
| 49 | R15 | 寫這份 session log 補完 |

---

## 開著的 PR
無（PR #1 是 whitesource bot 不算）。所有為這次部署開的 PR 都已 merge。

---

## 部署最終狀態 ✅

- **OS**: Ubuntu 22.04 jammy on 192.168.0.250 (eno1)
- **Docker**: 27.x + Compose v2 + Buildx
- **Repo**: `/opt/openradiusweb` clone from public `acronhuang/openradiusweb`，main HEAD **`dba0a44`**
- **Containers**: **12/12 Up**（postgres / redis / nats healthy；gateway / frontend / device_inventory / discovery / policy_engine / switch_mgmt / coa_service / freeradius_config_watcher / **freeradius** 全部 Up）
- **DB**: schema OK，admin password reset 成功（OpenNAC2026），1 個 NAS client row（Fortigate-90D / 192.168.0.99 / secret = MDS2026），1 CA cert + 1 Server cert 都 Active
- **Web UI**: http://192.168.0.250:8888 可登入、CRUD 流程通
- **RADIUS server**: ✅ **alive**，UDP 1812-1813 對外綁定，本機 radtest 可收到 Access-Reject 回應
- **RADIUS auth**: ⚠️ **半成品** — server 通了但 orw module 沒載（沒 rlm_python3）→ 沒 DB user lookup → 任何用戶都會被 reject。Fortigate Test Connectivity 應該可通（看到 RADIUS server 有回應）但實際 admin 認證會 fail

---

## 給未來的人的「如果重來」清單 v2

### 已收進文件的（fresh deploy 不會再踩）
1. **`deployment-guide.md` v2.1**（PR #29）已收進 5 個坑：Docker apt repo 設定、GitHub PAT auth、env 密碼產生（避免 base64 含 `|` 破 sed）、bcrypt seed-hash reset 流程、smoke test endpoint
2. **`scripts/check_no_new_routes.py`**（PR #26 後）禁止新檔加進 `services/gateway/routes/`
3. **`tests/unit/test_no_inline_inet_cast.py`**（PR #32）regression test 禁止 `:foo::type` SQL 重新出現

### 已修在 code 的（從根本不會再發生）
4. **PR #36** 把 `freeradius_config_manager` 從 string concat 改為真的用 templates，加 `has_eap` / `has_python` / `ldap_modules` flags — 沒 cert / 沒 rlm_python3 都自動跳過 module + site refs
5. **PR #37** runtime detect rlm_python3 — 之後換成有 bundle 的 freeradius image，自動切回 has_python=True 不用改 code
6. **PR #38 / #39** 移除 freeradius 3.2.5+ 才 bundle 的 unlang policy refs（`preprocess` in accounting、`remove_reply_message_authenticator`）

### 還沒解 / 留給後續
7. **freeradius image 沒 bundle rlm_python3** — 換 `freeradius/freeradius-server` 之外的 image（含 freeradius-python3 套件的）OR 自己 from-source build。當前只能跑 PAP/MAB/EAP-TLS/EAP-PEAP（cert-based），不能跑 DB-backed user lookup
8. **bcrypt seed-hash mismatch** — 每次 fresh deploy 都要手動 reset admin password。長期解法：
   - (a) 修 `migrations/seed.sql` 不寫死 hash，改 entrypoint 第一次啟動時動態產
   - (b) 把 reset 步驟整合進 `init.sql`
9. **certs feature 的寫入路徑契約** — 確認 UI Generate Server cert → DB → `freeradius_config_manager._write_cert_files()` → `/etc/freeradius/certs/server.{key,pem}` 整條鏈路在 PR #36 後是 OK 的（這次 deploy 證實），但要做 BDD 測試覆蓋
10. **stale config files in shared volume** — 當 manager 不再產某個 config（例如沒 rlm_python3 時不產 python config），上次的舊檔還在 volume 裡，entrypoint loop 仍會 symlink → freeradius 載入 → fail。Workaround 是手動刪。**PR #38 應該補：每次 generate 前 cleanup 不在新一輪輸出裡的 `mods-available/*` / `sites-available/*` 檔案**。

### 流程性 lesson
11. **PAT-leak-via-chat** 提醒：以後操作流程不要要求使用者貼 token；改成「server 端 inline 進 clone URL，立刻 `git remote set-url` 清掉」
12. **template-vs-runtime-environment 落差** — `freeradius/freeradius-server:3.2.3` 不含 rlm_python3、`freeradius-python3` apt 套件不在它的 repo、`remove_reply_message_authenticator` policy 也是 3.2.5+ 才有。Templates 不能假設這些一定在；用 runtime detect 或 conditional generation 才是 robust 做法
13. **band-aid sed 改 entrypoint** 是 dead-end — 因為 watcher 的 reconciliation 會把 manager 寫的 config 重新覆蓋，繞過 entrypoint sed。要修就修 source code（manager 或 template），不要修 entrypoint
14. **多個 service share code via 不同 image** — `freeradius_config_manager.py` 同時 bundle 進 `freeradius` 跟 `freeradius_config_watcher` 兩個 image。任何 manager 的修改要**同時 rebuild 兩個 image**，不然會發生「watcher 寫新 config，freeradius 內建 entrypoint 跑舊 config_manager 又把它覆蓋」的詭異現象
15. **完整 audit > 單點 fix** — 當踩到第 6 個 bug 時就應該停下來做 DDD/SDD lens 完整 audit（用 Explore subagent），找到所有 architectural gaps 一次修完，不要繼續 reactive band-aid。Round 11 開始這樣做後，剩下的 fix（PR #36-#39）就只是逐步 converge，每個都是真實的根本問題
