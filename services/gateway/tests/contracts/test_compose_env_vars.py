"""Contract tests for compose service env var propagation.

This catches the bug class we hit on 2026-05-03:

  - freeradius_config_manager.py imports orw_common.secrets
    AND calls _rlm_python3_available()
  - Both behaviours depend on env vars being set in the container
  - Adding the import / function call in source code without the
    matching env var entry in the compose files leads to silent
    runtime regressions:
      * orw_common.secrets without ORW_SECRET_MASTER → RuntimeError
        on first decrypt call → "Missing env var(s)" stub config
      * _rlm_python3_available() without ORW_HAS_PYTHON3 → False
        in non-freeradius containers → site_default rendered without
        orw → MAB rejected with "No Auth-Type found"

Both bugs cost real production debug time. This test rejects the
regression at PR time by parsing each compose file + asserting the
required env vars on every service that imports the corresponding
module.

Covers BOTH `docker-compose.yml` (dev) AND `docker-compose.prod.yml`
(the file used in production via `--env-file .env.production`). Prior
to 2026-05-03 only the dev file was covered, and a long-standing
omission of ORW_SECRET_* + ORW_HAS_PYTHON3 from the prod file went
undetected until the strict-mode rollout (PR #83) made the watcher
fail loudly.
"""
from __future__ import annotations

from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[4]


# Lazy import — pyyaml might not be in services/gateway/requirements-test.txt.
# Skip the whole module if it isn't available rather than failing the import.
yaml = pytest.importorskip("yaml")


# ---------------------------------------------------------------------------
# Service-name conventions: both compose files now agree on the same names
# (PR #85 renamed dev `coa` → `coa_service` for parity with prod). If you
# add a new compose file in the future, append it here with the same
# service tuples — the parametrize unfolds the cross-product automatically.
# ---------------------------------------------------------------------------

_COMPOSE_FILES = ("docker-compose.yml", "docker-compose.prod.yml")

_SECRETS_SERVICES = (
    "gateway",
    "freeradius",
    "freeradius_config_watcher",
    "switch_mgmt",
    "coa_service",
)
_PYTHON3_SERVICES = ("freeradius", "freeradius_config_watcher")


def _load(compose_file: str) -> dict:
    path = _REPO_ROOT / compose_file
    if not path.exists():
        pytest.skip(f"{compose_file} not found at {path}")
    with path.open("r", encoding="utf-8") as fh:
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


def _secrets_cases() -> list[tuple[str, str]]:
    return [(f, s) for f in _COMPOSE_FILES for s in _SECRETS_SERVICES]


def _python3_cases() -> list[tuple[str, str]]:
    return [(f, s) for f in _COMPOSE_FILES for s in _PYTHON3_SERVICES]


# ---------------------------------------------------------------------------
# ORW_SECRET_MASTER + ORW_SECRET_KDF_SALT — required by orw_common.secrets
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("compose_file,service", _secrets_cases())
def test_secrets_env_vars_present(compose_file, service):
    """Every service that uses orw_common.secrets must have both env vars."""
    compose = _load(compose_file)
    env = _service_env_keys(compose, service)
    missing = {"ORW_SECRET_MASTER", "ORW_SECRET_KDF_SALT"} - env
    assert not missing, (
        f"{compose_file}: service '{service}' is missing env vars "
        f"{missing} required by orw_common.secrets. Without these the "
        f"module raises RuntimeError at first decrypt call. See PR #70/#71."
    )


# ---------------------------------------------------------------------------
# ORW_HAS_PYTHON3 — required where freeradius_config_manager.py runs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("compose_file,service", _python3_cases())
def test_has_python3_env_var_present(compose_file, service):
    """freeradius + watcher must declare ORW_HAS_PYTHON3=true."""
    compose = _load(compose_file)
    env = _service_env_keys(compose, service)
    assert "ORW_HAS_PYTHON3" in env, (
        f"{compose_file}: service '{service}' is missing ORW_HAS_PYTHON3. "
        f"Without it, freeradius_config_manager._rlm_python3_available() "
        f"falls back to filesystem check; in containers that lack "
        f"/usr/lib/freeradius/rlm_python3.so this returns False, sites "
        f"get generated without the orw module, and MAB requests fail "
        f"with 'No Auth-Type found'. See PR #76 for the postmortem."
    )


# ---------------------------------------------------------------------------
# Sanity — each compose file actually parses and has services
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("compose_file", _COMPOSE_FILES)
def test_compose_has_services(compose_file):
    compose = _load(compose_file)
    assert isinstance(compose, dict)
    assert "services" in compose
    assert len(compose["services"]) > 0
