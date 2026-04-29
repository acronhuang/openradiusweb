"""Pure unit tests for the radius_auth_log service layer.

Focus areas:
  - filter normalization (last_hours wins over start/end)
  - sort whitelist enforcement
  - pagination math (page count, ``pages == 0`` when total == 0)
  - troubleshooting resolution gates (success vs failure, missing reason)
  - export validation (start/end required)
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from features.radius_auth_log import service
from features.radius_auth_log import repository as repo
from orw_common.exceptions import NotFoundError, ValidationError


# ---------------------------------------------------------------------------
# _normalize_filters
# ---------------------------------------------------------------------------

class TestNormalizeFilters:
    def test_last_hours_overrides_start_end(self):
        out = service._normalize_filters({
            "last_hours": 12,
            "start_time": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "end_time": datetime(2026, 2, 1, tzinfo=timezone.utc),
            "username": "alice",
        })
        # since computed; start/end stripped; passthrough kept
        assert "since" in out
        assert "start_time" not in out
        assert "end_time" not in out
        assert out["username"] == "alice"

    def test_no_last_hours_passes_filters_through(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        out = service._normalize_filters({
            "start_time": start, "auth_result": "reject",
        })
        assert out["start_time"] == start
        assert out["auth_result"] == "reject"
        assert "since" not in out


# ---------------------------------------------------------------------------
# list_logs — sort whitelist + pagination math
# ---------------------------------------------------------------------------

class TestListLogs:
    @pytest.mark.asyncio
    async def test_invalid_sort_field_falls_back_to_timestamp(self):
        with patch.object(repo, "count_logs", AsyncMock(return_value=0)) as cnt, \
             patch.object(repo, "list_logs", AsyncMock(return_value=[])) as lst:
            await service.list_logs(
                AsyncMock(),
                page=1, page_size=10,
                sort_by="DROP TABLE x", sort_order="desc",
                filters={},
            )
        cnt.assert_awaited_once()
        assert lst.await_args.kwargs["sort_by"] == "timestamp"

    @pytest.mark.asyncio
    async def test_invalid_sort_order_falls_back_to_desc(self):
        with patch.object(repo, "count_logs", AsyncMock(return_value=0)), \
             patch.object(repo, "list_logs", AsyncMock(return_value=[])) as lst:
            await service.list_logs(
                AsyncMock(),
                page=1, page_size=10,
                sort_by="timestamp", sort_order="garbage",
                filters={},
            )
        assert lst.await_args.kwargs["sort_order"] == "desc"

    @pytest.mark.asyncio
    async def test_pagination_pages_zero_on_empty(self):
        with patch.object(repo, "count_logs", AsyncMock(return_value=0)), \
             patch.object(repo, "list_logs", AsyncMock(return_value=[])):
            out = await service.list_logs(
                AsyncMock(),
                page=1, page_size=50,
                sort_by="timestamp", sort_order="desc",
                filters={},
            )
        assert out == {
            "items": [], "total": 0, "page": 1,
            "page_size": 50, "pages": 0,
        }

    @pytest.mark.asyncio
    async def test_pagination_round_up(self):
        with patch.object(repo, "count_logs", AsyncMock(return_value=151)), \
             patch.object(repo, "list_logs", AsyncMock(return_value=[])):
            out = await service.list_logs(
                AsyncMock(),
                page=2, page_size=50,
                sort_by="timestamp", sort_order="desc",
                filters={},
            )
        assert out["pages"] == 4  # ceil(151/50) = 4
        assert out["page"] == 2

    @pytest.mark.asyncio
    async def test_offset_computed_from_page(self):
        with patch.object(repo, "count_logs", AsyncMock(return_value=200)), \
             patch.object(repo, "list_logs", AsyncMock(return_value=[])) as lst:
            await service.list_logs(
                AsyncMock(),
                page=3, page_size=20,
                sort_by="timestamp", sort_order="asc",
                filters={},
            )
        assert lst.await_args.kwargs["offset"] == 40
        assert lst.await_args.kwargs["limit"] == 20


# ---------------------------------------------------------------------------
# get_log_detail
# ---------------------------------------------------------------------------

class TestGetLogDetail:
    @pytest.mark.asyncio
    async def test_raises_not_found_when_missing(self):
        with patch.object(repo, "get_log_by_id", AsyncMock(return_value=None)):
            with pytest.raises(NotFoundError):
                await service.get_log_detail(AsyncMock(), log_id=uuid4())

    @pytest.mark.asyncio
    async def test_success_skips_troubleshooting(self):
        log_id = uuid4()
        entry = {
            "id": log_id, "auth_result": "success",
            "failure_reason": None, "calling_station_id": None,
        }
        with patch.object(repo, "get_log_by_id", AsyncMock(return_value=entry)), \
             patch.object(repo, "find_failure_catalog_entry",
                          AsyncMock()) as catalog, \
             patch.object(repo, "list_related_by_mac",
                          AsyncMock(return_value=[])):
            out = await service.get_log_detail(AsyncMock(), log_id=log_id)
        assert out["troubleshooting"] is None
        catalog.assert_not_awaited()
        assert out["related_history"] == []

    @pytest.mark.asyncio
    async def test_failure_with_no_reason_skips_troubleshooting(self):
        log_id = uuid4()
        entry = {
            "id": log_id, "auth_result": "reject",
            "failure_reason": None, "calling_station_id": None,
        }
        with patch.object(repo, "get_log_by_id", AsyncMock(return_value=entry)), \
             patch.object(repo, "find_failure_catalog_entry",
                          AsyncMock()) as catalog, \
             patch.object(repo, "list_related_by_mac",
                          AsyncMock(return_value=[])):
            out = await service.get_log_detail(AsyncMock(), log_id=log_id)
        assert out["troubleshooting"] is None
        catalog.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failure_resolves_catalog_entry(self):
        log_id = uuid4()
        entry = {
            "id": log_id, "auth_result": "reject",
            "failure_reason": "bad password", "ad_error_code": "0xC000006A",
            "calling_station_id": "aa:bb:cc:dd:ee:ff",
        }
        catalog_row = {
            "category": "credential",
            "description": "Wrong password",
            "possible_causes": ["typo"],
            "remediation_steps": ["reset password"],
            "severity": "low",
            "kb_url": "https://example/kb/1",
        }
        with patch.object(repo, "get_log_by_id", AsyncMock(return_value=entry)), \
             patch.object(repo, "find_failure_catalog_entry",
                          AsyncMock(return_value=catalog_row)) as catalog, \
             patch.object(repo, "list_related_by_mac",
                          AsyncMock(return_value=[{"id": uuid4()}])):
            out = await service.get_log_detail(AsyncMock(), log_id=log_id)
        # Catalog called with code (preferring ad_error_code)
        assert catalog.await_args.kwargs["code"] == "0xC000006A"
        assert out["troubleshooting"]["category"] == "credential"
        assert out["troubleshooting"]["kb_url"] == "https://example/kb/1"
        assert len(out["related_history"]) == 1

    @pytest.mark.asyncio
    async def test_no_calling_station_id_skips_related_lookup(self):
        log_id = uuid4()
        entry = {
            "id": log_id, "auth_result": "reject",
            "failure_reason": "x", "ad_error_code": None,
            "calling_station_id": None,
        }
        with patch.object(repo, "get_log_by_id", AsyncMock(return_value=entry)), \
             patch.object(repo, "find_failure_catalog_entry",
                          AsyncMock(return_value=None)), \
             patch.object(repo, "list_related_by_mac",
                          AsyncMock()) as related:
            out = await service.get_log_detail(AsyncMock(), log_id=log_id)
        related.assert_not_awaited()
        assert out["related_history"] == []


# ---------------------------------------------------------------------------
# get_summary_stats — fraction math + composition
# ---------------------------------------------------------------------------

class TestSummaryStats:
    @pytest.mark.asyncio
    async def test_zero_total_no_division_by_zero(self):
        with patch.object(repo, "count_by_result", AsyncMock(return_value={})), \
             patch.object(repo, "top_failure_reasons", AsyncMock(return_value=[])), \
             patch.object(repo, "top_failing_users", AsyncMock(return_value=[])), \
             patch.object(repo, "top_failing_macs", AsyncMock(return_value=[])), \
             patch.object(repo, "auth_method_distribution", AsyncMock(return_value=[])), \
             patch.object(repo, "hourly_trend", AsyncMock(return_value=[])):
            out = await service.get_summary_stats(AsyncMock(), last_hours=24)
        assert out["total_attempts"] == 0
        assert out["success_rate"] == 0
        assert out["period_hours"] == 24

    @pytest.mark.asyncio
    async def test_success_rate_rounded(self):
        with patch.object(repo, "count_by_result",
                          AsyncMock(return_value={"success": 5, "reject": 2})), \
             patch.object(repo, "top_failure_reasons", AsyncMock(return_value=[])), \
             patch.object(repo, "top_failing_users", AsyncMock(return_value=[])), \
             patch.object(repo, "top_failing_macs", AsyncMock(return_value=[])), \
             patch.object(repo, "auth_method_distribution", AsyncMock(return_value=[])), \
             patch.object(repo, "hourly_trend", AsyncMock(return_value=[])):
            out = await service.get_summary_stats(AsyncMock(), last_hours=1)
        assert out["total_attempts"] == 7
        assert out["success_count"] == 5
        assert out["failure_count"] == 2
        assert out["success_rate"] == 71.4  # 5/7 -> 71.428...


# ---------------------------------------------------------------------------
# export_logs — validation
# ---------------------------------------------------------------------------

class TestExport:
    @pytest.mark.asyncio
    async def test_missing_start_raises_validation(self):
        with pytest.raises(ValidationError):
            await service.export_logs(
                AsyncMock(),
                start_time=None,
                end_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )

    @pytest.mark.asyncio
    async def test_missing_end_raises_validation(self):
        with pytest.raises(ValidationError):
            await service.export_logs(
                AsyncMock(),
                start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
                end_time=None,
            )

    @pytest.mark.asyncio
    async def test_passes_filters_to_repo(self):
        with patch.object(repo, "list_logs_for_export",
                          AsyncMock(return_value=[{"id": "x"}])) as lst:
            out = await service.export_logs(
                AsyncMock(),
                start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
                end_time=datetime(2026, 2, 1, tzinfo=timezone.utc),
                auth_result="reject",
            )
        assert out == [{"id": "x"}]
        filters_arg = lst.await_args.args[1]
        assert filters_arg["auth_result"] == "reject"
        assert "start_time" in filters_arg
        assert "end_time" in filters_arg
