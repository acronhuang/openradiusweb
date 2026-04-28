"""Test fixtures for the auth feature.

Self-contained: defines its own JWT helper and mock fixtures so the
feature subtree can be collected without depending on the legacy
`gateway/tests/conftest.py`. Once that conftest is promoted to
`gateway/conftest.py` (a real parent of `features/`), this file can
shrink to only feature-specific fixtures.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt

# Make gateway source + shared/ importable.
_THIS_DIR = os.path.dirname(__file__)
_GATEWAY = os.path.abspath(os.path.join(_THIS_DIR, "../../../"))
_SHARED = os.path.abspath(os.path.join(_THIS_DIR, "../../../../../shared"))
for p in (_GATEWAY, _SHARED):
    if p not in sys.path:
        sys.path.insert(0, p)

# Override settings BEFORE any app import.
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("NATS_URL", "nats://localhost:4222")

_JWT_SECRET = "test-secret-key-for-unit-tests"
_JWT_ALG = "HS256"
_TENANT = str(uuid4())
_ADMIN_ID = str(uuid4())
_OPERATOR_ID = str(uuid4())
_VIEWER_ID = str(uuid4())


def _make_token(user_id: str, username: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "tenant_id": _TENANT,
        "exp": now + timedelta(hours=1),
        "iat": now,
    }
    return jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALG)


@pytest.fixture
def admin_headers():
    return {"Authorization": f"Bearer {_make_token(_ADMIN_ID, 'admin', 'admin')}"}


@pytest.fixture
def operator_headers():
    return {"Authorization": f"Bearer {_make_token(_OPERATOR_ID, 'operator', 'operator')}"}


@pytest.fixture
def viewer_headers():
    return {"Authorization": f"Bearer {_make_token(_VIEWER_ID, 'viewer', 'viewer')}"}


@pytest.fixture
def mock_nats(monkeypatch):
    """Mock NATS so app startup doesn't try to connect."""
    mock = AsyncMock()
    monkeypatch.setattr("orw_common.nats_client.publish", mock.publish)
    monkeypatch.setattr("orw_common.nats_client.connect", mock.connect)
    monkeypatch.setattr("orw_common.nats_client.close", mock.close)
    monkeypatch.setattr("orw_common.nats_client.ensure_stream", mock.ensure_stream)
    return mock


@pytest.fixture
def mock_redis():
    """Mock Redis client for rate limiting & lockout."""
    r = AsyncMock()
    r.get = AsyncMock(return_value=None)
    r.incr = AsyncMock(return_value=1)
    r.expire = AsyncMock()
    r.setex = AsyncMock()
    r.delete = AsyncMock()
    r.exists = AsyncMock(return_value=0)
    return r


@pytest.fixture
def mock_db_session():
    """Mock async DB session whose execute() returns an empty result.

    Tests that need specific row data should override `session.execute`
    via `monkeypatch` or set up their own MagicMock chain.
    """
    empty_result = MagicMock()
    empty_result.mappings.return_value.first.return_value = None
    empty_result.mappings.return_value.all.return_value = []
    empty_result.first.return_value = None
    empty_result.scalar.return_value = 0

    session = AsyncMock()
    session.execute = AsyncMock(return_value=empty_result)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    return session


@pytest.fixture
async def test_client(mock_nats, mock_redis, mock_db_session, monkeypatch):
    """ASGI test client with Redis + DB mocked.

    Patches Redis at the *use-site* (features.auth.routes), since the
    route module already bound `get_redis_client` at import time. The
    db dependency is overridden through FastAPI's standard mechanism.
    """
    from main import app
    from orw_common.database import get_db

    # Redis is called directly (not via Depends) — must patch the bound symbol.
    monkeypatch.setattr(
        "features.auth.routes.get_redis_client",
        AsyncMock(return_value=mock_redis),
    )

    async def _mock_get_db():
        yield mock_db_session

    app.dependency_overrides[get_db] = _mock_get_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)
