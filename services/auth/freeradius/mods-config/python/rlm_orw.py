"""
OpenRadiusWeb FreeRADIUS Python Module (rlm_python3)

This module hooks into FreeRADIUS to:
1. Log every authentication attempt to OpenRadiusWeb database
2. Map AD/LDAP errors to human-readable failure reasons
3. Apply OpenRadiusWeb authorization policies
4. Record RADIUS session data for Access Tracker

Install: symlink to /etc/freeradius/mods-config/python/orw.py
Config in mods-available/python:
    python {
        module = orw
        python_path = /etc/freeradius/mods-config/python
        mod_authorize = authorize
        mod_post_auth = post_auth
        mod_accounting = accounting
    }
"""

import json
import os
import sys
import sysconfig
import time
import traceback

import radiusd  # FreeRADIUS built-in module


def _ensure_site_packages():
    """Inject pip- and apt-installed package paths into sys.path.

    rlm_python3 starts the embedded interpreter without running site.main(),
    so the standard `dist-packages` / `site-packages` directories are NOT on
    sys.path by default. Result: `import psycopg2` fails silently and
    HAS_DB stays False, so every auth event drops on the floor instead of
    landing in radius_auth_log. (See PR #58 commit message for the full
    debug trail; symptom is an empty radius_auth_log table while
    freeradius logs show "Login OK".)

    Inject Debian-style `dist-packages` (both pip's /usr/local and apt's
    /usr/lib trees), plus whatever sysconfig reports for this interpreter.
    Detect Python version dynamically so this keeps working when Debian
    bumps the base from 3.11 → 3.12.
    """
    pyver = "{}.{}".format(sys.version_info.major, sys.version_info.minor)
    candidates = [
        "/usr/local/lib/python{}/dist-packages".format(pyver),
        "/usr/lib/python{}/dist-packages".format(pyver),
        "/usr/lib/python3/dist-packages",
        sysconfig.get_path("purelib"),
        sysconfig.get_path("platlib"),
    ]
    for path in candidates:
        if path and path not in sys.path:
            sys.path.insert(0, path)


_ensure_site_packages()

# Try to import database libraries (fail gracefully if not available during testing)
try:
    import psycopg2
    import psycopg2.extras
    HAS_DB = True
except ImportError:
    HAS_DB = False

try:
    import nats
    HAS_NATS = True
except ImportError:
    HAS_NATS = False

try:
    import ldap3
    HAS_LDAP3 = True
except ImportError:
    HAS_LDAP3 = False


# ============================================================
# Configuration
# ============================================================
DB_URL = os.environ.get("ORW_DB_URL", "")
NATS_URL = os.environ.get("ORW_NATS_URL", "nats://localhost:4222")
TENANT_ID = os.environ.get("ORW_TENANT_ID") or None  # docker compose passes "" (empty) when unset; "" fails UUID cast

# AD Error Code to Failure Reason Mapping
# These codes appear in LDAP/AD bind responses and MS-CHAP errors
AD_ERROR_MAP = {
    # AD Extended Error Codes (from LDAP bind failure data field)
    "525": ("AD_MACHINE_NOT_FOUND", "User/computer not found in AD"),
    "52e": ("AD_INVALID_CREDENTIALS", "Invalid credentials - wrong password"),
    "530": ("AD_LOGON_HOURS", "Not permitted to logon at this time"),
    "531": ("AD_LOGON_WORKSTATION", "Not permitted to logon from this workstation"),
    "532": ("AD_PASSWORD_EXPIRED", "Password has expired"),
    "533": ("AD_ACCOUNT_DISABLED", "Account is disabled"),
    "701": ("AD_ACCOUNT_EXPIRED", "Account has expired"),
    "773": ("AD_PASSWORD_MUST_CHANGE", "User must change password before first logon"),
    "775": ("AD_ACCOUNT_LOCKED", "Account is locked out"),

    # MS-CHAP Error Codes (E= values)
    "691": ("AD_INVALID_CREDENTIALS", "Authentication failure - wrong password or username"),
    "646": ("AD_ACCOUNT_DISABLED", "Account restriction - account disabled or locked"),
    "647": ("AD_ACCOUNT_EXPIRED", "Account expired"),
    "648": ("AD_LOGON_HOURS", "Logon hours restriction"),
    "649": ("AD_LOGON_WORKSTATION", "Workstation restriction"),
}

# EAP Method ID to Name
EAP_METHOD_MAP = {
    1: "Identity",
    4: "MD5-Challenge",
    6: "GTC",
    13: "EAP-TLS",
    21: "EAP-TTLS",
    25: "PEAP",
    26: "MSCHAPv2",
    43: "EAP-FAST",
}

_db_conn = None


def _get_db():
    """Get or create database connection."""
    global _db_conn
    if not HAS_DB:
        return None
    try:
        if _db_conn is None or _db_conn.closed:
            _db_conn = psycopg2.connect(DB_URL)
            _db_conn.autocommit = True
        return _db_conn
    except Exception as e:
        radiusd.radlog(radiusd.L_ERR, f"OpenRadiusWeb DB connection failed: {e}")
        return None


def _extract_attrs(request_pairs):
    """Convert FreeRADIUS attribute tuples to dict."""
    attrs = {}
    for attr_name, attr_value in request_pairs:
        if attr_name in attrs:
            if isinstance(attrs[attr_name], list):
                attrs[attr_name].append(attr_value)
            else:
                attrs[attr_name] = [attrs[attr_name], attr_value]
        else:
            attrs[attr_name] = attr_value
    return attrs


def _detect_auth_method(request, reply):
    """Detect the authentication method used."""
    eap_type = request.get("EAP-Type")
    if eap_type:
        return EAP_METHOD_MAP.get(int(eap_type) if eap_type.isdigit() else 0, f"EAP-{eap_type}")

    # Check for MAB (MAC Authentication Bypass)
    calling_id = request.get("Calling-Station-Id", "")
    username = request.get("User-Name", "")
    if calling_id and username:
        # MAB: username == MAC address (in some format)
        mac_clean = calling_id.replace(":", "").replace("-", "").replace(".", "").lower()
        user_clean = username.replace(":", "").replace("-", "").replace(".", "").lower()
        if mac_clean == user_clean:
            return "MAB"

    service_type = request.get("Service-Type")
    if service_type == "Call-Check":
        return "MAB"

    return "PAP"  # Default


def _detect_failure_reason(request, reply, auth_result):
    """
    Detect the specific reason for authentication failure.
    Maps AD errors, certificate issues, and RADIUS errors to catalog entries.
    """
    if auth_result == "success":
        return None, None, None

    reply_message = reply.get("Reply-Message", "")
    module_fail = reply.get("Module-Failure-Message", "")
    eap_message = reply.get("EAP-Message", "")

    failure_reason = None
    ad_error_code = None
    ad_error_message = None

    # Check for AD/LDAP error codes in various places
    error_sources = [reply_message, module_fail, str(reply)]

    for source in error_sources:
        if not source:
            continue
        source_lower = source.lower()

        # Look for AD extended error codes (e.g., "data 775" = locked)
        for code, (reason, desc) in AD_ERROR_MAP.items():
            if code in source or f"data {code}" in source_lower:
                failure_reason = desc
                ad_error_code = reason
                ad_error_message = source
                return failure_reason, ad_error_code, ad_error_message

        # MS-CHAP errors (E=691, E=646, etc.)
        if "E=" in source:
            for code, (reason, desc) in AD_ERROR_MAP.items():
                if f"E={code}" in source:
                    failure_reason = desc
                    ad_error_code = reason
                    ad_error_message = source
                    return failure_reason, ad_error_code, ad_error_message

        # Certificate errors
        if "certificate" in source_lower:
            if "expired" in source_lower:
                return "Client certificate expired", "CERT_EXPIRED", source
            elif "revoked" in source_lower:
                return "Client certificate revoked", "CERT_REVOKED", source
            elif "unknown ca" in source_lower or "untrusted" in source_lower:
                return "Client certificate from untrusted CA", "CERT_NOT_TRUSTED", source
            elif "verify" in source_lower:
                return "Certificate verification failed", "CERT_VERIFY_FAILED", source

        # EAP errors
        if "eap" in source_lower:
            if "timeout" in source_lower:
                return "EAP conversation timed out", "EAP_TIMEOUT", source
            elif "method" in source_lower and ("reject" in source_lower or "not found" in source_lower):
                return "EAP method not supported", "EAP_METHOD_MISMATCH", source

        # LDAP connection errors
        if "ldap" in source_lower and ("connect" in source_lower or "bind" in source_lower):
            return "Cannot connect to AD/LDAP", "AD_CONNECT_FAILED", source

        # Shared secret
        if "shared secret" in source_lower or "authenticator" in source_lower:
            return "RADIUS shared secret mismatch", "SHARED_SECRET_MISMATCH", source

    # Generic failure
    if module_fail:
        return module_fail, None, None
    if reply_message:
        return reply_message, None, None

    return "Authentication rejected", None, None


def _extract_cert_info(request):
    """Extract client certificate information from TLS attributes."""
    cert_info = {}
    cert_cn = request.get("TLS-Client-Cert-Common-Name")
    if cert_cn:
        cert_info["client_cert_cn"] = cert_cn
    cert_issuer = request.get("TLS-Client-Cert-Issuer")
    if cert_issuer:
        cert_info["client_cert_issuer"] = cert_issuer
    cert_serial = request.get("TLS-Client-Cert-Serial")
    if cert_serial:
        cert_info["client_cert_serial"] = cert_serial
    cert_expiry = request.get("TLS-Client-Cert-Not-After")
    if cert_expiry:
        cert_info["client_cert_expiry"] = cert_expiry
    server_cn = request.get("TLS-Cert-Common-Name")
    if server_cn:
        cert_info["server_cert_cn"] = server_cn
    return cert_info


def _log_auth_to_db(request, reply, auth_result, processing_time_ms):
    """Write authentication attempt to database."""
    conn = _get_db()
    if not conn:
        return

    try:
        auth_method = _detect_auth_method(request, reply)
        failure_reason, ad_error_code, ad_error_message = _detect_failure_reason(
            request, reply, auth_result
        )
        cert_info = _extract_cert_info(request)

        # Extract VLAN from Tunnel attributes
        assigned_vlan = None
        tunnel_pvid = reply.get("Tunnel-Private-Group-Id")
        if tunnel_pvid:
            try:
                assigned_vlan = int(tunnel_pvid)
            except (ValueError, TypeError):
                pass

        # Determine username
        username = request.get("User-Name")
        user_domain = None
        if username and "\\" in username:
            user_domain, username_short = username.split("\\", 1)
        elif username and "@" in username:
            username_short, user_domain = username.rsplit("@", 1)
        else:
            username_short = username

        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO radius_auth_log (
                    session_id, request_type, auth_result, auth_method, eap_type,
                    failure_reason, failure_code, ad_error_code, ad_error_message,
                    radius_reply_message,
                    calling_station_id, username, user_domain,
                    nas_ip, nas_port, nas_port_type, nas_port_id, nas_identifier,
                    assigned_vlan, filter_id,
                    client_cert_cn, client_cert_issuer, client_cert_serial,
                    client_cert_expiry, server_cert_cn,
                    service_type, framed_ip, source_ip,
                    processing_time_ms, policy_matched,
                    request_attributes, response_attributes,
                    realm_name, tenant_id
                ) VALUES (
                    %(session_id)s, %(request_type)s, %(auth_result)s, %(auth_method)s,
                    %(eap_type)s, %(failure_reason)s, %(failure_code)s,
                    %(ad_error_code)s, %(ad_error_message)s, %(radius_reply_message)s,
                    %(calling_station_id)s, %(username)s, %(user_domain)s,
                    %(nas_ip)s, %(nas_port)s, %(nas_port_type)s, %(nas_port_id)s,
                    %(nas_identifier)s, %(assigned_vlan)s, %(filter_id)s,
                    %(client_cert_cn)s, %(client_cert_issuer)s, %(client_cert_serial)s,
                    %(client_cert_expiry)s, %(server_cert_cn)s,
                    %(service_type)s, %(framed_ip)s, %(source_ip)s,
                    %(processing_time_ms)s, %(policy_matched)s,
                    %(request_attributes)s, %(response_attributes)s,
                    %(realm_name)s, %(tenant_id)s
                )
            """, {
                "session_id": request.get("Acct-Session-Id"),
                "request_type": "Access-Accept" if auth_result == "success" else "Access-Reject",
                "auth_result": auth_result,
                "auth_method": auth_method,
                "eap_type": request.get("EAP-Type"),
                "failure_reason": failure_reason,
                "failure_code": None,
                "ad_error_code": ad_error_code,
                "ad_error_message": ad_error_message,
                "radius_reply_message": reply.get("Reply-Message"),
                "calling_station_id": request.get("Calling-Station-Id"),
                "username": username,
                "user_domain": user_domain,
                "nas_ip": request.get("NAS-IP-Address"),
                "nas_port": request.get("NAS-Port"),
                "nas_port_type": request.get("NAS-Port-Type"),
                "nas_port_id": request.get("NAS-Port-Id"),
                "nas_identifier": request.get("NAS-Identifier"),
                "assigned_vlan": assigned_vlan,
                "filter_id": reply.get("Filter-Id"),
                "client_cert_cn": cert_info.get("client_cert_cn"),
                "client_cert_issuer": cert_info.get("client_cert_issuer"),
                "client_cert_serial": cert_info.get("client_cert_serial"),
                "client_cert_expiry": cert_info.get("client_cert_expiry"),
                "server_cert_cn": cert_info.get("server_cert_cn"),
                "service_type": request.get("Service-Type"),
                "framed_ip": reply.get("Framed-IP-Address"),
                "source_ip": request.get("Packet-Src-IP-Address"),
                "processing_time_ms": processing_time_ms,
                "policy_matched": reply.get("OpenRadiusWeb-Policy-Name"),
                "request_attributes": json.dumps(
                    {k: str(v) for k, v in request.items()}, default=str
                ),
                "response_attributes": json.dumps(
                    {k: str(v) for k, v in reply.items()}, default=str
                ),
                "realm_name": user_domain,
                "tenant_id": TENANT_ID,
            })

    except Exception as e:
        radiusd.radlog(radiusd.L_ERR, f"OpenRadiusWeb auth log failed: {e}")
        traceback.print_exc()


# ============================================================
# FreeRADIUS Hook Functions
# ============================================================

def instantiate(p):
    """Called when the module is loaded."""
    radiusd.radlog(radiusd.L_INFO, "OpenRadiusWeb rlm_python module loaded")
    # Test database connection
    conn = _get_db()
    if conn:
        radiusd.radlog(radiusd.L_INFO, "OpenRadiusWeb database connected")
    else:
        radiusd.radlog(radiusd.L_WARN, "OpenRadiusWeb database NOT connected - logging disabled")
    return radiusd.RLM_MODULE_OK


def _normalize_mac(raw_mac):
    """Normalize MAC address to lowercase colon-separated format."""
    # Handle Cisco format: aabb.ccdd.eeff
    if "." in raw_mac and len(raw_mac) == 14:
        raw_mac = raw_mac.replace(".", "")
        return ":".join(raw_mac[i:i+2].lower() for i in range(0, 12, 2))
    # Strip all separators
    raw = raw_mac.replace(":", "").replace("-", "").replace(".", "").lower()
    if len(raw) == 12:
        return ":".join(raw[i:i+2] for i in range(0, 12, 2))
    return raw_mac.lower()


def _lookup_mab_device(mac_clean):
    """Look up an enabled, non-expired MAB device by MAC.

    Returns the row as a dict (RealDictCursor) or None if not found / on
    error. Used by both:
    - authorize() for true MAB requests (Service-Type=Call-Check)
    - post_auth() to apply per-device VLAN overrides on successful 802.1X
      authentication, so the same mab_devices entry can both whitelist
      a device for MAB and pin a VLAN for users on a WPA2-Enterprise SSID.
    """
    conn = _get_db()
    if not conn:
        return None
    try:
        tenant_id = os.environ.get("ORW_TENANT_ID")
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if tenant_id:
                cur.execute(
                    "SELECT * FROM mab_devices "
                    "WHERE mac_address = %s::macaddr "
                    "AND enabled = true "
                    "AND (expiry_date IS NULL OR expiry_date > NOW()) "
                    "AND tenant_id = %s",
                    (mac_clean, tenant_id),
                )
            else:
                cur.execute(
                    "SELECT * FROM mab_devices "
                    "WHERE mac_address = %s::macaddr "
                    "AND enabled = true "
                    "AND (expiry_date IS NULL OR expiry_date > NOW())",
                    (mac_clean,),
                )
            return cur.fetchone()
    except Exception as e:
        radiusd.radlog(
            radiusd.L_ERR,
            f"OpenRadiusWeb MAB device lookup error: {e}",
        )
        return None


def authorize(p):
    """
    Called during the authorize phase.
    Detects realm from User-Name. For MAB requests, checks the MAB whitelist
    and returns Auth-Type=Accept with VLAN attributes if approved.
    """
    request = _extract_attrs(p)
    username = request.get("User-Name", "")
    calling_station_id = request.get("Calling-Station-Id", "")

    # Detect realm
    realm = None
    if "@" in username:
        _, realm = username.rsplit("@", 1)
    elif "\\" in username:
        realm, _ = username.split("\\", 1)

    radiusd.radlog(
        radiusd.L_DBG,
        f"OpenRadiusWeb authorize: user={username} "
        f"mac={calling_station_id} "
        f"nas={request.get('NAS-IP-Address')} "
        f"realm={realm}"
    )

    # Detect if this is a MAB request
    auth_method = _detect_auth_method(request, {})

    if auth_method == "MAB" and calling_station_id:
        mac_clean = _normalize_mac(calling_station_id)
        radiusd.radlog(radiusd.L_INFO,
                       f"OpenRadiusWeb MAB request: {mac_clean}")

        mab_device = _lookup_mab_device(mac_clean)
        if mab_device:
            vlan_id = mab_device.get("assigned_vlan_id")
            dev_name = mab_device.get("name", "unknown")
            radiusd.radlog(radiusd.L_INFO,
                           f"OpenRadiusWeb MAB approved: {mac_clean} "
                           f"({dev_name}) -> VLAN {vlan_id}")

            reply_attrs = []
            if vlan_id:
                reply_attrs.extend([
                    ("Tunnel-Type", "VLAN"),
                    ("Tunnel-Medium-Type", "IEEE-802"),
                    ("Tunnel-Private-Group-Id", str(vlan_id)),
                ])

            control_attrs = [("Auth-Type", "Accept")]
            if realm:
                control_attrs.append(("OpenRadiusWeb-Realm", realm))

            return (radiusd.RLM_MODULE_OK,
                    tuple(reply_attrs),
                    tuple(control_attrs))
        else:
            radiusd.radlog(radiusd.L_INFO,
                           f"OpenRadiusWeb MAB not in whitelist: {mac_clean}")

    # Non-MAB or MAB-not-found: existing behavior
    if realm:
        return (radiusd.RLM_MODULE_OK,
                (),
                (("OpenRadiusWeb-Realm", realm),))

    return radiusd.RLM_MODULE_OK


def _get_user_ldap_groups(username, user_domain):
    """Query LDAP for user's group memberships.

    Returns a list of group names (CNs) or empty list on failure.
    """
    if not HAS_LDAP3:
        radiusd.radlog(radiusd.L_DBG,
                       "OpenRadiusWeb: ldap3 not available, skipping group lookup")
        return []

    conn = _get_db()
    if not conn:
        return []

    try:
        # Get LDAP server config from DB
        tenant_id = os.environ.get("ORW_TENANT_ID")
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if tenant_id:
                cur.execute(
                    "SELECT host, port, use_tls, use_starttls, "
                    "bind_dn, bind_password_encrypted, base_dn, "
                    "user_search_base, user_search_filter, "
                    "group_membership_attr "
                    "FROM ldap_servers WHERE enabled = true "
                    "AND tenant_id = %s ORDER BY priority ASC LIMIT 1",
                    (tenant_id,)
                )
            else:
                cur.execute(
                    "SELECT host, port, use_tls, use_starttls, "
                    "bind_dn, bind_password_encrypted, base_dn, "
                    "user_search_base, user_search_filter, "
                    "group_membership_attr "
                    "FROM ldap_servers WHERE enabled = true "
                    "ORDER BY priority ASC LIMIT 1"
                )
            ldap_cfg = cur.fetchone()

        if not ldap_cfg:
            radiusd.radlog(radiusd.L_DBG,
                           "OpenRadiusWeb: no LDAP server configured")
            return []

        # Build LDAP URI
        host = ldap_cfg["host"]
        port = ldap_cfg["port"] or (636 if ldap_cfg["use_tls"] else 389)
        use_ssl = bool(ldap_cfg["use_tls"])

        server = ldap3.Server(host, port=port, use_ssl=use_ssl,
                              get_info=ldap3.NONE,
                              connect_timeout=5)

        # Bind with service account
        bind_dn = ldap_cfg["bind_dn"]
        bind_pw = ldap_cfg["bind_password_encrypted"] or ""

        ldap_conn = ldap3.Connection(server, user=bind_dn, password=bind_pw,
                                     auto_bind=True, receive_timeout=10)

        if ldap_cfg["use_starttls"] and not use_ssl:
            ldap_conn.start_tls()

        # Search for user
        search_base = ldap_cfg["user_search_base"] or ldap_cfg["base_dn"]
        group_attr = ldap_cfg["group_membership_attr"] or "memberOf"

        # Strip domain from username for search
        search_user = username
        if "\\" in search_user:
            _, search_user = search_user.split("\\", 1)
        elif "@" in search_user:
            search_user, _ = search_user.rsplit("@", 1)

        # Use configured filter or default sAMAccountName
        search_filter = ldap_cfg.get("user_search_filter") or "(sAMAccountName={username})"
        search_filter = search_filter.replace(
            "%{%{Stripped-User-Name}:-%{User-Name}}", search_user
        ).replace("{username}", search_user)

        ldap_conn.search(
            search_base=search_base,
            search_filter=search_filter,
            search_scope=ldap3.SUBTREE,
            attributes=[group_attr],
        )

        groups = []
        if ldap_conn.entries:
            entry = ldap_conn.entries[0]
            member_of = entry[group_attr].values if group_attr in entry else []
            for dn in member_of:
                # Extract CN from DN: "CN=IT-Staff,OU=Groups,DC=corp,DC=local"
                dn_str = str(dn)
                for part in dn_str.split(","):
                    part = part.strip()
                    if part.upper().startswith("CN="):
                        groups.append(part[3:])
                        break

        ldap_conn.unbind()
        radiusd.radlog(radiusd.L_DBG,
                       f"OpenRadiusWeb LDAP groups for {search_user}: {groups}")
        return groups

    except Exception as e:
        radiusd.radlog(radiusd.L_ERR,
                       f"OpenRadiusWeb LDAP group lookup failed: {e}")
        return []


def _lookup_vlan_for_groups(groups):
    """Look up VLAN assignment from group_vlan_mappings table.

    Returns (vlan_id, group_name) tuple or (None, None) if no match.
    """
    if not groups:
        return None, None

    conn = _get_db()
    if not conn:
        return None, None

    try:
        tenant_id = os.environ.get("ORW_TENANT_ID")
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if tenant_id:
                cur.execute(
                    "SELECT group_name, vlan_id FROM group_vlan_mappings "
                    "WHERE group_name = ANY(%s) "
                    "AND enabled = true AND tenant_id = %s "
                    "ORDER BY priority ASC LIMIT 1",
                    (groups, tenant_id)
                )
            else:
                cur.execute(
                    "SELECT group_name, vlan_id FROM group_vlan_mappings "
                    "WHERE group_name = ANY(%s) "
                    "AND enabled = true "
                    "ORDER BY priority ASC LIMIT 1",
                    (groups,)
                )
            row = cur.fetchone()
            if row:
                return row["vlan_id"], row["group_name"]
    except Exception as e:
        radiusd.radlog(radiusd.L_ERR,
                       f"OpenRadiusWeb VLAN lookup failed: {e}")

    return None, None


def post_auth(p):
    """
    Called after authentication completes (success or failure).
    Logs the auth attempt and performs Dynamic VLAN Assignment on success.

    VLAN assignment precedence (first match wins):
    1. Per-device override (mab_devices.assigned_vlan_id keyed by
       Calling-Station-Id). Lets WPA2-Enterprise SSIDs pin a VLAN by
       device MAC even when 802.1X authenticates a real user account
       (you can't do classical MAB on WPA2-Enterprise — the supplicant
       has to do EAP — so the mab_devices table doubles as a per-MAC
       VLAN table here).
    2. Group-based mapping (group_vlan_mappings keyed by AD memberOf).
       Requires LDAP group lookup for the authenticated user.

    On match, return Tunnel-Type/Tunnel-Medium-Type/
    Tunnel-Private-Group-Id and the NAS assigns the port/STA to that VLAN.
    """
    start_time = time.time()
    request = _extract_attrs(p)

    # Determine auth result from Post-Auth-Type
    post_auth_type = request.get("Post-Auth-Type", "")
    if post_auth_type == "Reject" or post_auth_type == "REJECT":
        auth_result = "reject"
    else:
        auth_result = "success"

    # Build reply dict from config items
    reply = {}

    # VLAN Assignment on successful authentication
    reply_attrs = []
    if auth_result == "success":
        username = request.get("User-Name", "")
        auth_method = _detect_auth_method(request, {})
        calling_mac = request.get("Calling-Station-Id", "")

        # Skip the MAB authorize path entirely (handled there). For 802.1X,
        # check per-device override first, then fall back to group-based.
        if auth_method != "MAB" and username:
            vlan_id = None
            vlan_source = None  # for logging

            # Priority 1: per-MAC override from mab_devices table
            if calling_mac:
                mac_clean = _normalize_mac(calling_mac)
                mab_device = _lookup_mab_device(mac_clean)
                if mab_device and mab_device.get("assigned_vlan_id"):
                    vlan_id = mab_device["assigned_vlan_id"]
                    vlan_source = (
                        f"per-MAC override (device="
                        f"{mab_device.get('name', 'unnamed')}, "
                        f"mac={mac_clean})"
                    )

            # Priority 2: group-based mapping (only if no per-MAC match)
            if vlan_id is None:
                user_domain = None
                if "\\" in username:
                    user_domain, _ = username.split("\\", 1)
                elif "@" in username:
                    _, user_domain = username.rsplit("@", 1)

                groups = _get_user_ldap_groups(username, user_domain)
                if groups:
                    vlan_id, matched_group = _lookup_vlan_for_groups(groups)
                    if vlan_id:
                        vlan_source = f"AD group {matched_group}"

            if vlan_id:
                reply_attrs = [
                    ("Tunnel-Type", "VLAN"),
                    ("Tunnel-Medium-Type", "IEEE-802"),
                    ("Tunnel-Private-Group-Id", str(vlan_id)),
                ]
                reply["Tunnel-Private-Group-Id"] = str(vlan_id)
                radiusd.radlog(
                    radiusd.L_INFO,
                    f"OpenRadiusWeb VLAN assigned: user={username} "
                    f"source={vlan_source} -> VLAN {vlan_id}"
                )

    processing_time_ms = int((time.time() - start_time) * 1000)

    # Log to database (non-blocking in production, sync here for simplicity)
    _log_auth_to_db(request, reply, auth_result, processing_time_ms)

    radiusd.radlog(
        radiusd.L_INFO,
        f"OpenRadiusWeb post_auth: user={request.get('User-Name')} "
        f"mac={request.get('Calling-Station-Id')} "
        f"result={auth_result}"
        f"{' vlan=' + reply.get('Tunnel-Private-Group-Id', '') if reply.get('Tunnel-Private-Group-Id') else ''}"
    )

    if reply_attrs:
        return (radiusd.RLM_MODULE_UPDATED,
                tuple(reply_attrs),
                ())

    return radiusd.RLM_MODULE_OK


def accounting(p):
    """
    Called for RADIUS accounting packets.
    Updates session status (start/stop/interim-update).
    """
    request = _extract_attrs(p)
    acct_type = request.get("Acct-Status-Type", "")
    session_id = request.get("Acct-Session-Id")

    radiusd.radlog(
        radiusd.L_DBG,
        f"OpenRadiusWeb accounting: type={acct_type} session={session_id}"
    )

    return radiusd.RLM_MODULE_OK


def detach(_p=None):
    """Called when the module is unloaded."""
    global _db_conn
    if _db_conn:
        _db_conn.close()
        _db_conn = None
    radiusd.radlog(radiusd.L_INFO, "OpenRadiusWeb rlm_python module unloaded")
    return radiusd.RLM_MODULE_OK
