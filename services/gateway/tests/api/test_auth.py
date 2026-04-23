"""API tests for authentication endpoints."""

import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../../shared"))

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def mock_redis():
    """Mock Redis client for rate limiting."""
    r = AsyncMock()
    r.get = AsyncMock(return_value=None)
    r.incr = AsyncMock(return_value=1)
    r.expire = AsyncMock()
    r.setex = AsyncMock()
    r.delete = AsyncMock()
    return r


@pytest.fixture
def mock_db_session():
    """Mock database session."""
    session = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    return session


@pytest.fixture
async def test_client(mock_nats, mock_redis, mock_db_session, monkeypatch):
    """Create a test client with mocked dependencies."""
    # Mock Redis
    monkeypatch.setattr("utils.redis_client.get_redis_client", AsyncMock(return_value=mock_redis))

    # Mock DB
    async def mock_get_db():
        yield mock_db_session

    monkeypatch.setattr("orw_common.database.get_db", mock_get_db)

    from main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


class TestLogin:
    """Tests for POST /api/v1/auth/login."""

    @pytest.mark.asyncio
    async def test_login_missing_fields(self, test_client):
        """Login with missing fields returns 422."""
        resp = await test_client.post("/api/v1/auth/login", json={})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_login_empty_username(self, test_client):
        """Login with empty username returns 422."""
        resp = await test_client.post(
            "/api/v1/auth/login",
            json={"username": "", "password": "test"},
        )
        # FastAPI may accept empty string, but DB lookup should fail
        assert resp.status_code in (401, 422)


class TestMeEndpoint:
    """Tests for GET /api/v1/auth/me."""

    @pytest.mark.asyncio
    async def test_me_no_token(self, test_client):
        """No auth header returns 401."""
        resp = await test_client.get("/api/v1/auth/me")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_me_invalid_token(self, test_client):
        """Invalid JWT token returns 401."""
        resp = await test_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer invalid.token.here"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_me_valid_token(self, test_client, admin_headers):
        """Valid token returns user info."""
        resp = await test_client.get("/api/v1/auth/me", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "admin"
        assert data["role"] == "admin"


class TestRBAC:
    """Tests for role-based access control."""

    @pytest.mark.asyncio
    async def test_viewer_cannot_create_user(self, test_client, viewer_headers):
        """Viewer role cannot create users (403)."""
        resp = await test_client.post(
            "/api/v1/auth/users",
            headers=viewer_headers,
            json={
                "username": "newuser",
                "password": "password123",
                "role": "viewer",
            },
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_operator_cannot_create_user(self, test_client, operator_headers):
        """Operator role cannot create users (403)."""
        resp = await test_client.post(
            "/api/v1/auth/users",
            headers=operator_headers,
            json={
                "username": "newuser",
                "password": "password123",
                "role": "viewer",
            },
        )
        assert resp.status_code == 403


class TestSecurityHeaders:
    """Tests for security headers middleware."""

    @pytest.mark.asyncio
    async def test_security_headers_present(self, test_client):
        """Security headers are set on all responses."""
        resp = await test_client.get("/health")
        assert resp.headers.get("x-content-type-options") == "nosniff"
        assert resp.headers.get("x-frame-options") == "DENY"
        assert resp.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
        assert resp.headers.get("cache-control") == "no-store"

    @pytest.mark.asyncio
    async def test_request_id_header(self, test_client):
        """X-Request-ID header is generated."""
        resp = await test_client.get("/health")
        request_id = resp.headers.get("x-request-id")
        assert request_id is not None
        assert len(request_id) == 36  # UUID format
