"""
FreeRADIUS Configuration Manager - Generate FreeRADIUS configs from database.

Reads LDAP servers, RADIUS realms, NAS clients, and certificates from
PostgreSQL and renders Jinja2 templates into FreeRADIUS configuration files.
Tracks config state (hashes) in the freeradius_config table so the watcher
can detect drift and re-apply.

Can be run standalone:
    python freeradius_config_manager.py --generate-and-apply
"""

import argparse
import hashlib
import os
import subprocess
import sys
from datetime import datetime, timezone
from typing import Optional

import psycopg2
import psycopg2.extras
from jinja2 import Environment, FileSystemLoader


# ============================================================
# Capability detection
# ============================================================

def _rlm_python3_available() -> bool:
    """Return True iff this freeradius binary has rlm_python3 compiled in.

    Some freeradius distributions (incl. the upstream freeradius/freeradius-server
    Docker image) don't ship rlm_python3. Without it, declaring `python3 orw {…}`
    in any module/site config makes radiusd refuse to start with
    "Failed to find python3 module". Skip generating that config when missing.
    """
    try:
        out = subprocess.run(
            ["radiusd", "-v"],
            capture_output=True, text=True, timeout=5,
        )
        return "rlm_python3" in (out.stdout + out.stderr)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# ============================================================
# FreeRADIUS Configuration Manager
# ============================================================

class FreeRADIUSConfigManager:
    """Generate and apply FreeRADIUS configuration from database state."""

    def __init__(
        self,
        db_url: str,
        template_dir: str,
        output_dir: str,
        cert_dir: str,
    ):
        self.db_url = db_url
        self.template_dir = template_dir
        self.output_dir = output_dir
        self.cert_dir = cert_dir

        self._jinja_env = Environment(
            loader=FileSystemLoader(template_dir),
            keep_trailing_newline=True,
            trim_blocks=True,
            lstrip_blocks=True,
        )

    # ----------------------------------------------------------
    # Database helpers
    # ----------------------------------------------------------

    def _get_conn(self) -> psycopg2.extensions.connection:
        """Create a new psycopg2 connection from the DB URL."""
        conn = psycopg2.connect(self.db_url)
        conn.autocommit = False
        return conn

    def _fetch_all(
        self, query: str, params: Optional[dict] = None,
    ) -> list[dict]:
        """Execute a query and return all rows as dicts."""
        conn = self._get_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(query, params or {})
                rows = cur.fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def _fetch_one(
        self, query: str, params: Optional[dict] = None,
    ) -> Optional[dict]:
        """Execute a query and return the first row as a dict, or None."""
        conn = self._get_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(query, params or {})
                row = cur.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    # ----------------------------------------------------------
    # Data loaders
    # ----------------------------------------------------------

    def _load_active_certificates(self) -> list[dict]:
        """Load all active and enabled certificates."""
        return self._fetch_all(
            "SELECT * FROM certificates "
            "WHERE is_active = true AND enabled = true "
            "ORDER BY cert_type, name"
        )

    def _load_ldap_servers(self) -> list[dict]:
        """Load all enabled LDAP servers ordered by priority."""
        return self._fetch_all(
            "SELECT * FROM ldap_servers "
            "WHERE enabled = true "
            "ORDER BY priority, name"
        )

    def _load_realms(self) -> list[dict]:
        """Load all enabled RADIUS realms ordered by priority."""
        return self._fetch_all(
            "SELECT r.*, ls.name AS ldap_server_name, ls.host AS ldap_host "
            "FROM radius_realms r "
            "LEFT JOIN ldap_servers ls ON r.ldap_server_id = ls.id "
            "WHERE r.enabled = true "
            "ORDER BY r.priority, r.name"
        )

    def _load_nas_clients(self) -> list[dict]:
        """Load all enabled NAS clients."""
        return self._fetch_all(
            "SELECT * FROM radius_nas_clients "
            "WHERE enabled = true "
            "ORDER BY name"
        )

    def _load_system_settings(self, category: str) -> dict[str, str]:
        """Load system settings for a given category as a key-value dict.

        Schema columns are `setting_key` and `setting_value` (per
        migrations/002_settings_radius_features.sql); aliased to k/v here so
        the dict-comp below stays readable.
        """
        rows = self._fetch_all(
            "SELECT setting_key AS k, setting_value AS v FROM system_settings "
            "WHERE category = %(category)s",
            {"category": category},
        )
        return {r["k"]: r["v"] for r in rows}

    # ----------------------------------------------------------
    # Config generators
    # ----------------------------------------------------------

    def generate_all_configs(self) -> dict[str, str]:
        """
        Generate all FreeRADIUS configuration files from database state.

        Capability detection: site templates are rendered with has_eap /
        has_python / ldap_modules flags, so they only reference modules
        that were actually generated. This prevents radiusd from refusing
        to parse a site that references a missing module (e.g. eap when
        no server cert is configured).

        Returns:
            Dict mapping filename (relative to output_dir) to rendered content.
        """
        configs: dict[str, str] = {}

        # === Capability detection (compute first, pass to site templates) ===

        # EAP — only generate if active CA + server cert exist (template
        # references real cert files, radiusd would crash if they don't).
        eap_content = self.generate_eap_config()
        has_eap = bool(eap_content)
        if has_eap:
            configs["mods-available/eap"] = eap_content

        # LDAP — one config per enabled server; collect module names so the
        # site template can iterate over them.
        ldap_module_names: list[str] = []
        for filename, content in self.generate_ldap_configs():
            configs[f"mods-available/{filename}"] = content
            # Strip extension if present; ldap_configs returns "ldap_<name>"
            ldap_module_names.append(filename)

        # Python — rlm_python3 may or may not be available. Detect at runtime
        # (the freeradius/freeradius-server:3.2.3 image does NOT bundle
        # rlm_python3 by default). If missing, skip python config generation
        # entirely; site templates handle has_python=False.
        has_python = _rlm_python3_available()
        if has_python:
            python_content = self.generate_python_config()
            if python_content:
                configs["mods-available/python"] = python_content
            else:
                has_python = False  # template render failed; treat as missing

        # Realms — used by inner-tunnel for proxy
        realms = self._load_realms()
        realms_enabled = bool(realms)

        # === Static configs (no capability dependencies) ===

        proxy_content = self.generate_proxy_config()
        if proxy_content:
            configs["proxy.conf"] = proxy_content

        clients_content = self.generate_clients_config()
        if clients_content:
            configs["clients.conf"] = clients_content

        # === Site configs (require capability flags) ===

        default_content = self.generate_site_default(
            has_eap=has_eap,
            has_python=has_python,
            ldap_modules=ldap_module_names,
            realms_enabled=realms_enabled,
        )
        if default_content:
            configs["sites-available/default"] = default_content

        # inner-tunnel only makes sense when EAP is enabled — it handles
        # the inner phase of PEAP / EAP-TTLS. Skip generation entirely if
        # no EAP, otherwise radiusd loads a useless site.
        if has_eap:
            inner_tunnel_content = self.generate_site_inner_tunnel(
                has_python=has_python,
                ldap_modules=ldap_module_names,
                realms_enabled=realms_enabled,
            )
            if inner_tunnel_content:
                configs["sites-available/inner-tunnel"] = inner_tunnel_content

        return configs

    def generate_eap_config(self) -> str:
        """
        Generate mods-available/eap from active certificates and RADIUS settings.

        Uses the eap.j2 template with cert paths and TLS settings.
        """
        certs = self._load_active_certificates()
        radius_settings = self._load_system_settings("radius")

        # Find active CA and server certs
        ca_cert = next((c for c in certs if c["cert_type"] == "ca"), None)
        server_cert = next((c for c in certs if c["cert_type"] == "server"), None)

        if not ca_cert or not server_cert:
            print(
                "[config-manager] WARNING: No active CA or server certificate found. "
                "EAP config will use placeholder paths."
            )

        # Build cert file paths (where write_cert_files will place them)
        ca_cert_path = os.path.join(self.cert_dir, "ca.pem")
        server_cert_path = os.path.join(self.cert_dir, "server.pem")
        server_key_path = os.path.join(self.cert_dir, "server.key")
        dh_file = os.path.join(self.cert_dir, "dh.pem")

        # Check if DH params are available
        has_dh = False
        if server_cert and server_cert.get("dh_params_pem"):
            has_dh = True
        elif ca_cert and ca_cert.get("dh_params_pem"):
            has_dh = True

        template_vars = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "ca_cert_path": ca_cert_path,
            "ca_path": os.path.join(self.cert_dir, "trusted-cas/"),
            "server_cert_path": server_cert_path,
            "server_key_path": server_key_path,
            "dh_file": dh_file if has_dh else None,
            "default_eap_type": radius_settings.get("default_eap_type", "peap"),
            "timer_expire": 60,
            "max_sessions": 4096,
            "tls_min_version": radius_settings.get("tls_min_version", "1.2"),
            "tls_max_version": "1.3",
            "cipher_list": "DEFAULT",
            "cache_lifetime": 24,
            "cache_max_entries": 255,
        }

        try:
            template = self._jinja_env.get_template("eap.j2")
            return template.render(**template_vars)
        except Exception as e:
            print(f"[config-manager] ERROR rendering eap.j2: {e}")
            return ""

    def generate_ldap_configs(self) -> list[tuple[str, str]]:
        """
        Generate mods-available/ldap_<name> for each enabled LDAP server.

        Returns:
            List of (filename, content) tuples.
        """
        servers = self._load_ldap_servers()
        if not servers:
            return []

        # Prepare template variables for each server
        rendered_servers = []
        for server in servers:
            # Build a safe module name from the server name
            safe_name = (
                server["name"]
                .lower()
                .replace(" ", "_")
                .replace("-", "_")
                .replace(".", "_")
            )
            module_name = f"ldap_{safe_name}"

            # Build TLS CA cert path if CA cert data is available
            tls_ca_cert_path = None
            if server.get("tls_ca_cert"):
                tls_ca_cert_path = os.path.join(
                    self.cert_dir, "ldap", f"{safe_name}_ca.pem"
                )

            rendered_servers.append({
                "module_name": module_name,
                "host": server["host"],
                "port": server["port"],
                "bind_dn": server["bind_dn"],
                "bind_password": server.get("bind_password_encrypted", ""),
                "base_dn": server["base_dn"],
                "user_search_base": server.get("user_search_base") or server["base_dn"],
                "user_search_filter": server.get(
                    "user_search_filter",
                    "(sAMAccountName=%{%{Stripped-User-Name}:-%{User-Name}})",
                ),
                "group_search_base": server.get("group_search_base") or server["base_dn"],
                "group_search_filter": server.get(
                    "group_search_filter",
                    "(member=%{control:Ldap-UserDn})",
                ),
                "group_membership_attr": server.get("group_membership_attr", "memberOf"),
                "use_tls": server.get("use_tls", False),
                "use_starttls": server.get("use_starttls", False),
                "tls_ca_cert_path": tls_ca_cert_path,
                "tls_require_cert": (
                    server.get("tls_require_cert", "demand")
                    if server.get("tls_require_cert") is not None
                    else "demand"
                ),
                "connect_timeout_seconds": server.get("connect_timeout_seconds", 5),
                "search_timeout_seconds": server.get("search_timeout_seconds", 10),
                "idle_timeout_seconds": server.get("idle_timeout_seconds", 60),
            })

        try:
            template = self._jinja_env.get_template("ldap.j2")
            # The ldap.j2 template iterates over ldap_servers, rendering all
            # servers into a single file. We also produce individual files for
            # FreeRADIUS module-per-file layout.
            results: list[tuple[str, str]] = []

            # Render combined file using the template's own loop
            combined_content = template.render(
                generated_at=datetime.now(timezone.utc).isoformat(),
                ldap_servers=rendered_servers,
            )
            results.append(("ldap_orw", combined_content))

            return results
        except Exception as e:
            print(f"[config-manager] ERROR rendering ldap.j2: {e}")
            return []

    def generate_proxy_config(self) -> str:
        """
        Generate proxy.conf from enabled realms.

        Configures home servers and realm routing for proxy-type realms.
        """
        realms = self._load_realms()
        now = datetime.now(timezone.utc).isoformat()

        lines = [
            "# OpenRadiusWeb Generated Configuration - DO NOT EDIT MANUALLY",
            f"# Generated at: {now}",
            "#",
            "# Proxy configuration",
            "# Template: proxy.conf (inline)",
            "",
            "proxy server {",
            "    default_fallback = no",
            "}",
            "",
        ]

        # Generate home_server and realm blocks for proxy realms
        proxy_realms = [r for r in realms if r["realm_type"] == "proxy"]
        for realm in proxy_realms:
            safe_name = (
                realm["name"]
                .lower()
                .replace(" ", "_")
                .replace("-", "_")
                .replace(".", "_")
            )

            # Home server definition
            lines.extend([
                f"home_server hs_{safe_name} {{",
                "    type = auth+acct",
                f"    ipaddr = {realm['proxy_host']}",
                f"    port = {realm.get('proxy_port', 1812)}",
                f"    secret = {realm.get('proxy_secret_encrypted', 'changeme')}",
                "    response_window = 20",
                f"    zombie_period = {realm.get('proxy_dead_time_seconds', 120)}",
                f"    revive_interval = {realm.get('proxy_dead_time_seconds', 120)}",
                "}",
                "",
                f"home_server_pool pool_{safe_name} {{",
                "    type = fail-over",
                f"    home_server = hs_{safe_name}",
                "}",
                "",
            ])

        # Realm definitions for all enabled realms
        for realm in realms:
            safe_name = (
                realm["name"]
                .lower()
                .replace(" ", "_")
                .replace("-", "_")
                .replace(".", "_")
            )

            if realm["realm_type"] == "proxy":
                lines.extend([
                    f"realm {realm['name']} {{",
                    f"    pool = pool_{safe_name}",
                ])
                if realm.get("strip_username", True):
                    lines.append("    strip")
                else:
                    lines.append("    nostrip")
                lines.extend(["}", ""])

            elif realm["realm_type"] == "local":
                lines.extend([
                    f"realm {realm['name']} {{",
                ])
                if realm.get("strip_username", True):
                    lines.append("    strip")
                else:
                    lines.append("    nostrip")
                lines.extend(["}", ""])

            elif realm["realm_type"] == "reject":
                lines.extend([
                    f"realm {realm['name']} {{",
                    "    auth_pool = reject",
                    "}", "",
                ])

        # Default realm (catch-all for unmatched requests)
        default_realm = next(
            (r for r in realms if r["name"] in ("DEFAULT", "default", "*")),
            None,
        )
        if not default_realm:
            lines.extend([
                "# Default realm - handle locally",
                "realm DEFAULT {",
                "    strip",
                "}",
                "",
                "realm NULL {",
                "    strip",
                "}",
                "",
            ])

        return "\n".join(lines)

    def generate_clients_config(self) -> str:
        """
        Generate clients.conf from NAS clients.

        Each NAS client gets a client block with its IP, secret, and shortname.
        """
        clients = self._load_nas_clients()
        now = datetime.now(timezone.utc).isoformat()

        lines = [
            "# OpenRadiusWeb Generated Configuration - DO NOT EDIT MANUALLY",
            f"# Generated at: {now}",
            "#",
            "# RADIUS client (NAS) configuration",
            "",
        ]

        # Always include localhost for testing
        lines.extend([
            "client localhost {",
            "    ipaddr = 127.0.0.1",
            "    secret = testing123",
            "    shortname = localhost",
            "    nas_type = other",
            "}",
            "",
            "client localhost_ipv6 {",
            "    ipv6addr = ::1",
            "    secret = testing123",
            "    shortname = localhost_v6",
            "}",
            "",
        ])

        for client in clients:
            # ip_address is VARCHAR(50) so it can already carry CIDR notation
            # (e.g. "10.0.0.0/24") in a single field — no separate prefix column.
            ip_addr = str(client["ip_address"])
            shortname = client.get("shortname") or client["name"][:31]
            secret = client.get("secret_encrypted", "changeme")
            nas_type = client.get("nas_type", "other")
            description = client.get("description", "")

            lines.append(f"client {shortname} {{")
            lines.append(f"    ipaddr = {ip_addr}")
            lines.append(f"    secret = {secret}")
            lines.append(f"    shortname = {shortname}")
            lines.append(f"    nas_type = {nas_type}")

            if client.get("virtual_server"):
                lines.append(f"    virtual_server = {client['virtual_server']}")

            if description:
                # Sanitize description for FreeRADIUS comment
                safe_desc = description.replace("\n", " ").strip()
                lines.append(f"    # {safe_desc}")

            lines.extend(["}", ""])

        return "\n".join(lines)

    def generate_site_default(
        self,
        *,
        has_eap: bool = False,
        has_python: bool = True,
        ldap_modules: list[str] | None = None,
        realms_enabled: bool = False,
    ) -> str:
        """Render sites-available/default from site_default.j2.

        Capability flags:
        - has_eap: include eap { } block + Auth-Type EAP { } stanza.
          Skip if no active CA/server cert (eap module won't be loaded).
        - has_python: include orw module references. Skip if rlm_python3
          isn't available or rlm_orw.py failed to load.
        - ldap_modules: list of generated LDAP module names. Empty = no
          LDAP authorize/authenticate.
        - realms_enabled: include pre-proxy / post-proxy stanzas.
        """
        ldap_module_dicts = [
            {"name": m.removeprefix("ldap_"), "module_name": m}
            for m in (ldap_modules or [])
        ]
        try:
            template = self._jinja_env.get_template("site_default.j2")
            return template.render(
                generated_at=datetime.now(timezone.utc).isoformat(),
                has_eap=has_eap,
                has_python=has_python,
                ldap_modules=ldap_module_dicts,
                realms_enabled=realms_enabled,
            )
        except Exception as e:
            print(f"[config-manager] ERROR rendering site_default.j2: {e}")
            return ""

    def generate_site_inner_tunnel(
        self,
        *,
        has_python: bool = True,
        ldap_modules: list[str] | None = None,
        realms_enabled: bool = False,
    ) -> str:
        """Render sites-available/inner-tunnel from site_inner_tunnel.j2.

        Caller (generate_all_configs) only invokes this when has_eap is
        true — inner-tunnel is the inner-phase site for PEAP/EAP-TTLS
        and is meaningless without EAP.

        See generate_site_default for has_python / ldap_modules semantics.
        """
        ldap_module_dicts = [
            {"name": m.removeprefix("ldap_"), "module_name": m}
            for m in (ldap_modules or [])
        ]
        try:
            template = self._jinja_env.get_template("site_inner_tunnel.j2")
            return template.render(
                generated_at=datetime.now(timezone.utc).isoformat(),
                has_python=has_python,
                ldap_modules=ldap_module_dicts,
                realms_enabled=realms_enabled,
            )
        except Exception as e:
            print(f"[config-manager] ERROR rendering site_inner_tunnel.j2: {e}")
            return ""

    def generate_python_config(self) -> str:
        """Render mods-available/python from python.j2.

        Targets rlm_python3 (declared via 'python3 orw {' in the template).
        rlm_python3 must be available in the freeradius container — the
        Dockerfile installs freeradius-python3 to ensure that.
        """
        try:
            template = self._jinja_env.get_template("python.j2")
            return template.render(
                generated_at=datetime.now(timezone.utc).isoformat(),
                python_path="/etc/freeradius/mods-config/python",
            )
        except Exception as e:
            print(f"[config-manager] ERROR rendering python.j2: {e}")
            return ""

    # ----------------------------------------------------------
    # Certificate file writer
    # ----------------------------------------------------------

    def write_cert_files(self) -> None:
        """
        Write CA certs, server cert+key, and DH params from DB to filesystem.

        File layout under cert_dir:
            ca.pem          - Active CA certificate
            server.pem      - Active server certificate
            server.key      - Active server private key
            dh.pem          - Diffie-Hellman parameters (if available)
            trusted-cas/    - Directory with all active CA certs
            ldap/           - LDAP TLS CA certificates
        """
        certs = self._load_active_certificates()
        ldap_servers = self._load_ldap_servers()

        # Ensure directories exist
        os.makedirs(self.cert_dir, exist_ok=True)
        trusted_cas_dir = os.path.join(self.cert_dir, "trusted-cas")
        os.makedirs(trusted_cas_dir, exist_ok=True)
        ldap_certs_dir = os.path.join(self.cert_dir, "ldap")
        os.makedirs(ldap_certs_dir, exist_ok=True)

        ca_written = False
        server_written = False

        for cert in certs:
            pem_data = cert.get("pem_data")
            if not pem_data:
                print(
                    f"[config-manager] WARNING: Certificate '{cert['name']}' "
                    f"(type={cert['cert_type']}) has no PEM data, skipping."
                )
                continue

            if cert["cert_type"] == "ca":
                # Write main CA cert
                self._write_file(
                    os.path.join(self.cert_dir, "ca.pem"), pem_data
                )
                # Also write to trusted-cas directory
                safe_name = cert["name"].replace(" ", "_").lower()
                self._write_file(
                    os.path.join(trusted_cas_dir, f"{safe_name}.pem"), pem_data
                )
                # Write CA chain if available
                if cert.get("chain_pem"):
                    self._write_file(
                        os.path.join(trusted_cas_dir, f"{safe_name}_chain.pem"),
                        cert["chain_pem"],
                    )
                ca_written = True

            elif cert["cert_type"] == "server":
                # Write server certificate
                self._write_file(
                    os.path.join(self.cert_dir, "server.pem"), pem_data
                )
                # Write server private key
                key_pem = cert.get("key_pem_encrypted")
                if key_pem:
                    self._write_file(
                        os.path.join(self.cert_dir, "server.key"),
                        key_pem,
                        mode=0o600,
                    )
                # Write DH params if available
                dh_pem = cert.get("dh_params_pem")
                if dh_pem:
                    self._write_file(
                        os.path.join(self.cert_dir, "dh.pem"), dh_pem
                    )
                server_written = True

        # Write LDAP TLS CA certs from ldap_servers table
        for server in ldap_servers:
            if server.get("tls_ca_cert"):
                safe_name = (
                    server["name"]
                    .lower()
                    .replace(" ", "_")
                    .replace("-", "_")
                    .replace(".", "_")
                )
                self._write_file(
                    os.path.join(ldap_certs_dir, f"{safe_name}_ca.pem"),
                    server["tls_ca_cert"],
                )

        if not ca_written:
            print(
                "[config-manager] WARNING: No active CA certificate found. "
                "EAP-TLS/PEAP/TTLS may not work."
            )
        if not server_written:
            print(
                "[config-manager] WARNING: No active server certificate found. "
                "EAP-TLS/PEAP/TTLS may not work."
            )

    # ----------------------------------------------------------
    # Apply configs (write to disk + track in DB)
    # ----------------------------------------------------------

    def apply_configs(self) -> dict:
        """
        Write all configs to output_dir and store hashes in freeradius_config table.

        Returns:
            Dict mapping config_type to {status, hash, error?}.
        """
        results: dict[str, dict] = {}

        # Step 1: Write certificates to filesystem
        try:
            self.write_cert_files()
            results["certificates"] = {"status": "applied", "hash": "", "error": None}
        except Exception as e:
            results["certificates"] = {
                "status": "error",
                "hash": "",
                "error": str(e),
            }
            print(f"[config-manager] ERROR writing certificates: {e}")

        # Step 2: Generate all configs
        try:
            configs = self.generate_all_configs()
        except Exception as e:
            print(f"[config-manager] ERROR generating configs: {e}")
            return {"_generate_error": {"status": "error", "hash": "", "error": str(e)}}

        # Step 3: Write each config file and track state
        for filename, content in configs.items():
            config_hash = self._compute_hash(content)

            # Determine config_type from the filename
            if "eap" in filename:
                config_type = "eap"
            elif "ldap" in filename:
                config_type = "ldap"
            elif "proxy" in filename:
                config_type = "proxy"
            elif "clients" in filename:
                config_type = "clients"
            elif "inner-tunnel" in filename:
                config_type = "inner-tunnel"
            elif "default" in filename:
                config_type = "site-default"
            elif "python" in filename:
                config_type = "python"
            else:
                config_type = filename.replace("/", "_").replace(".", "_")

            try:
                output_path = os.path.join(self.output_dir, filename)
                output_dir = os.path.dirname(output_path)
                os.makedirs(output_dir, exist_ok=True)

                self._write_file(output_path, content)

                # Track in database
                self._save_config_state(
                    config_type=config_type,
                    config_name=filename,
                    content=content,
                    config_hash=config_hash,
                    status="applied",
                )

                results[config_type] = {
                    "status": "applied",
                    "hash": config_hash,
                    "error": None,
                }

            except Exception as e:
                # Track the error in database
                self._save_config_state(
                    config_type=config_type,
                    config_name=filename,
                    content=content,
                    config_hash=config_hash,
                    status="error",
                    error=str(e),
                )

                results[config_type] = {
                    "status": "error",
                    "hash": config_hash,
                    "error": str(e),
                }
                print(f"[config-manager] ERROR writing {filename}: {e}")

        return results

    # ----------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------

    def _compute_hash(self, content: str) -> str:
        """SHA-256 hash of content."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _save_config_state(
        self,
        config_type: str,
        config_name: str,
        content: str,
        config_hash: str,
        status: str,
        error: Optional[str] = None,
    ) -> None:
        """Upsert freeradius_config table with current config state."""
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO freeradius_config
                        (config_type, config_name, config_content, config_hash,
                         last_applied_at, last_applied_hash, status, error_message)
                    VALUES
                        (%(config_type)s, %(config_name)s, %(content)s, %(hash)s,
                         NOW(), %(hash)s, %(status)s, %(error)s)
                    ON CONFLICT (config_type, config_name, tenant_id)
                    DO UPDATE SET
                        config_content = EXCLUDED.config_content,
                        config_hash = EXCLUDED.config_hash,
                        last_applied_at = NOW(),
                        last_applied_hash = EXCLUDED.config_hash,
                        status = EXCLUDED.status,
                        error_message = EXCLUDED.error_message,
                        updated_at = NOW()
                    """,
                    {
                        "config_type": config_type,
                        "config_name": config_name,
                        "content": content,
                        "hash": config_hash,
                        "status": status,
                        "error": error,
                    },
                )
            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"[config-manager] ERROR saving config state for {config_type}: {e}")
        finally:
            conn.close()

    def _write_file(
        self, path: str, content: str, mode: int = 0o644,
    ) -> None:
        """Write content to a file, creating parent directories as needed."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        os.chmod(path, mode)


# ============================================================
# CLI entrypoint
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="OpenRadiusWeb FreeRADIUS Configuration Manager",
    )
    parser.add_argument(
        "--generate-and-apply",
        action="store_true",
        help="Generate all FreeRADIUS configs from database and write to output directory.",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Generate configs and print to stdout without writing files.",
    )
    args = parser.parse_args()

    db_url = os.environ.get("ORW_DB_URL", "")
    manager = FreeRADIUSConfigManager(
        db_url=db_url,
        template_dir=os.environ.get(
            "ORW_TEMPLATE_DIR", "/etc/freeradius/orw-templates"
        ),
        output_dir=os.environ.get(
            "ORW_OUTPUT_DIR", "/etc/freeradius/orw-managed"
        ),
        cert_dir=os.environ.get(
            "ORW_CERT_DIR", "/etc/freeradius/certs"
        ),
    )

    if args.preview:
        configs = manager.generate_all_configs()
        for filename, content in configs.items():
            print(f"\n{'='*60}")
            print(f"  {filename}")
            print(f"  hash: {manager._compute_hash(content)}")
            print(f"{'='*60}")
            print(content)
        print(f"\n[preview] {len(configs)} config file(s) generated.")
        sys.exit(0)

    if args.generate_and_apply:
        print("[config-manager] Generating and applying FreeRADIUS configuration...")
        result = manager.apply_configs()

        # Print summary
        print(f"\n{'='*50}")
        print("  FreeRADIUS Config Apply Summary")
        print(f"{'='*50}")
        for config_type, info in result.items():
            status_icon = "OK" if info["status"] == "applied" else "FAIL"
            line = f"  [{status_icon}] {config_type}: {info['status']}"
            if info.get("hash"):
                line += f" (hash: {info['hash'][:12]}...)"
            if info.get("error"):
                line += f" -- {info['error']}"
            print(line)
        print(f"{'='*50}")

        errors = [k for k, v in result.items() if v["status"] == "error"]
        if errors:
            print(f"\n[config-manager] Completed with {len(errors)} error(s).")
            sys.exit(1)
        else:
            print(f"\n[config-manager] All {len(result)} configs applied successfully.")
            sys.exit(0)

    # No action specified
    parser.print_help()
    sys.exit(1)
