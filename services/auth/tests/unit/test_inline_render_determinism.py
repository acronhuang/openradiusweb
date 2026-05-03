"""Two of the three configs the watcher renders are NOT built from the
Jinja templates — they're built inline in Python via list-of-strings
concatenation:

  - generate_clients_config() → clients.conf
  - generate_proxy_config()   → proxy.conf

The Jinja-template determinism test (test_template_determinism.py)
doesn't cover them. Pre-PR-#90 both inlined a `# Generated at: <now>`
header → every render produced a different hash → idempotency guard
in apply_configs marked them "applied" every reconcile → SIGHUP every
5 min even when nothing in the DB had changed.

This test exercises the actual code paths with mocked DB rows so the
nondeterminism shows up locally (instead of post-deploy).

Scope: only the two inline-built configs. The certificates path is
covered by the apply_configs hash-skip test (TBD) since it has its
own non-Jinja code path.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest


_AUTH_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_AUTH_DIR))


pytest.importorskip("jinja2")
pytest.importorskip("psycopg2")


@pytest.fixture
def manager(tmp_path):
    """Build a manager that doesn't touch a real DB. Each test patches
    the relevant `_load_*` helper to inject deterministic data."""
    from freeradius_config_manager import FreeRADIUSConfigManager

    mgr = FreeRADIUSConfigManager(
        db_url="postgresql://stub:stub@stub:5432/stub",
        template_dir=str(_AUTH_DIR / "freeradius" / "templates"),
        output_dir=str(tmp_path / "out"),
        cert_dir=str(tmp_path / "certs"),
    )
    return mgr


def _sample_clients() -> list[dict]:
    # `secret_encrypted=None` keeps the test fixture independent of the
    # actual encryption key — the manager falls through to the
    # `or "changeme"` default. This test cares about determinism, not
    # about the actual secret value.
    return [
        {
            "ip_address": "10.0.0.1",
            "name": "switch-a",
            "shortname": "sw-a",
            "secret_encrypted": None,
            "nas_type": "cisco",
            "description": "Lab switch",
            "virtual_server": None,
        },
        {
            "ip_address": "10.0.0.2",
            "name": "switch-b",
            "shortname": "sw-b",
            "secret_encrypted": None,
            "nas_type": "other",
            "description": "",
            "virtual_server": None,
        },
    ]


def _sample_realms() -> list[dict]:
    return [
        {
            "name": "mds.local",
            "realm_type": "local",
            "strip_username": True,
            "ldap_server_name": None,
        },
        {
            "name": "partner.example",
            "realm_type": "proxy",
            "strip_username": False,
            "proxy_host": "10.99.99.1",
            "proxy_port": 1812,
            "proxy_secret_encrypted": None,
            "proxy_dead_time_seconds": 60,
        },
    ]


def test_generate_clients_config_is_deterministic(manager):
    with patch.object(manager, "_load_nas_clients", return_value=_sample_clients()):
        a = manager.generate_clients_config()
        b = manager.generate_clients_config()
    assert a == b, (
        "generate_clients_config produced different bytes on identical inputs. "
        "Regression of PR #90 — likely a `# Generated at: <now>` snuck back "
        "into the inline string builder."
    )


def test_generate_proxy_config_is_deterministic(manager):
    with patch.object(manager, "_load_realms", return_value=_sample_realms()):
        a = manager.generate_proxy_config()
        b = manager.generate_proxy_config()
    assert a == b, (
        "generate_proxy_config produced different bytes on identical inputs. "
        "Regression of PR #90 — likely a `# Generated at: <now>` snuck back "
        "into the inline string builder."
    )


def test_no_generated_at_in_clients_or_proxy_output(manager):
    """Even if the determinism test passes by accident (e.g. mocked time),
    the literal substring `Generated at:` should never appear in either
    file's output. Defense in depth against the timestamp pattern."""
    with (
        patch.object(manager, "_load_nas_clients", return_value=_sample_clients()),
        patch.object(manager, "_load_realms", return_value=_sample_realms()),
    ):
        clients_out = manager.generate_clients_config()
        proxy_out = manager.generate_proxy_config()
    bad: list[str] = []
    if "Generated at:" in clients_out:
        bad.append("clients.conf")
    if "Generated at:" in proxy_out:
        bad.append("proxy.conf")
    assert not bad, (
        f"`Generated at:` appears in {bad} output. Remove the timestamp — "
        f"freeradius_config.last_applied_at already records when the file "
        f"was applied, and the timestamp in the output defeats the "
        f"hash-based idempotency guard in apply_configs."
    )


def test_compute_cert_files_hash_is_deterministic(manager):
    """The cert-file hash must be stable across two calls with the same
    DB state, otherwise the apply_configs cert-skip path (PR #90) keeps
    re-writing files and SIGHUPing on every reconcile."""
    sample_certs = [
        {
            "cert_type": "ca",
            "name": "Root CA",
            "pem_data": "-----BEGIN CERTIFICATE-----\nFAKE\n-----END CERTIFICATE-----\n",
            "chain_pem": None,
            "key_pem_encrypted": None,
            "dh_params_pem": None,
        },
        {
            "cert_type": "server",
            "name": "Radius Server",
            "pem_data": "-----BEGIN CERTIFICATE-----\nSERVER-FAKE\n-----END CERTIFICATE-----\n",
            "chain_pem": None,
            "key_pem_encrypted": "AcW4nl87...",  # ciphertext-shaped string
            "dh_params_pem": "-----BEGIN DH PARAMETERS-----\nDH\n-----END DH PARAMETERS-----\n",
        },
    ]
    sample_ldap = [
        {"name": "MDS-DC", "tls_ca_cert": "-----BEGIN CERTIFICATE-----\nLDAP-CA\n-----END CERTIFICATE-----\n"},
    ]
    with (
        patch.object(manager, "_load_active_certificates", return_value=sample_certs),
        patch.object(manager, "_load_ldap_servers", return_value=sample_ldap),
    ):
        h1 = manager._compute_cert_files_hash()
        h2 = manager._compute_cert_files_hash()
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex digest


def test_compute_cert_files_hash_changes_on_input_change(manager):
    """Sanity: any input change must shift the hash. Otherwise the
    skip-when-unchanged path silently misses real changes."""
    base_certs = [
        {
            "cert_type": "ca",
            "name": "Root",
            "pem_data": "PEM-ORIGINAL",
            "chain_pem": None,
            "key_pem_encrypted": None,
            "dh_params_pem": None,
        },
    ]
    with patch.object(manager, "_load_active_certificates", return_value=base_certs), \
         patch.object(manager, "_load_ldap_servers", return_value=[]):
        h_original = manager._compute_cert_files_hash()

    changed_certs = [{**base_certs[0], "pem_data": "PEM-DIFFERENT"}]
    with patch.object(manager, "_load_active_certificates", return_value=changed_certs), \
         patch.object(manager, "_load_ldap_servers", return_value=[]):
        h_changed = manager._compute_cert_files_hash()

    assert h_original != h_changed


def test_compute_cert_files_hash_stable_across_query_order(manager):
    """If the DB query returns rows in a different order on a future
    reconcile (no ORDER BY guarantee), the hash must NOT change. Sort
    by stable key in the hash function."""
    certs_a = [
        {"cert_type": "ca", "name": "A", "pem_data": "A", "chain_pem": None,
         "key_pem_encrypted": None, "dh_params_pem": None},
        {"cert_type": "ca", "name": "B", "pem_data": "B", "chain_pem": None,
         "key_pem_encrypted": None, "dh_params_pem": None},
    ]
    certs_b = list(reversed(certs_a))
    with patch.object(manager, "_load_active_certificates", return_value=certs_a), \
         patch.object(manager, "_load_ldap_servers", return_value=[]):
        h_a = manager._compute_cert_files_hash()
    with patch.object(manager, "_load_active_certificates", return_value=certs_b), \
         patch.object(manager, "_load_ldap_servers", return_value=[]):
        h_b = manager._compute_cert_files_hash()
    assert h_a == h_b
