"""Contract tests for docker-compose.yml service env var propagation.

This catches the bug class we hit on 2026-05-03:

  - freeradius_config_manager.py imports orw_common.secrets
    AND calls _rlm_python3_available()
  - Both behaviours depend on env vars being set in the container
  - Adding the import / function call in source code without the
    matching env var entry in docker-compose.yml leads to silent
    runtime regressions:
      * orw_common.secrets without ORW_SECRET_MASTER → RuntimeError
        on startup → service crash-loops
      * _rlm_python3_available() without ORW_HAS_PYTHON3 → False
        in non-freeradius containers → site_default rendered without
        orw → MAB rejected with "No Auth-Type found"

Both bugs cost real production debug time. This test rejects the
regression at PR time by parsing docker-compose.yml + asserting the
required env vars on every service that imports the corresponding
module.
"""
from __future__ import annotations

from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[4]
_COMPOSE_PATH = _REPO_ROOT / "docker-compose.yml"


# Lazy import — pyyaml might not be in services/gateway/requirements-test.txt.
# Skip the whole module if it isn't available rather than failing the import.
yaml = pytest.importorskip("yaml")


@pytest.fixture(scope="module")
def compose() -> dict:
    """Parsed docker-compose.yml as a dict."""
    if not _COMPOSE_PATH.exists():
        pytest.skip(f"docker-compose.yml not found at {_COMPOSE_PATH}")
    with _COMPOSE_PATH.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _service_env_keys(compose: dict, service: str) -> set[str]:
    """Return the set of env var NAMES (not values) for the named service.

    Handles both list form (`- KEY=value`) and mapping form (`KEY: value`).
    Returns empty set if the service doesn't exist or has no environment.
    """
    services = compose.get("services", {})
    if service not in services:
        return set()
    env = services[service].get("environment", [])
    if isinstance(env, dict):
        return set(env.keys())
    keys: set[str] = set()
    for entry in env:
        if "=" in entry:
            keys.add(entry.split("=", 1)[0])
        else:
            keys.add(entry)  # `- KEY` (inherits from host)
    return keys


# ---------------------------------------------------------------------------
# ORW_SECRET_MASTER + ORW_SECRET_KDF_SALT — required by orw_common.secrets
# ---------------------------------------------------------------------------

# Every service that imports orw_common.secrets (transitively or directly)
# must have BOTH env vars set; without them, the module raises RuntimeError
# at import time and the service crash-loops.
_SECRETS_IMPORTING_SERVICES = [
    "gateway",                        # PR #71 — direct import in repositories
    "freeradius",                     # PR #71 — rlm_orw.py + freeradius_config_manager.py
    "freeradius_config_watcher",      # PR #71 — imports freeradius_config_manager
    "switch_mgmt",                    # PR #74 — snmp_manager.py decrypts community
    "coa",                            # PR #74/#79 — coa_manager.py decrypts coa_secret
]


@pytest.mark.parametrize("service", _SECRETS_IMPORTING_SERVICES)
def test_secrets_env_vars_present(compose, service):
    """Every service that uses orw_common.secrets must have both env vars."""
    env = _service_env_keys(compose, service)
    missing = {"ORW_SECRET_MASTER", "ORW_SECRET_KDF_SALT"} - env
    assert not missing, (
        f"docker-compose service '{service}' is missing env vars "
        f"{missing} required by orw_common.secrets. Without these the "
        f"module raises RuntimeError at import → service crash-loops. "
        f"See PR #70/#71 for context."
    )


# ---------------------------------------------------------------------------
# ORW_HAS_PYTHON3 — required where freeradius_config_manager.py runs
# ---------------------------------------------------------------------------

# Containers that run freeradius_config_manager.py but DON'T have
# /usr/lib/freeradius/rlm_python3.so on disk need this env var, or
# _rlm_python3_available() returns False and they generate sites
# WITHOUT the orw module call → MAB rejected as "No Auth-Type found".
#
# The freeradius container itself has the .so file (Dockerfile.freeradius
# enforces it at build) but we set the env var on it too for parity
# (explicit > implicit; PR #76).
_HAS_PYTHON3_SERVICES = [
    "freeradius",
    "freeradius_config_watcher",
]


@pytest.mark.parametrize("service", _HAS_PYTHON3_SERVICES)
def test_has_python3_env_var_present(compose, service):
    """freeradius + watcher must declare ORW_HAS_PYTHON3=true."""
    env = _service_env_keys(compose, service)
    assert "ORW_HAS_PYTHON3" in env, (
        f"docker-compose service '{service}' is missing ORW_HAS_PYTHON3. "
        f"Without it, freeradius_config_manager._rlm_python3_available() "
        f"falls back to filesystem check; in containers that lack "
        f"/usr/lib/freeradius/rlm_python3.so this returns False, sites "
        f"get generated without the orw module, and MAB requests fail "
        f"with 'No Auth-Type found'. See PR #76 for the postmortem."
    )


# ---------------------------------------------------------------------------
# Sanity — the file we're parsing actually has services
# ---------------------------------------------------------------------------

def test_compose_has_services(compose):
    """Catch a malformed docker-compose.yml that parses to nothing useful."""
    assert isinstance(compose, dict)
    assert "services" in compose
    assert len(compose["services"]) > 0
