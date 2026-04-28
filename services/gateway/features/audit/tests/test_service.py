"""Pure unit tests for the audit service layer.

Read-only feature, so no mutation/audit assertions. Focus is on:
- pagination math + filter wiring (last_hours short-circuit)
- single-row NotFound handling
- export filter passthrough
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from orw_common.exceptions import NotFoundError

from features.audit import service
from features.audit import repository as repo


@pytest.fixture
def mock_db():
    return AsyncMock()


@pytest.fixture
def tenant_id():
    return str(uuid4())


# ---------------------------------------------------------------------------
# Single-row read
# ---------------------------------------------------------------------------

class TestGet:
    @pytest.mark.asyncio
    async def test_missing_raises_not_found(self, mock_db, tenant_id):
        with patch.object(repo, "lookup_audit_log", AsyncMock(return_value=None)):
            with pytest.raises(NotFoundError):
                await service.get_audit_log(
                    mock_db, tenant_id=tenant_id, log_id=uuid4(),
                )

    @pytest.mark.asyncio
    async def test_present_returns_dict(self, mock_db, tenant_id):
        row = {"id": uuid4(), "action": "login_success", "username": "alice"}
        with patch.object(repo, "lookup_audit_log", AsyncMock(return_value=row)):
            out = await service.get_audit_log(
                mock_db, tenant_id=tenant_id, log_id=uuid4(),
            )
        assert out["action"] == "login_success"


# ---------------------------------------------------------------------------
# List + pagination + filter wiring
# ---------------------------------------------------------------------------

class TestList:
    @pytest.mark.asyncio
    async def test_pagination_math(self, mock_db, tenant_id):
        with patch.object(repo, "count_audit_logs", AsyncMock(return_value=237)), \
             patch.object(repo, "list_audit_logs", AsyncMock(return_value=[])) as lst:
            out = await service.list_audit_logs(
                mock_db, tenant_id=tenant_id,
                user_id=None, action=None, resource_type=None, search=None,
                start_time=None, end_time=None, last_hours=None,
                page=3, page_size=50,
            )
        assert out == {"items": [], "total": 237, "page": 3, "page_size": 50}
        # offset = (3 - 1) * 50 = 100
        assert lst.await_args.kwargs["offset"] == 100
        assert lst.await_args.kwargs["limit"] == 50

    @pytest.mark.asyncio
    async def test_last_hours_overrides_start_end(self, mock_db, tenant_id):
        with patch.object(repo, "count_audit_logs", AsyncMock(return_value=0)) as cnt, \
             patch.object(repo, "list_audit_logs", AsyncMock(return_value=[])):
            await service.list_audit_logs(
                mock_db, tenant_id=tenant_id,
                user_id=None, action=None, resource_type=None, search=None,
                start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
                end_time=datetime(2026, 12, 31, tzinfo=timezone.utc),
                last_hours=24,
                page=1, page_size=10,
            )
        filters = cnt.await_args.kwargs["filters"]
        assert "since" in filters
        assert "start_time" not in filters
        assert "end_time" not in filters
        # The since cutoff is roughly 24h ago (allow small clock skew)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        assert abs((filters["since"] - cutoff).total_seconds()) < 5

    @pytest.mark.asyncio
    async def test_no_last_hours_uses_explicit_range(self, mock_db, tenant_id):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = datetime(2026, 1, 2, tzinfo=timezone.utc)
        with patch.object(repo, "count_audit_logs", AsyncMock(return_value=0)) as cnt, \
             patch.object(repo, "list_audit_logs", AsyncMock(return_value=[])):
            await service.list_audit_logs(
                mock_db, tenant_id=tenant_id,
                user_id=None, action=None, resource_type=None, search=None,
                start_time=start, end_time=end, last_hours=None,
                page=1, page_size=10,
            )
        filters = cnt.await_args.kwargs["filters"]
        assert filters["start_time"] == start
        assert filters["end_time"] == end
        assert "since" not in filters

    @pytest.mark.asyncio
    async def test_filters_passed_through(self, mock_db, tenant_id):
        uid = uuid4()
        with patch.object(repo, "count_audit_logs", AsyncMock(return_value=0)) as cnt, \
             patch.object(repo, "list_audit_logs", AsyncMock(return_value=[])):
            await service.list_audit_logs(
                mock_db, tenant_id=tenant_id,
                user_id=uid, action="delete", resource_type="device",
                search="quarantine",
                start_time=None, end_time=None, last_hours=None,
                page=1, page_size=20,
            )
        filters = cnt.await_args.kwargs["filters"]
        assert filters["user_id"] == uid
        assert filters["action"] == "delete"
        assert filters["resource_type"] == "device"
        assert filters["search"] == "quarantine"


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

class TestExport:
    @pytest.mark.asyncio
    async def test_passes_filters_to_repo(self, mock_db, tenant_id):
        start = datetime(2026, 4, 1, tzinfo=timezone.utc)
        end = datetime(2026, 4, 29, tzinfo=timezone.utc)
        with patch.object(
            repo, "list_audit_logs_for_export", AsyncMock(return_value=[]),
        ) as lst:
            out = await service.fetch_audit_logs_for_export(
                mock_db, tenant_id=tenant_id,
                start_time=start, end_time=end,
                action="create", resource_type="user",
            )
        assert out == []
        lst.assert_awaited_once_with(
            mock_db,
            tenant_id=tenant_id,
            start_time=start, end_time=end,
            action="create", resource_type="user",
        )

    @pytest.mark.asyncio
    async def test_returns_dicts(self, mock_db, tenant_id):
        rows = [
            {"id": uuid4(), "action": "create", "username": "admin"},
            {"id": uuid4(), "action": "delete", "username": None},
        ]
        with patch.object(
            repo, "list_audit_logs_for_export", AsyncMock(return_value=rows),
        ):
            out = await service.fetch_audit_logs_for_export(
                mock_db, tenant_id=tenant_id,
                start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
                end_time=datetime(2026, 12, 31, tzinfo=timezone.utc),
                action=None, resource_type=None,
            )
        assert len(out) == 2
        assert out[0]["action"] == "create"
