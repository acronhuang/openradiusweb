"""HTTP-level tests for the auth feature.

Ported from the legacy `tests/api/test_auth.py`. These hit the FastAPI
app through ASGI and validate status codes / headers / RBAC. They mock
DB and Redis at their boundaries.

Pure-logic tests for the service layer live in `test_service.py`.
"""
import pytest


class TestLogin:
    """POST /api/v1/auth/login."""

    @pytest.mark.asyncio
    async def test_login_missing_fields(self, test_client):
        resp = await test_client.post("/api/v1/auth/login", json={})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_login_empty_username(self, test_client):
        resp = await test_client.post(
            "/api/v1/auth/login",
            json={"username": "", "password": "test"},
        )
        # Pydantic may accept empty string, but the DB lookup yields no user.
        assert resp.status_code in (401, 422)


class TestMeEndpoint:
    """GET /api/v1/auth/me."""

    @pytest.mark.asyncio
    async def test_me_no_token(self, test_client):
        resp = await test_client.get("/api/v1/auth/me")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_me_invalid_token(self, test_client):
        resp = await test_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer invalid.token.here"},
        )
        assert resp.status_code == 401


class TestRBAC:
    """Role-based access control on user-management endpoints."""

    @pytest.mark.asyncio
    async def test_viewer_cannot_create_user(self, test_client, viewer_headers):
        resp = await test_client.post(
            "/api/v1/auth/users",
            headers=viewer_headers,
            json={"username": "newuser", "password": "password123", "role": "viewer"},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_operator_cannot_create_user(self, test_client, operator_headers):
        resp = await test_client.post(
            "/api/v1/auth/users",
            headers=operator_headers,
            json={"username": "newuser", "password": "password123", "role": "viewer"},
        )
        assert resp.status_code == 403


class TestSecurityHeaders:
    """Cross-cutting middleware sanity (lives here because /health is the
    cheapest endpoint to exercise it)."""

    @pytest.mark.asyncio
    async def test_security_headers_present(self, test_client):
        resp = await test_client.get("/health")
        assert resp.headers.get("x-content-type-options") == "nosniff"
        assert resp.headers.get("x-frame-options") == "DENY"
        assert resp.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
        assert resp.headers.get("cache-control") == "no-store"

    @pytest.mark.asyncio
    async def test_request_id_header(self, test_client):
        resp = await test_client.get("/health")
        request_id = resp.headers.get("x-request-id")
        assert request_id is not None
        assert len(request_id) == 36
