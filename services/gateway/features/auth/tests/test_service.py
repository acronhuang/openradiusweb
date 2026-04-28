"""Pure unit tests for the auth service layer.

These tests exercise `service.py` directly with mocked DB / Redis /
audit. They are FastAPI-free and run in milliseconds — that is the
payoff of the Layer 2 / Layer 3 split documented in §10.6.
"""
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from orw_common.exceptions import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    RateLimitError,
    ValidationError,
)

from features.auth import service
from features.auth import repository as repo


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def mock_redis():
    r = AsyncMock()
    r.incr = AsyncMock(return_value=1)
    r.expire = AsyncMock()
    r.exists = AsyncMock(return_value=0)
    r.delete = AsyncMock()
    r.setex = AsyncMock()
    return r


# ---------------------------------------------------------------------------
# Login flow
# ---------------------------------------------------------------------------

class TestLogin:
    @pytest.mark.asyncio
    async def test_ip_rate_limit_trips_after_threshold(self, mock_db, mock_redis):
        mock_redis.incr = AsyncMock(return_value=service.IP_RATE_LIMIT + 1)
        with pytest.raises(RateLimitError):
            await service.login(
                mock_db, mock_redis,
                username="alice", password="x", client_ip="1.2.3.4",
            )

    @pytest.mark.asyncio
    async def test_locked_account_rejected(self, mock_db, mock_redis):
        mock_redis.exists = AsyncMock(return_value=1)
        with pytest.raises(RateLimitError):
            await service.login(
                mock_db, mock_redis,
                username="alice", password="x", client_ip="1.2.3.4",
            )

    @pytest.mark.asyncio
    async def test_unknown_user_raises_auth_error(self, mock_db, mock_redis):
        with patch.object(repo, "lookup_user_for_login", AsyncMock(return_value=None)), \
             patch("features.auth.service.log_audit", AsyncMock()):
            with pytest.raises(AuthenticationError):
                await service.login(
                    mock_db, mock_redis,
                    username="ghost", password="x", client_ip="1.2.3.4",
                )

    @pytest.mark.asyncio
    async def test_disabled_user_raises_auth_error(self, mock_db, mock_redis):
        user = {"id": uuid4(), "username": "alice", "password_hash": "h",
                "role": "admin", "tenant_id": uuid4(), "enabled": False}
        with patch.object(repo, "lookup_user_for_login", AsyncMock(return_value=user)), \
             patch("features.auth.service.log_audit", AsyncMock()):
            with pytest.raises(AuthenticationError):
                await service.login(
                    mock_db, mock_redis,
                    username="alice", password="x", client_ip="1.2.3.4",
                )

    @pytest.mark.asyncio
    async def test_successful_login_returns_token_and_clears_lockout(
        self, mock_db, mock_redis,
    ):
        user = {"id": uuid4(), "username": "alice", "password_hash": "h",
                "role": "admin", "tenant_id": uuid4(), "enabled": True}
        with patch.object(repo, "lookup_user_for_login", AsyncMock(return_value=user)), \
             patch.object(repo, "update_last_login", AsyncMock()), \
             patch("features.auth.service.verify_password", return_value=True), \
             patch("features.auth.service.log_audit", AsyncMock()):
            token, expires_in = await service.login(
                mock_db, mock_redis,
                username="alice", password="correct", client_ip="1.2.3.4",
            )
            assert isinstance(token, str) and token.count(".") == 2  # JWT shape
            assert expires_in > 0
            mock_redis.delete.assert_awaited()  # lockout/fail counters cleared


# ---------------------------------------------------------------------------
# User CRUD
# ---------------------------------------------------------------------------

class TestUserMutations:
    @pytest.mark.asyncio
    async def test_create_user_conflicts_when_username_taken(self, mock_db):
        with patch.object(repo, "username_exists", AsyncMock(return_value=True)):
            with pytest.raises(ConflictError):
                await service.create_user(
                    mock_db, {"sub": "actor", "tenant_id": "t"},
                    username="dup", email=None,
                    password="abcdefgh", role="viewer",
                )

    @pytest.mark.asyncio
    async def test_update_user_with_no_fields_raises_validation(self, mock_db):
        with pytest.raises(ValidationError):
            await service.update_user(
                mock_db, {"sub": "actor"},
                user_id=str(uuid4()),
                updates={"email": None, "role": None, "enabled": None},
            )

    @pytest.mark.asyncio
    async def test_update_user_not_found(self, mock_db):
        with patch.object(repo, "update_user_fields", AsyncMock(return_value=None)), \
             patch("features.auth.service.log_audit", AsyncMock()):
            with pytest.raises(NotFoundError):
                await service.update_user(
                    mock_db, {"sub": "actor"},
                    user_id=str(uuid4()),
                    updates={"role": "operator"},
                )

    @pytest.mark.asyncio
    async def test_cannot_delete_self(self, mock_db):
        my_id = str(uuid4())
        with pytest.raises(ValidationError):
            await service.delete_user(
                mock_db, {"sub": my_id}, user_id=my_id,
            )

    @pytest.mark.asyncio
    async def test_delete_user_not_found(self, mock_db):
        with patch.object(repo, "delete_user", AsyncMock(return_value=False)):
            with pytest.raises(NotFoundError):
                await service.delete_user(
                    mock_db, {"sub": str(uuid4())},
                    user_id=str(uuid4()),
                )


# ---------------------------------------------------------------------------
# Self-service
# ---------------------------------------------------------------------------

class TestSelfService:
    @pytest.mark.asyncio
    async def test_change_password_rejects_wrong_current(self, mock_db):
        with patch.object(repo, "lookup_password_hash", AsyncMock(return_value="hash")), \
             patch("features.auth.service.verify_password", return_value=False):
            with pytest.raises(ValidationError):
                await service.change_own_password(
                    mock_db, {"sub": str(uuid4())},
                    current_password="wrong", new_password="newpassword123",
                )

    @pytest.mark.asyncio
    async def test_change_password_user_missing(self, mock_db):
        with patch.object(repo, "lookup_password_hash", AsyncMock(return_value=None)):
            with pytest.raises(NotFoundError):
                await service.change_own_password(
                    mock_db, {"sub": str(uuid4())},
                    current_password="x", new_password="newpassword123",
                )

    @pytest.mark.asyncio
    async def test_get_own_preferences_returns_none_for_no_row(self, mock_db):
        with patch.object(repo, "lookup_user_preferences", AsyncMock(return_value=None)):
            assert await service.get_own_preferences(
                mock_db, user_id=str(uuid4())
            ) is None
