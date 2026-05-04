"""Tests for SSHManager._get_ssh_credentials.

Pre-PR-#100 the function returned hardcoded `{"username": "",
"password": ""}` so every SSH-based switch action silently failed
(no creds → SSH auth reject). PR #100 wires it up to the new
`network_devices.ssh_username` + `ssh_password_encrypted` columns
plus `orw_common.secrets.decrypt_secret`.

These tests verify the lookup + decrypt path with mocked DB rows
so we don't need a real Postgres + Netmiko in unit tests.
"""
from __future__ import annotations

import base64
import os
import secrets
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "shared"))
sys.path.insert(0, str(_REPO_ROOT / "services" / "switch_mgmt"))

# orw_common.secrets._derive_key needs both env vars set before import.
# Use throwaway test values so the unit test doesn't touch real keys.
os.environ.setdefault(
    "ORW_SECRET_MASTER",
    "ssh-manager-test-master-" + secrets.token_urlsafe(16),
)
os.environ.setdefault(
    "ORW_SECRET_KDF_SALT",
    base64.urlsafe_b64encode(b"ssh-test-16byte!").rstrip(b"=").decode("ascii"),
)


from orw_common.secrets import encrypt_secret  # noqa: E402
from ssh_manager import SSHManager  # noqa: E402


def _mock_db_returning(row):
    """Build a mock async-context-manager that yields a session whose
    .execute() returns a result whose .first() returns `row`."""
    mock_db = MagicMock()
    mock_result = MagicMock()
    mock_result.first.return_value = row
    mock_db.execute = AsyncMock(return_value=mock_result)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_db)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


class TestGetSshCredentials:
    @pytest.mark.asyncio
    async def test_returns_decrypted_creds_when_row_complete(self):
        """Happy path: row has username + encrypted password → decrypt
        and return non-empty creds."""
        ct = encrypt_secret("real-switch-pw")
        with patch(
            "ssh_manager.get_db_context",
            return_value=_mock_db_returning(("netadmin", ct)),
        ):
            creds = await SSHManager()._get_ssh_credentials(str(uuid4()))
        assert creds == {"username": "netadmin", "password": "real-switch-pw"}

    @pytest.mark.asyncio
    async def test_raises_when_device_not_found(self):
        """No row → not found error, NOT empty creds (which would
        cause silent SSH failure downstream)."""
        with patch(
            "ssh_manager.get_db_context",
            return_value=_mock_db_returning(None),
        ):
            with pytest.raises(ValueError, match="not found"):
                await SSHManager()._get_ssh_credentials(str(uuid4()))

    @pytest.mark.asyncio
    async def test_raises_when_username_missing(self):
        """Device exists but ssh_username is NULL — common case for
        SNMP-only devices. Loud error tells the operator how to fix."""
        with patch(
            "ssh_manager.get_db_context",
            return_value=_mock_db_returning((None, None)),
        ):
            with pytest.raises(ValueError, match="No SSH credentials"):
                await SSHManager()._get_ssh_credentials(str(uuid4()))

    @pytest.mark.asyncio
    async def test_handles_null_password_with_empty_string(self):
        """Operator set ssh_username but no password (e.g. uses SSH
        keys) — return empty password, don't raise. Caller decides
        whether SSH proceeds with key-only auth."""
        with patch(
            "ssh_manager.get_db_context",
            return_value=_mock_db_returning(("netadmin", None)),
        ):
            creds = await SSHManager()._get_ssh_credentials(str(uuid4()))
        assert creds == {"username": "netadmin", "password": ""}
