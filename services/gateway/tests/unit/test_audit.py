"""Unit tests for utils/audit.py."""

import sys
import os
import json
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest
from utils.audit import log_audit


@pytest.fixture
def mock_db():
    session = AsyncMock()
    session.execute = AsyncMock()
    return session


class TestLogAudit:
    """Tests for the log_audit helper."""

    @pytest.mark.asyncio
    async def test_basic_audit_entry(self, mock_db):
        """Audit entry with all fields populated."""
        user = {"sub": "550e8400-e29b-41d4-a716-446655440000", "tenant_id": "660e8400-e29b-41d4-a716-446655440001"}
        await log_audit(
            mock_db, user,
            action="create",
            resource_type="device",
            resource_id="770e8400-e29b-41d4-a716-446655440002",
            details={"mac_address": "AA:BB:CC:DD:EE:FF"},
            ip_address="192.168.1.1",
        )
        mock_db.execute.assert_called_once()
        args = mock_db.execute.call_args
        params = args[0][1]
        assert params["user_id"] == "550e8400-e29b-41d4-a716-446655440000"
        assert params["action"] == "create"
        assert params["resource_type"] == "device"
        assert params["resource_id"] == "770e8400-e29b-41d4-a716-446655440002"
        assert params["ip_address"] == "192.168.1.1"
        assert params["tenant_id"] == "660e8400-e29b-41d4-a716-446655440001"
        # details should be JSON string
        details = json.loads(params["details"])
        assert details["mac_address"] == "AA:BB:CC:DD:EE:FF"

    @pytest.mark.asyncio
    async def test_null_user_fields(self, mock_db):
        """Audit entry with null user_id (e.g., failed login)."""
        user = {"sub": None, "tenant_id": None}
        await log_audit(
            mock_db, user,
            action="login_failed",
            resource_type="auth",
            details={"username": "baduser"},
        )
        params = mock_db.execute.call_args[0][1]
        assert params["user_id"] is None
        assert params["tenant_id"] is None
        assert params["resource_id"] is None

    @pytest.mark.asyncio
    async def test_no_details(self, mock_db):
        """Audit entry with no details defaults to empty dict JSON."""
        user = {"sub": "550e8400-e29b-41d4-a716-446655440000"}
        await log_audit(mock_db, user, action="delete", resource_type="policy")
        params = mock_db.execute.call_args[0][1]
        assert json.loads(params["details"]) == {}

    @pytest.mark.asyncio
    async def test_no_ip_address(self, mock_db):
        """ip_address defaults to None."""
        user = {"sub": "test-uuid"}
        await log_audit(mock_db, user, action="update", resource_type="user")
        params = mock_db.execute.call_args[0][1]
        assert params["ip_address"] is None

    @pytest.mark.asyncio
    async def test_details_with_non_serializable(self, mock_db):
        """Details with non-JSON-serializable values use str() fallback."""
        from datetime import datetime
        user = {"sub": "test-uuid"}
        now = datetime(2026, 1, 1, 12, 0, 0)
        await log_audit(
            mock_db, user,
            action="update",
            resource_type="device",
            details={"timestamp": now},
        )
        params = mock_db.execute.call_args[0][1]
        details = json.loads(params["details"])
        assert "2026" in details["timestamp"]

    @pytest.mark.asyncio
    async def test_sql_uses_cast_syntax(self, mock_db):
        """Verify CAST() syntax is used instead of :: to avoid SQLAlchemy conflicts."""
        user = {"sub": "test-uuid", "tenant_id": "tenant-uuid"}
        await log_audit(mock_db, user, action="test", resource_type="test")
        sql_text = str(mock_db.execute.call_args[0][0].text)
        assert "CAST(:user_id AS uuid)" in sql_text
        assert "CAST(:tenant_id AS uuid)" in sql_text
        assert "CAST(:resource_id AS uuid)" in sql_text
        assert "CAST(:details AS jsonb)" in sql_text
        assert "CAST(:ip_address AS inet)" in sql_text
        # Should NOT use :: syntax
        assert ":user_id::uuid" not in sql_text
