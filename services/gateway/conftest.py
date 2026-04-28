"""Gateway-wide test fixtures.

This is a *parent* conftest of both `tests/` and `features/`, so pytest
auto-discovers it for any test in the gateway. Per-feature conftests
should keep only feature-specific fixtures (e.g. an ASGI test client
with feature-specific monkeypatches).
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from jose import jwt

# ---------------------------------------------------------------------------
# Import paths — make `gateway` source + `shared/` importable for tests
# regardless of where pytest is invoked from.
# ---------------------------------------------------------------------------
_THIS_DIR = os.path.dirname(__file__)
_GATEWAY = os.path.abspath(_THIS_DIR)
_SHARED = os.path.abspath(os.path.join(_THIS_DIR, "../../shared"))
for p in (_GATEWAY, _SHARED):
    if p not in sys.path:
        sys.path.insert(0, p)

# ---------------------------------------------------------------------------
# Default test environment — set BEFORE any app import.
# ---------------------------------------------------------------------------
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("NATS_URL", "nats://localhost:4222")

_JWT_SECRET = "test-secret-key-for-unit-tests"
_JWT_ALG = "HS256"

ADMIN_USER_ID = str(uuid4())
OPERATOR_USER_ID = str(uuid4())
VIEWER_USER_ID = str(uuid4())
TENANT_ID = str(uuid4())


def _make_token(user_id: str, username: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "tenant_id": TENANT_ID,
        "exp": now + timedelta(hours=1),
        "iat": now,
    }
    return jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALG)


# ---------------------------------------------------------------------------
# RBAC tokens / headers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Infrastructure mocks (DB, Redis, NATS)
# ---------------------------------------------------------------------------

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
    """Generic mocked Redis client (rate limiting, lockout, cache)."""
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
def mock_db():
    """Bare mock DB session (no result setup) — used by audit-helper unit tests
    that want to inspect raw call_args. New tests should prefer
    `mock_db_session` which has sensible empty-result defaults.
    """
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    return session
