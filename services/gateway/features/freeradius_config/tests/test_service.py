"""Pure unit tests for the freeradius_config service layer."""
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from features.freeradius_config import service
from features.freeradius_config import repository as repo


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def actor():
    return {"sub": str(uuid4()), "tenant_id": str(uuid4())}


# ---------------------------------------------------------------------------
# get_config_status
# ---------------------------------------------------------------------------

class TestGetStatus:
    @pytest.mark.asyncio
    async def test_needs_apply_true_when_hash_mismatch(self, mock_db, actor):
        rows = [
            {"id": uuid4(), "config_type": "main", "config_name": "radiusd.conf",
             "config_hash": "new-hash", "last_applied_hash": "old-hash"},
        ]
        with patch.object(repo, "list_config_status", AsyncMock(return_value=rows)):
            out = await service.get_config_status(
                mock_db, tenant_id=actor["tenant_id"],
            )
        assert out["needs_apply"] is True
        assert out["total"] == 1

    @pytest.mark.asyncio
    async def test_needs_apply_false_when_hashes_match(self, mock_db, actor):
        rows = [
            {"id": uuid4(), "config_type": "main", "config_name": "radiusd.conf",
             "config_hash": "abc", "last_applied_hash": "abc"},
        ]
        with patch.object(repo, "list_config_status", AsyncMock(return_value=rows)):
            out = await service.get_config_status(
                mock_db, tenant_id=actor["tenant_id"],
            )
        assert out["needs_apply"] is False

    @pytest.mark.asyncio
    async def test_needs_apply_false_for_empty_config_list(self, mock_db, actor):
        with patch.object(repo, "list_config_status", AsyncMock(return_value=[])):
            out = await service.get_config_status(
                mock_db, tenant_id=actor["tenant_id"],
            )
        assert out["needs_apply"] is False
        assert out["total"] == 0

    @pytest.mark.asyncio
    async def test_unhashed_config_does_not_trigger_needs_apply(self, mock_db, actor):
        # Row with config_hash = None (never built) shouldn't claim needs_apply
        rows = [
            {"id": uuid4(), "config_type": "main", "config_name": "x",
             "config_hash": None, "last_applied_hash": None},
        ]
        with patch.object(repo, "list_config_status", AsyncMock(return_value=rows)):
            out = await service.get_config_status(
                mock_db, tenant_id=actor["tenant_id"],
            )
        assert out["needs_apply"] is False


# ---------------------------------------------------------------------------
# preview_config
# ---------------------------------------------------------------------------

class TestPreview:
    @pytest.mark.asyncio
    async def test_returns_configs_and_counts(self, mock_db, actor):
        with patch.object(
            repo, "list_config_preview", AsyncMock(return_value=[]),
        ), patch.object(
            repo, "count_enabled_ldap_servers", AsyncMock(return_value=2),
        ), patch.object(
            repo, "count_enabled_realms", AsyncMock(return_value=3),
        ), patch.object(
            repo, "count_enabled_nas_clients", AsyncMock(return_value=10),
        ), patch.object(
            repo, "count_active_certificates", AsyncMock(return_value=1),
        ):
            out = await service.preview_config(
                mock_db, tenant_id=actor["tenant_id"],
            )
        assert out["source_data"] == {
            "ldap_servers": 2,
            "realms": 3,
            "nas_clients": 10,
            "active_certificates": 1,
        }


# ---------------------------------------------------------------------------
# trigger_apply (NATS publish + audit)
# ---------------------------------------------------------------------------

class TestApply:
    @pytest.mark.asyncio
    async def test_publishes_correct_subject_and_audits(self, mock_db, actor):
        with patch("features.freeradius_config.events.nats_client.publish",
                   AsyncMock()) as pub, \
             patch("features.freeradius_config.service.log_audit",
                   AsyncMock()) as audit:
            out = await service.trigger_apply(
                mock_db, actor, client_ip="1.2.3.4",
            )
        # NATS contract
        subject, payload = pub.await_args.args
        assert subject == "orw.config.freeradius.apply"
        assert payload["action"] == "apply"
        assert payload["tenant_id"] == actor["tenant_id"]
        assert payload["requested_by"] == actor["sub"]
        assert "requested_at" in payload  # ISO timestamp
        # Audit
        audit.assert_awaited_once()
        assert audit.await_args.kwargs["action"] == "freeradius_config_apply"
        assert audit.await_args.kwargs["resource_type"] == "freeradius_config"
        assert audit.await_args.kwargs["ip_address"] == "1.2.3.4"
        # Response
        assert out["status"] == "apply_triggered"


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

class TestHistory:
    @pytest.mark.asyncio
    async def test_pagination_math(self, mock_db, actor):
        with patch.object(repo, "count_config_history", AsyncMock(return_value=42)), \
             patch.object(repo, "list_config_history", AsyncMock(return_value=[])) as lst:
            out = await service.get_history(
                mock_db, tenant_id=actor["tenant_id"],
                page=3, page_size=20,
            )
        assert out == {"items": [], "total": 42, "page": 3, "page_size": 20}
        assert lst.await_args.kwargs["offset"] == 40
