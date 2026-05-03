# Session Log — 2026-05-02 Production Debug + Pure MAB Rollout

**Deployment**: 192.168.0.250 (RADIUS server, FortiWiFi 60C as NAS)
**AD**: mds.local (192.168.0.253)
**Test users**: `ming@mds.local` (AD account) + POCO F5 Pro phone + TWTPEN0804008A
**Duration**: ~6 hours (10:00 → 17:15 local time)
**Outcome**: 7 PRs merged, both 802.1X + pure MAB authentication paths working in production

This is a chronological session log. The `Why → Action → Result` structure is
preserved for each problem so a future engineer can use it as a debug recipe.
For just the symptoms-and-fixes index, see [troubleshooting-8021x-ad.md](troubleshooting-8021x-ad.md).

---

## Phase 0 — Starting state

Continuation of the 2026-05-01 deployment session. Production was up but
WiFi authentication was failing with assorted symptoms:
- Phones couldn't connect to MDS-01 (WPA2-Enterprise SSID)
- `freeradius` logs showed various errors per attempt
- `radius_auth_log` table existed but was empty

Goal for the day: get `ming@mds.local` to actually authenticate and connect
on a phone, then build out pure MAB SSID for IoT devices.

---

## Phase 1 — 802.1X + AD integration (TTLS+PAP)

### Issue 1: `Ignoring request from unknown client`

**User prompt**: pasted FortiGate log showing RADIUS requests being rejected.

**Root cause**: `freeradius_entrypoint.sh` symlinked managed configs at
`/etc/freeradius/clients.conf` but Debian's freeradius reads from
`/etc/freeradius/3.0/clients.conf`. The wrong path silently meant `clients.conf`
contained only the stock localhost entry, so real NAS requests got rejected.

**Fix**: Changed entrypoint to use `FR_CONF_DIR=/etc/freeradius/3.0` (PR #56,
already merged earlier in the day).

### Issue 2: TLS 1.3 EAP supplicant compatibility

**User prompt**: 「用TLS 1.3 與TLS 1.2 有什麽差異」 (What's the difference?)

**Why this came up**: freeradius logged a warning about TLS 1.3 + 802.1X
supplicant compatibility. Most Android <14 / iOS <16 / Win10 RTM don't do
EAP-TLS 1.3 well.

**Fix**: Added editable `tls_max_version` setting to `system_settings` table
+ a UI control in System Settings → RADIUS tab with a TLS 1.3 warning panel.
**PR #57**: `feat(settings-ui): editable RADIUS tab with TLS Min/Max + 1.3 warning`

### Issue 3: `Strong (authentication) required`

**Symptom**:
```
rlm_ldap: ldap_bind: Strong(er) authentication required
```

**Root cause**: AD requires LDAPS (port 636) by default; freeradius was
trying plain LDAP on port 389.

**Fix** (SQL hotfix on production):
```sql
UPDATE ldap_servers SET use_tls=true, port=636, tls_require_cert='never'
WHERE server_name='mds.local';
```

### Issue 4: `CERTIFICATE_VERIFY_FAILED`

**Root cause**: freeradius didn't trust AD's CA.

**Fix**: `tls_require_cert='never'` (lab acceptable). Production should import
the AD-CS root CA properly — TODO.

### Issue 5: EAP cert mismatch

**Symptom**:
```
error:05800074:x509 certificate routines::key values mismatch
```

**Root cause**: `server.pem` and `server.key` weren't a matching pair.

**Fix**: User imported a fresh AD-CS-issued cert pair via the certificates UI.

### Issue 6: `No Auth-Type found: rejecting`

**Root cause**: `rlm_ldap` found the user but didn't tell freeradius which
module should verify the password. Site templates were missing the explicit
`Auth-Type := <ldap_module_name>` block.

**Fix**: Modified `site_inner_tunnel.j2` and `site_default.j2` (PR #56) to
add after the LDAP module call:
```jinja
if ((ok || updated) && User-Password) {
    update control {
        &Auth-Type := {{ ldap_mod.module_name }}
    }
}
```

### Issue 7: PEAP-MSCHAPv2 architectural incompatibility

**Symptom**:
```
mschap: FAILED: No NT-Password.  Cannot perform authentication
```

**Root cause**: PEAP-MSCHAPv2 needs the user's NT-Hash to compute the
challenge/response. AD doesn't expose `unicodePwd` via LDAP, and OpenRadiusWeb's
freeradius isn't joined to the AD domain (no Samba/winbind/ntlm_auth bridge).

**User prompt**: 「freeradius 用 LDAP bind 來驗證... 一開始需求不是就有要求用LDAP驗證嗎？」 (Wait, weren't we always supposed to use LDAP auth?)

**Fix**: Switch the phone EAP method from PEAP-MSCHAPv2 to **EAP-TTLS+PAP**.
TTLS+PAP sends the cleartext password through the TLS tunnel, freeradius does
LDAPS bind to AD with it — no NT-Hash needed. See
[troubleshooting-8021x-ad.md PEAP vs TTLS section](troubleshooting-8021x-ad.md#peap-mschapv2-vs-eap-ttlspap為何重要)
for the full explanation.

### Issues 8 & 9: LDAP search base + filter

**Symptom 8**: `Search returned no results` for `ming` even though the user existed.

**Root cause 8**: search base was `CN=Users` but ming was in `OU=IT,OU=MDS`.

**Fix 8**:
```sql
UPDATE ldap_servers SET user_search_base='DC=mds,DC=local';
```

**Symptom 9**: filter `(sAMAccountName={username})` wasn't expanding —
`{username}` is ClearPass syntax, not freeradius.

**Fix 9**:
```sql
UPDATE ldap_servers SET
  user_search_filter='(sAMAccountName=%{%{Stripped-User-Name}:-%{User-Name}})';
```

### Issue 10: Phone caches old EAP profile

**Symptom**: Server settings updated, freeradius restarted, log showed new
behavior — but phone connect attempts looked exactly like before.

**Fix**: Hard reset on the phone: WiFi → long-press SSID → Forget Network →
turn WiFi off entirely → wait 5s → turn back on → re-add with all fields
fresh. Android caches profiles aggressively.

### Issue 11: `mschap: FAILED: No NT-Password` (recurrence)

After fixing 1-9, ming's auth was still showing `mschap: FAILED`. Phone profile
had reverted to PEAP-MSCHAPv2 again.

**Fix**: Re-applied "forget network" + reconfigured as TTLS + PAP, used
`ming@mds.local` as Identity, ming's AD password as Password.

**Result**: First successful login!
```
Auth: Login OK: [ming@mds.local/!QAZxcvfr432wsde] (from client Fortigate-60C ...)
```

(Password leaked in log — fixed in Phase 2.)

---

## Phase 2 — PR #58: rlm_python3 actually writes to DB + log security

After Phase 1 succeeded, three follow-up issues surfaced.

### Q1: 「為何 log 顯示密碼」 (Why does the log show the password?)

**Root cause**: `radiusd.conf` had `auth_goodpass = yes` and `auth_badpass = yes`,
which freeradius uses to log the password on every Auth: line. The entrypoint
script was actively setting these to `yes` (debug leftover).

**Fix**: Edit `freeradius_entrypoint.sh` to flip those to `no`.

### Q3: 「沒看到登入成功或失敗的 log」 (No login events visible in Web UI)

**Symptom**: freeradius docker logs showed `Login OK` but
`SELECT * FROM radius_auth_log` returned 0 rows.

**Debugging path** (this took the longest of the day):

1. Suspected the orw rlm_python module was failing silently. Added a
   `radiusd.radlog(L_INFO, f"ORW_DEBUG _log_auth_to_db HAS_DB={HAS_DB}")`
   line via `sed -i` directly into the running container's `orw.py`. Restarted.
   Result: `HAS_DB=False` — psycopg2 wasn't importing.

2. Investigated WHY psycopg2 wasn't importing. The Dockerfile installs it via
   `pip install --break-system-packages psycopg2-binary`, which lands at
   `/usr/local/lib/python3.11/dist-packages/psycopg2`. But running
   `python3 -S -c "import sys; print(sys.path)"` in the container showed:
   ```
   ['', '/usr/lib/python311.zip', '/usr/lib/python3.11', '/usr/lib/python3.11/lib-dynload']
   ```
   No dist-packages! `rlm_python3` initializes the embedded interpreter
   without running `site.main()`, so the standard dist-packages dirs are NOT
   on `sys.path`.

3. **Fix attempt 1 (failed)**: Modified `python.j2` template to set
   `python_path = ".../mods-config/python:/usr/local/lib/python3.11/dist-packages:..."`.
   Restarted. Still `HAS_DB=False`. rlm_python3's `python_path` config
   doesn't actually inject into sys.path before module import (or doesn't
   the way we expected).

4. **Fix attempt 2 (worked)**: Modified `orw.py` directly to inject paths
   via `sys.path.insert(0, ...)` BEFORE `import psycopg2`. Restarted.
   `HAS_DB=True`! But...

5. **Next bug**: `OpenRadiusWeb auth log failed: invalid input syntax for type uuid: ""`
   Empty string passed to a UUID column (tenant_id). Source:
   ```python
   TENANT_ID = os.environ.get("ORW_TENANT_ID", None)
   ```
   But `ORW_TENANT_ID=` (empty string) is set in the env, so `.get(..., None)`
   returns `""` not `None`. Postgres rejects `""` for a UUID column.

   **Fix**:
   ```python
   TENANT_ID = os.environ.get("ORW_TENANT_ID") or None
   ```

6. After all this: `radius_auth_log` started filling correctly.

**PR #58**: `fix(freeradius): make orw rlm_python3 module actually write to DB`
- `_ensure_site_packages()` function in `rlm_orw.py` (PR #58 codified what
  the hot-patch did, with dynamic Python version detection)
- `TENANT_ID = ... or None`
- `entrypoint.sh` flips `auth_goodpass / auth_badpass` to `no`
- Plus brand-new `docs/troubleshooting-8021x-ad.md` capturing all 10 issues
  from Phase 1

User prompt: 「推 PR」 → opened PR #58 → CI green → user said merge → merged.

### Q2: 「無法使用 MAC 驗證」 (Can't use MAC auth)

User asked about MAC authentication for devices that can't enter credentials.
Branched into Phase 3 + Phase 4.

---

## Phase 3 — PR #59: Per-MAC VLAN override

User wanted: phone authenticates as `ming@mds.local` on MDS-01 but should
always land on VLAN 15 regardless of which AD groups ming is in.

**Why this isn't classical MAB**: WPA2-Enterprise SSID forces 802.1X — supplicant
must do EAP, the AP never falls back to MAC-only. So we can't do MAB on MDS-01.
But we CAN look up the device MAC in `mab_devices` table during 802.1X post-auth
and use that table's `assigned_vlan_id` as a per-device VLAN override.

**Implementation** in `rlm_orw.py post_auth`:

```python
# Priority 1: per-MAC override from mab_devices table
if calling_mac:
    mab_device = _lookup_mab_device(_normalize_mac(calling_mac))
    if mab_device and mab_device.get("assigned_vlan_id"):
        vlan_id = mab_device["assigned_vlan_id"]
        ...

# Priority 2: group-based mapping (existing) — only if no per-MAC match
if vlan_id is None:
    groups = _get_user_ldap_groups(username, user_domain)
    vlan_id, matched_group = _lookup_vlan_for_groups(groups)
```

Refactored the existing MAB SQL out of `authorize()` into a `_lookup_mab_device()`
helper to share code between MAB requests and per-MAC overrides.

**PR #59**: `feat(freeradius): per-MAC VLAN override for 802.1X via mab_devices table`
→ CI green → merged.

---

## Phase 4 — Pure MAB SSID (FortiWiFi 60C side)

**User prompt**: 「那我可以使用mab驗證指定到任何SSID」 (Can I use MAB on any SSID?)

**Important conceptual unblock**: explained that **WPA2-Enterprise SSIDs cannot
do classical MAB**. The supplicant must initiate EAP, so the AP never sends
a MAC-as-username RADIUS request. To use MAB, you need a separate **Open** or
**WPA2-Personal** SSID with `radius-mac-auth enable`.

User created a new SSID called `MAB_Auth`. First attempt configured it as
`wpa2-only-enterprise` — wrong. Walked through:

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

(See [troubleshooting-8021x-ad.md MAB section](troubleshooting-8021x-ad.md#設定一個純-mab-ssid給不能打帳密的設備)
for the full FortiOS 5.2.x CLI sequence.)

### MAC mismatch debugging

After the SSID was up, freeradius log showed:
```
OpenRadiusWeb MAB request: 3c:13:5a:cc:21:21
OpenRadiusWeb MAB not in whitelist: 3c:13:5a:cc:21:21
```

But `mab_devices` had:
```
0e:9a:05:d2:bb:b2 | POCO F5 Pro    | 15
```

The phone was using its **real** MAC (`3c:13:5a:cc:21:21`) but the table held
the **random** MAC from a previous Android-randomization session. Updated:

```sql
UPDATE mab_devices SET mac_address='3c:13:5a:cc:21:21' WHERE name='POCO F5 Pro';
```

Phone reconnected → MAB approved → Login OK → got IP from FortiGate's
192.168.99.x range. **Pure MAB working end-to-end!**

```
OpenRadiusWeb MAB approved: 3c:13:5a:cc:21:21 (POCO F5 Pro) -> VLAN 15
Auth: Login OK: [3C-13-5A-CC-21-21/3C-13-5A-CC-21-21]
```

---

## Phase 5 — Three-act tragedy: PR #62 → PR #63 → PR #64

User asked for 3 follow-ups from PR #58's known-issues list:
1. Fix `auth_result` detection bug (reject events were logged as `success`)
2. Register `OpenRadiusWeb-Realm` in dictionary (cosmetic warning every request)
3. Switch Dockerfile from pip → apt for python deps (more robust than sys.path hack)

Bundled all 3 into **PR #62**. CI green, merged.

### Live deploy → freeradius crash-loop

```
Fatal Python error: drop_gil: drop_gil: GIL is not locked
Extension modules: radiusd, psycopg2._psycopg
```

**Root cause**: The auth_result fix added a SECOND `python3 orw_reject` instance
to `python.j2`, with the idea that the success and reject post-auth branches
would call different module names → different functions → different `auth_result`.

But **rlm_python3 has a known bug with multiple `python3 { }` instances in one
config** — the second sub-interpreter doesn't hold the GIL when its detach
hook fires, and Python aborts the whole daemon. The freeradius docs warn
about this; we hit it cold.

### Hot-patch nightmare

The container was in restart loop. Every `docker exec` failed mid-execution
because the container kept dying every few seconds. The hot-patch flow had to be:

```bash
sudo docker stop orw-freeradius
sudo docker cp <fixed-template> orw-freeradius:/etc/freeradius/orw-templates/...
sudo docker start orw-freeradius
```

Then the user did `git pull && docker compose build && up -d --force-recreate`
which **rebuilt with the still-broken main** and undid the hot-patch. Cycle
repeated 2-3 times. Until **PR #63 was merged**, every redeploy re-introduced
the GIL bug.

### PR #63 — partial revert

Reverted ONLY the auth_result-split parts of PR #62 (the broken bit). Kept the
apt-install + dictionary register parts (which were fine).

After PR #63 merged + production rebuilt: freeradius started cleanly.
But the auth_result detection bug was BACK — reject events logged as success again.

### PR #64 — proper fix using control attribute marker

Single `python3 orw` instance (no GIL games), but the site templates tag the
request before calling orw:

```jinja
post-auth {
    update request { &OpenRadiusWeb-Result := "success" }
    orw

    Post-Auth-Type REJECT {
        update request { &OpenRadiusWeb-Result := "reject" }
        orw
    }
}
```

`update request { &Foo := ... }` adds Foo to the request packet attributes,
which rlm_python3 DOES pass into Python's request tuple (unlike control attrs).
One instance, no crash, accurate result logging.

`OpenRadiusWeb-Result` registered as ATTRIBUTE 3003 in
`dictionary.openradiusweb`.

**Pre-merge production verification** (this time we tested before merging):

```bash
# Hot-patch the running container with branch's files
sudo docker stop orw-freeradius
git show origin/fix/auth-result-via-control-attr:<file> > /tmp/<file>
sudo docker cp /tmp/<file> orw-freeradius:<path>
# repeat for 4 files
sudo docker start orw-freeradius
```

Triggered phone auth (MAB success + 802.1X PEAP-MSCHAPv2 fail), then:

```sql
SELECT auth_result, auth_method, COUNT(*) FROM radius_auth_log
WHERE timestamp > NOW() - INTERVAL '5 minutes'
GROUP BY auth_result, auth_method;
```

Result:
```
 auth_result | auth_method  | count
-------------+--------------+-------
 reject      | EAP-MSCHAPv2 |     3   ← correctly logged as reject!
 reject      | EAP-PEAP     |     3   ← correctly logged as reject!
 success     | MAB          |     1   ← correctly logged as success
```

Verification passed → merged → production rebuilt cleanly. Auth log no longer
lies about what actually happened.

---

## Other plumbing done today

- **PR #52**: Committed `docs/api/openapi.yaml` + `frontend/package-lock.json`
  (both were untracked despite docs claiming they were under version control).
  Lockfile commit unblocked `npm audit`.
- **PR #1 (closed)**: Mend Bolt onboarding PR. Closed in favor of GitHub Dependabot,
  which we enabled via `gh api -X PUT .../{vulnerability-alerts,automated-security-fixes}`.
  Dependabot scan found 1 medium (vite path traversal in dev-only dep) and
  auto-opened PRs #60 + #61 for the bump (left open for next session).
- **`.env` symlink**: `.env.production` existed but `docker compose` only reads
  `.env`. `git pull` had been silently failing to provide passwords during
  `docker compose build`. Fix: `ln -s .env.production .env`.

---

## Final PR table

| PR | Status | What |
|----|--------|------|
| [#57](https://github.com/acronhuang/openradiusweb/pull/57) | ✅ merged | settings UI editable + TLS warning |
| [#58](https://github.com/acronhuang/openradiusweb/pull/58) | ✅ merged | rlm_python3 actually writes to DB + password not in log + troubleshooting doc |
| [#59](https://github.com/acronhuang/openradiusweb/pull/59) | ✅ merged | per-MAC VLAN override |
| [#52](https://github.com/acronhuang/openradiusweb/pull/52) | ✅ merged | commit openapi.yaml + lockfile |
| [#62](https://github.com/acronhuang/openradiusweb/pull/62) | ✅ merged (commit 1 reverted) | auth_result split + apt deps + dict register |
| [#63](https://github.com/acronhuang/openradiusweb/pull/63) | ✅ merged | revert PR #62 commit 1 (GIL bug) |
| [#64](https://github.com/acronhuang/openradiusweb/pull/64) | ✅ merged | auth_result via control-attribute marker (proper fix) |
| [#1](https://github.com/acronhuang/openradiusweb/pull/1) | ❌ closed | Mend Bolt — using Dependabot instead |
| #60, #61 | ⏳ open | Dependabot vite bumps — next session |

---

## Lessons learned (all earned with production downtime)

1. **rlm_python3 + multiple `python3 {}` instances = GIL crash.** One instance
   only. Use control attributes to differentiate context.

2. **WPA2-Enterprise can't do classical MAB.** Supplicant initiates EAP, so
   the AP never falls back to MAC. For pure MAB you need an Open or
   WPA2-Personal SSID with `radius-mac-auth enable`.

3. **AD without ntlm_auth bridge can't do PEAP-MSCHAPv2.** AD doesn't expose
   `unicodePwd` via LDAP, freeradius can't compute the MSCHAP challenge.
   Use TTLS+PAP — the password goes through the TLS tunnel, freeradius does
   LDAPS bind. Same security level (TLS protects PAP), much simpler ops.

4. **rlm_python3's embedded interpreter doesn't run `site.main()`.** The
   standard `dist-packages` dirs are NOT on `sys.path`. Either inject manually
   in your Python module, or arrange for packages to land somewhere already
   on the embedded interpreter's path. (Both — defense in depth.)

5. **Android random MAC breaks MAB whitelists.** Android rotates random MACs
   per SSID; today's MAC won't match tomorrow's whitelist entry. Set the
   phone's WiFi privacy to "use device MAC" before adding to `mab_devices`.

6. **`docker compose --env-file` doesn't auto-fallback to `.env.production`.**
   Either rename, symlink, or pass `--env-file` explicitly. Silent
   `WARN[0000] The "DB_PASSWORD" variable is not set` cost ~1 hour of
   "why won't the new container connect to postgres?" debugging.

7. **`docker compose up --force-recreate` cascades to dependencies by default.**
   That's why our first redeploy attempt also tried to recreate `orw-postgres`
   (which broke because of a missing bind mount path). Use `--no-deps` to
   recreate just the target service.

8. **Test broken-on-load issues by docker-cp + restart, not just CI.** PR #62
   passed all 6 CI checks but crashed the moment freeradius actually loaded
   the config. We added a "hot-patch the live container before merging"
   step to PR #64's test plan; it caught nothing for #64 itself but it's
   the right pattern.

9. **Phone WiFi profile cache is aggressive.** "Forget network" + WiFi off/on
   + re-add from scratch is the only reliable way to actually apply EAP
   method changes on Android.

10. **`radius_auth_log` writes failing silently is worse than failing loudly.**
    The combination of `if not HAS_DB: return` + `if not conn: return` +
    `try: ... except` (no log) meant `_log_auth_to_db` could no-op all day.
    Always log on the error path; cheap defense against this.

---

## Acknowledgements

- ming@mds.local: tireless debug subject across 4+ hours of failed authentications
- POCO F5 Pro `3c:13:5a:cc:21:21`: the device that finally proved pure MAB works
- FortiWiFi 60C `FWF60C3G12006551`: 12-year-old hardware that actually runs MAB
  via FortiOS 5.2.15 CLI
- Claude Code (Opus 4.7, 1M context): typed a lot of `sudo docker exec ... | grep`
