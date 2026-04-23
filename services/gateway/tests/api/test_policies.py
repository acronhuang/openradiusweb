"""API tests for policy endpoints."""

import sys
import os
from unittest.mock import AsyncMock
from uuid import uuid4

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../../shared"))

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def mock_redis():
    r = AsyncMock()
    r.get = AsyncMock(return_value=None)
    r.incr = AsyncMock(return_value=1)
    r.expire = AsyncMock()
    r.setex = AsyncMock()
    r.delete = AsyncMock()
    return r


@pytest.fixture
def mock_db_session():
    session = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    return session


@pytest.fixture
async def test_client(mock_nats, mock_redis, mock_db_session, monkeypatch):
    monkeypatch.setattr("utils.redis_client.get_redis_client", AsyncMock(return_value=mock_redis))

    async def mock_get_db():
        yield mock_db_session

    monkeypatch.setattr("orw_common.database.get_db", mock_get_db)

    from main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


class TestPolicyEndpoints:
    """Tests for /api/v1/policies."""

    @pytest.mark.asyncio
    async def test_list_policies_no_auth(self, test_client):
        """Unauthenticated request returns 401."""
        resp = await test_client.get("/api/v1/policies")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_create_policy_viewer_forbidden(self, test_client, viewer_headers):
        """Viewer cannot create policies."""
        resp = await test_client.post(
            "/api/v1/policies",
            headers=viewer_headers,
            json={
                "name": "Test Policy",
                "conditions": [{"field": "device.type", "operator": "equals", "value": "printer"}],
                "match_actions": [{"type": "vlan_assign", "params": {"vlan_id": 10}}],
            },
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_delete_policy_operator_forbidden(self, test_client, operator_headers):
        """Operator cannot delete policies (admin only)."""
        policy_id = str(uuid4())
        resp = await test_client.delete(
            f"/api/v1/policies/{policy_id}",
            headers=operator_headers,
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_list_templates(self, test_client, viewer_headers):
        """Templates endpoint returns templates list."""
        resp = await test_client.get(
            "/api/v1/policies/templates/list",
            headers=viewer_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "templates" in data
        assert "action_types" in data
        assert len(data["templates"]) > 0

    @pytest.mark.asyncio
    async def test_simulate_no_auth(self, test_client):
        """Simulate endpoint requires authentication."""
        policy_id = str(uuid4())
        resp = await test_client.post(
            f"/api/v1/policies/{policy_id}/simulate",
            json={"mac_address": "AA:BB:CC:DD:EE:FF"},
        )
        assert resp.status_code == 401
