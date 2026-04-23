"""Shared test fixtures for gateway tests."""

import sys
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt

# Ensure gateway source is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../shared"))

# Override settings BEFORE any app import
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("NATS_URL", "nats://localhost:4222")

JWT_SECRET = "test-secret-key-for-unit-tests"
JWT_ALGORITHM = "HS256"

ADMIN_USER_ID = str(uuid4())
OPERATOR_USER_ID = str(uuid4())
VIEWER_USER_ID = str(uuid4())
TENANT_ID = str(uuid4())


def _make_token(user_id: str, username: str, role: str, tenant_id: str = TENANT_ID) -> str:
    """Create a JWT token for testing."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "tenant_id": tenant_id,
        "exp": now + timedelta(hours=1),
        "iat": now,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


@pytest.fixture
def admin_token() -> str:
    return _make_token(ADMIN_USER_ID, "admin", "admin")


@pytest.fixture
def operator_token() -> str:
    return _make_token(OPERATOR_USER_ID, "operator", "operator")


@pytest.fixture
def viewer_token() -> str:
    return _make_token(VIEWER_USER_ID, "viewer", "viewer")


@pytest.fixture
def admin_headers(admin_token) -> dict:
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture
def operator_headers(operator_token) -> dict:
    return {"Authorization": f"Bearer {operator_token}"}


@pytest.fixture
def viewer_headers(viewer_token) -> dict:
    return {"Authorization": f"Bearer {viewer_token}"}


@pytest.fixture
def mock_db():
    """Create a mock async DB session."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    return session


@pytest.fixture
def mock_nats(monkeypatch):
    """Mock NATS client to prevent real connections."""
    mock = AsyncMock()
    mock.publish = AsyncMock()
    mock.connect = AsyncMock()
    mock.close = AsyncMock()
    mock.ensure_stream = AsyncMock()
    monkeypatch.setattr("orw_common.nats_client.publish", mock.publish)
    monkeypatch.setattr("orw_common.nats_client.connect", mock.connect)
    monkeypatch.setattr("orw_common.nats_client.close", mock.close)
    monkeypatch.setattr("orw_common.nats_client.ensure_stream", mock.ensure_stream)
    return mock
