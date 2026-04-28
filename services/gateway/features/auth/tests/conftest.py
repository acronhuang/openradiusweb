"""Auth-feature-specific fixtures.

The universal fixtures (admin_headers, mock_nats, mock_redis,
mock_db_session, JWT env, sys.path) are inherited from
[gateway/conftest.py](../../../conftest.py).

Only `test_client` lives here because it patches Redis at the use-site
(`features.auth.routes.get_redis_client`) — that path is auth-specific
and would not apply to other features.
"""
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def test_client(mock_nats, mock_redis, mock_db_session, monkeypatch):
    """ASGI test client with Redis + DB mocked.

    Patches Redis at the *use-site* (features.auth.routes), since the
    route module already bound `get_redis_client` at import time. The
    db dependency is overridden through FastAPI's standard mechanism.
    """
    from main import app
    from orw_common.database import get_db

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
