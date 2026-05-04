"""Pure unit tests for the backups service layer (read side).

Mocks the repository — exercises orchestration:
  - get_settings returns Pydantic defaults when no row exists
  - get_settings forwards row fields when row exists
  - get_run raises NotFoundError when missing
  - list_runs forwards pagination params
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from orw_common.exceptions import NotFoundError
from features.backups import service
from features.backups import repository as repo


@pytest.fixture
def mock_db():
    return AsyncMock()


@pytest.fixture
def tenant_id():
    return str(uuid4())


class TestGetSettings:
    @pytest.mark.asyncio
    async def test_no_row_returns_pydantic_defaults(self, mock_db, tenant_id):
        with patch.object(repo, "lookup_settings", AsyncMock(return_value=None)):
            out = await service.get_settings(mock_db, tenant_id=tenant_id)
        # Should match the model defaults (matches Phase 1 systemd timer)
        assert out.schedule_cron == "30 2 * * *"
        assert out.keep_days == 7
        assert out.destination_type == "none"
        assert out.destination_configured is False
        assert out.enabled is False

    @pytest.mark.asyncio
    async def test_row_overrides_defaults(self, mock_db, tenant_id):
        row = {
            "schedule_cron": "0 3 * * *",
            "keep_days": 14,
            "destination_type": "rsync",
            "destination_configured": True,
            "enabled": True,
            "created_at": datetime(2026, 5, 1, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 5, 5, tzinfo=timezone.utc),
        }
        with patch.object(repo, "lookup_settings", AsyncMock(return_value=row)):
            out = await service.get_settings(mock_db, tenant_id=tenant_id)
        assert out.schedule_cron == "0 3 * * *"
        assert out.keep_days == 14
        assert out.destination_type == "rsync"
        assert out.destination_configured is True
        assert out.enabled is True


class TestListRuns:
    @pytest.mark.asyncio
    async def test_pagination_params_passed_through(self, mock_db, tenant_id):
        with (
            patch.object(repo, "list_runs", AsyncMock(return_value=[])) as mock_list,
            patch.object(repo, "count_runs", AsyncMock(return_value=0)) as mock_count,
        ):
            out = await service.list_runs(
                mock_db, tenant_id=tenant_id,
                status="ok", page=3, page_size=25,
            )
        mock_list.assert_awaited_once()
        assert mock_list.await_args.kwargs == {
            "tenant_id": tenant_id, "status": "ok",
            "page": 3, "page_size": 25,
        }
        mock_count.assert_awaited_once()
        assert mock_count.await_args.kwargs == {
            "tenant_id": tenant_id, "status": "ok",
        }
        assert out.items == []
        assert out.total == 0
        assert out.page == 3
        assert out.page_size == 25

    @pytest.mark.asyncio
    async def test_returns_run_response_objects(self, mock_db, tenant_id):
        run_row = {
            "id": uuid4(),
            "triggered_by": "schedule",
            "triggered_user_id": None,
            "started_at": datetime(2026, 5, 5, 2, 30, tzinfo=timezone.utc),
            "finished_at": datetime(2026, 5, 5, 2, 30, 18, tzinfo=timezone.utc),
            "duration_seconds": 18,
            "local_status": "ok",
            "local_archive_path": "/opt/openradiusweb/backups/orw-backup-x.tar.gz.gpg",
            "local_archive_size_bytes": 90655,
            "local_error": None,
            "offsite_status": "ok",
            "offsite_error": None,
            "prune_deleted_count": 0,
        }
        with (
            patch.object(repo, "list_runs", AsyncMock(return_value=[run_row])),
            patch.object(repo, "count_runs", AsyncMock(return_value=1)),
        ):
            out = await service.list_runs(mock_db, tenant_id=tenant_id)
        assert out.total == 1
        assert len(out.items) == 1
        assert out.items[0].local_status == "ok"
        assert out.items[0].duration_seconds == 18


class TestGetRun:
    @pytest.mark.asyncio
    async def test_missing_raises_not_found(self, mock_db, tenant_id):
        with patch.object(repo, "lookup_run", AsyncMock(return_value=None)):
            with pytest.raises(NotFoundError):
                await service.get_run(
                    mock_db, tenant_id=tenant_id, run_id=uuid4(),
                )

    @pytest.mark.asyncio
    async def test_existing_returns_response(self, mock_db, tenant_id):
        rid = uuid4()
        row = {
            "id": rid,
            "triggered_by": "manual",
            "triggered_user_id": uuid4(),
            "started_at": datetime(2026, 5, 5, tzinfo=timezone.utc),
            "finished_at": None,
            "duration_seconds": None,
            "local_status": "running",
            "local_archive_path": None,
            "local_archive_size_bytes": None,
            "local_error": None,
            "offsite_status": None,
            "offsite_error": None,
            "prune_deleted_count": 0,
        }
        with patch.object(repo, "lookup_run", AsyncMock(return_value=row)):
            out = await service.get_run(
                mock_db, tenant_id=tenant_id, run_id=rid,
            )
        assert out.id == rid
        assert out.triggered_by == "manual"
        assert out.local_status == "running"
