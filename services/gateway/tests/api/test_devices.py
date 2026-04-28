"""API tests for device endpoints."""

import sys
import os
from unittest.mock import AsyncMock, MagicMock
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
    # Override get_db via FastAPI's dependency mechanism (monkeypatching the
    # imported symbol won't catch the already-bound reference in routes/*.py).
    from main import app
    from orw_common.database import get_db

    monkeypatch.setattr("utils.redis_client.get_redis_client", AsyncMock(return_value=mock_redis))

    async def _mock_get_db():
        yield mock_db_session

    app.dependency_overrides[get_db] = _mock_get_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)


class TestDeviceEndpoints:
    """Tests for /api/v1/devices."""

    @pytest.mark.asyncio
    async def test_list_devices_no_auth(self, test_client):
        """Unauthenticated request returns 401."""
        resp = await test_client.get("/api/v1/devices")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_list_devices_authenticated(self, test_client, viewer_headers, mock_db_session):
        """Authenticated request attempts DB query."""
        # Mock DB to return count and empty results.
        # Result objects are *sync* — only db.execute itself is awaitable, so
        # MagicMock for the result, AsyncMock for execute.
        mock_count = MagicMock()
        mock_count.scalar.return_value = 0

        mock_rows = MagicMock()
        mock_rows.mappings.return_value.all.return_value = []

        mock_db_session.execute = AsyncMock(side_effect=[mock_count, mock_rows])

        resp = await test_client.get("/api/v1/devices", headers=viewer_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    @pytest.mark.asyncio
    async def test_create_device_viewer_forbidden(self, test_client, viewer_headers):
        """Viewer cannot create devices."""
        resp = await test_client.post(
            "/api/v1/devices",
            headers=viewer_headers,
            json={
                "mac_address": "AA:BB:CC:DD:EE:FF",
                "ip_address": "192.168.1.100",
            },
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_delete_device_operator_forbidden(self, test_client, operator_headers):
        """Operator cannot delete devices (admin only)."""
        device_id = str(uuid4())
        resp = await test_client.delete(
            f"/api/v1/devices/{device_id}",
            headers=operator_headers,
        )
        assert resp.status_code == 403
