# PKI Architecture for openradiusweb Deployments

Which TLS cert does freeradius present to supplicants during the
EAP-TTLS handshake — and how do clients trust it? Three deployment
shapes, each with a different answer. Pick the one that matches your
environment BEFORE you deploy; switching later requires re-issuing
the cert and re-distributing trust to every client.

> **Why this doc exists**: PR #93 shipped an auto-renewal background
> task that only works for one of the three scenarios. PR #95 / the
> existing UI's "Generate" vs "Import" buttons make sense once you
> know which scenario you're in. Without this map, an operator
> imports a cert from their AD CA and then wonders why auto-renewal
> never fires (correct answer: it deliberately won't, because it
> can't re-issue against a CA it doesn't control).

---

## Decision tree

```
Q1: Do you already have a Microsoft Active Directory CA (ADCS)
    issuing certs to your domain devices?
│
├── YES → Scenario 1: ADCS-issued server cert
│   (devices already trust AD root CA via GPO/Intune)
│
└── NO
    │
    Q2: Do you have any other PKI in place (FreeIPA / HashiCorp Vault
    PKI / public CA / corporate self-signed CA)?
    │
    ├── YES → Scenario 3: External non-AD PKI
    │   (you'll import certs and renew via the external PKI)
    │
    └── NO  → Scenario 2: Self-contained
        (openradiusweb generates its own CA + server cert and
         auto-renews; you handle CA distribution to clients)
```

## Quick-reference table

| | Scenario 1 (AD CA) | Scenario 2 (Self-contained) | Scenario 3 (External PKI) |
|---|---|---|---|
| **Server cert source** | ADCS issues, operator imports via UI | UI Generate CA + Generate Server | External PKI issues, operator imports via UI |
| **`certificates` row shape** | `imported=true` | `imported=false` | `imported=true` |
| **PR #93 auto-renewal applies?** | ❌ No (skips imported rows by design) | ✅ Yes — fully automated 30 days before expiry | ❌ No |
| **Client trust setup** | Domain devices: GPO/Intune already pushed AD root CA. BYOD: per [employee-wifi-setup-guide.md](employee-wifi-setup-guide.md) "不要驗證 CA" or push CA via MDM | Same options, but the CA is openradiusweb's, not AD's | Same options against the external PKI's root |
| **Renewal mechanism** | ADCS workflow (Windows side) → re-import via UI / API | Background loop in gateway, no operator action | External PKI workflow → re-import via UI / API |
| **Operator burden** | Yearly ADCS request + import | Initial CA generation + client distribution; then automatic | Per external PKI's process |

---

## Scenario 1 — Microsoft AD CA (ADCS) issues the server cert

**Pick this when**: your environment is already Windows Server + AD
+ ADCS, and most clients are domain-joined or managed by Intune /
Group Policy that has pushed the AD root CA into their trust store.
This is the current MDS production deployment.

### Why this is the cleanest if you already have ADCS

Domain devices already trust the AD root CA (GPO pushed it as part of
domain join). When freeradius presents an ADCS-issued server cert
during EAP-TTLS, those devices accept it without prompting. Zero
extra client config for the domain population.

BYOD / phones don't get the AD root CA automatically. Either:
- Push CA + WiFi profile via MDM (Intune for managed iOS/Android),
- Or follow the existing [employee-wifi-setup-guide.md](employee-wifi-setup-guide.md)
  "CA 憑證: 不要驗證" workaround (TLS tunnel still encrypts the inner
  PAP — supplicant just doesn't verify the chain).

### One-time setup

1. **On the Windows AD CA server**, request a server cert using a
   template that allows the **Server Authentication** EKU. Common
   templates:
   - `Web Server` (manual approval, simplest)
   - `RAS and IAS Server` (designed for this exact use case if your
     CA admin has set it up)

   Cert details:
   - **Common Name**: `radius.<your-ad-domain>` (e.g. `radius.mds.local`)
   - **Subject Alternative Names**:
     - DNS: same as CN, plus any aliases you'll use
     - IP: the RADIUS server's IP (some supplicants check IP SAN)
   - **Validity**: 1-2 years is typical
   - **Private key**: must be exportable (mark the request accordingly,
     or use `certreq` with `-machine` and exportable key in the .inf)

2. **Export from ADCS** as PEM (or .pfx → convert to PEM):
   ```powershell
   # If you have the .pfx:
   openssl pkcs12 -in radius.pfx -out radius.pem -nokeys -clcerts
   openssl pkcs12 -in radius.pfx -out radius.key -nocerts -nodes
   # The chain (intermediate + root) is usually in the .pfx too:
   openssl pkcs12 -in radius.pfx -out radius-chain.pem -nokeys -cacerts
   ```

3. **Import into openradiusweb UI**:
   - Navigate to **Certificates → Import**
   - Cert type: `server`
   - Name: descriptive, e.g. `radius-mds-2026`
   - Cert PEM: paste `radius.pem`
   - Key PEM: paste `radius.key`
   - Chain PEM (optional but recommended): paste `radius-chain.pem`
   - Save

4. **Activate** the new cert. The watcher receives the NATS message
   and SIGHUPs freeradius.

5. **Verify** with [runbook-post-deploy-verification.md](runbook-post-deploy-verification.md)
   step 6 — phone reconnect MDS-01 should `Login OK`.

### Renewal

PR #93 auto-renewal is **deliberately skipped** for ADCS-issued certs
(they have `imported=true`, which the candidate query filters out —
we can't re-issue against a CA we don't control). Renewal options:

- **Manual** — set a calendar reminder ~60 days before expiry, repeat
  the steps above, import + activate the new cert. The previous cert
  auto-deactivates on the activation step.
- **Half-automated PowerShell** (TBD; not yet shipped) — Windows
  Task Scheduler job that calls `certreq -submit` to renew, then
  POSTs the new PEM to openradiusweb's `/certificates/import`
  endpoint. Future work.

---

## Scenario 2 — Self-contained (openradiusweb generates everything)

**Pick this when**: you have no AD CA, no other PKI, and want
openradiusweb to be the only thing managing the server cert. Common
for small offices, non-Windows environments, FreeIPA / Samba
LDAP-only deployments, or first-time pilots.

This is the scenario PR #93's auto-renewal was designed for.

### One-time setup

1. **Generate a CA** in the UI (one-time, ~10 year validity):
   - **Certificates → Generate CA**
   - Name: `openradius-root-ca` (or your org name)
   - Common Name: same
   - Validity: 3650 days (10 years)
   - Activate it

2. **Generate the server cert** signed by that CA:
   - **Certificates → Generate Server**
   - Name: `radius-server`
   - Common Name: `radius.<your-domain>` (or just an IP if no DNS)
   - SAN DNS: same as CN, plus aliases
   - SAN IPs: RADIUS server's IP
   - Validity: 730 days (2 years) is the default
   - Key size: 2048
   - Activate it

3. **Distribute the CA cert** to clients. Three options ranked by
   security:
   - **Best — MDM (Intune / Jamf / etc.)** push CA root + a WiFi
     profile in one go. Devices auto-enroll, zero user steps.
   - **Good — GPO** if you have an AD environment but no AD CA
     (rare but possible). Computer Configuration → Trusted Root
     Certification Authorities.
   - **Fallback — BYOD self-service** per
     [employee-wifi-setup-guide.md](employee-wifi-setup-guide.md).
     Users pick "CA 憑證: 不要驗證". Less secure than CA-trust because
     supplicants don't verify the chain — but the TLS tunnel itself
     still encrypts the inner PAP, so AD passwords are not leaked
     on the wire. Acceptable for many corporate WiFi deployments.

### Renewal — fully automated

Once deployed:
- The gateway runs a background task every
  `ORW_CERT_RENEW_INTERVAL_HOURS` (default 6h) — see
  [services/gateway/features/certificates/auto_renewal.py](../services/gateway/features/certificates/auto_renewal.py).
- When the active server cert is within
  `ORW_CERT_RENEW_THRESHOLD_DAYS` (default 30) of expiry, the loop
  generates a fresh server cert (named `<old>-renewed-YYYYMMDD`),
  signed by the same active CA, and activates it.
- The freeradius_config_watcher catches the activation event,
  rewrites the cert files, and SIGHUPs freeradius.
- **Clients keep working** because the new cert is signed by the
  same CA they already trust. No re-distribution required.

The CA itself does NOT auto-renew. ~10 year validity gives you a
long lead time; near expiry you have to manually generate a new CA,
re-issue all server certs against it, AND push the new CA out to
every client. This is a known gap; see "Limitations" below.

### Verifying renewal works (test plan)

To exercise the loop without waiting 700 days for the cert to
naturally approach expiry:

```bash
# 1. Set the threshold high enough that the existing cert is in scope
sudo sed -i '/^ORW_CERT_RENEW_THRESHOLD_DAYS=/d' /opt/openradiusweb/.env.production
echo 'ORW_CERT_RENEW_THRESHOLD_DAYS=3650' | sudo tee -a /opt/openradiusweb/.env.production

# 2. Recreate gateway so it re-reads .env
sudo docker compose -f docker-compose.prod.yml --env-file .env.production \
    up -d --force-recreate --no-deps gateway

# 3. Tail for the renewal pass output (~30s)
sleep 30
sudo docker logs --since 60s --timestamps orw-gateway 2>&1 | grep cert_renewal_pass_complete

# 4. Confirm a new -renewed-YYYYMMDD row appeared
sudo docker exec orw-postgres psql -U orw -d orw -A -t -c "
SELECT name, is_active FROM certificates
WHERE cert_type='server' AND name LIKE '%renewed%' ORDER BY created_at DESC LIMIT 1;
"

# 5. Phone reconnect MDS-01 → Login OK confirms the new cert serves TLS

# 6. Restore the threshold back to default
sudo sed -i '/^ORW_CERT_RENEW_THRESHOLD_DAYS=3650/d' /opt/openradiusweb/.env.production
sudo docker compose -f docker-compose.prod.yml --env-file .env.production \
    up -d --force-recreate --no-deps gateway
```

---

## Scenario 3 — External non-AD PKI

**Pick this when**: you have an existing PKI that isn't Microsoft AD CA
— FreeIPA, HashiCorp Vault PKI, smallstep CA, your org's own root CA
managed elsewhere, or even a public CA via ACME for an internet-
facing RADIUS endpoint (rare).

### One-time setup

Same shape as Scenario 1, but the cert source is whatever PKI you have:

1. **Issue a server cert** from your PKI:
   - CN + SAN as in Scenario 1
   - Server Authentication EKU
   - Exportable private key
2. **Convert to PEM** if needed (Vault PKI, ACME tools, etc.
   already give you PEM).
3. **Import into openradiusweb UI** — same fields as Scenario 1.
4. **Activate**.
5. **Distribute the PKI's root CA** to clients via MDM / GPO / manual
   install / per-client trust store. Same as Scenario 1's distribution
   options apply.

### Renewal

PR #93 auto-renewal does NOT apply (`imported=true` rows are skipped).
Renewal is whatever your PKI provides:

- **HashiCorp Vault PKI**: agents can auto-renew leaf certs. Wire a
  cron + script that calls Vault → POSTs to openradiusweb's
  `/certificates/import` endpoint.
- **smallstep CA**: `step ca renew` cron + import script.
- **ACME (Let's Encrypt etc.)**: `certbot renew` + post-hook that
  calls the import API.

Each of these is a small custom integration; not yet shipped.

---

## Migrating between scenarios

You CAN switch later, but it requires every client's trust store to
be updated. Plan for downtime / a re-enrollment campaign:

| From → To | What changes for clients |
|---|---|
| 2 → 1 | New AD CA root must be in client trust (probably already is for domain devices). Phones need re-config. |
| 2 → 3 | New external PKI's root must replace the openradiusweb CA in every trust store. |
| 1 → 2 | Domain devices stop trusting the new openradiusweb CA until GPO/MDM is updated. |
| 3 → 2 | Same shape as 1 → 2. |
| 1 → 3 / 3 → 1 | Both root CAs must be trusted during the transition. |

The cleanest migration is to run BOTH old + new CA cert-trusts on
clients for the swap window, then drop the old once everyone's
re-enrolled.

---

## Limitations + known gaps

- **CA auto-rotation is not implemented** in Scenario 2. The CA is
  10-year valid and must be manually renewed. When it does happen,
  every active server cert + every client trust store must be
  re-issued / re-pushed. This is a multi-day project; see future
  work in [docs/security-audit-2026-05-02-secret-storage.md](security-audit-2026-05-02-secret-storage.md).
- **No half-automated ADCS renewal** for Scenario 1 yet. Operator
  has to manually re-import. A PowerShell + REST POST script could
  bridge this; not yet shipped.
- **No Vault / Cloud KMS integration** for any scenario — the
  imported / generated keys are stored AES-256-GCM-encrypted in the
  `certificates.key_pem_encrypted` column with `ORW_SECRET_MASTER`
  as the KEK. Acceptable for the threat model documented in the
  audit doc.

---

## See also

- [employee-wifi-setup-guide.md](employee-wifi-setup-guide.md) —
  what end users see when connecting; references to "CA 憑證: 不要驗證"
  apply when you haven't pushed the relevant root CA via MDM.
- [runbook-key-rotation.md](runbook-key-rotation.md) — rotates
  `ORW_SECRET_MASTER` (which decrypts cert key columns); independent
  of the cert-cycle scenarios above.
- [runbook-post-deploy-verification.md](runbook-post-deploy-verification.md)
  step 6 — phone reconnect verification, applies to all three
  scenarios.
- [services/gateway/features/certificates/auto_renewal.py](../services/gateway/features/certificates/auto_renewal.py)
  — the loop body for Scenario 2.
- [security-audit-2026-05-02-secret-storage.md](security-audit-2026-05-02-secret-storage.md)
  — TLS server cert auto-rotation listed under "Still pending"
  (the cross-scenario coverage gap).
