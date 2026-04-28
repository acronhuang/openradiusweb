"""Pure unit tests for the settings service layer.

Validates:
- secret masking on read AND in audit details
- batch update raising ValidationError when no keys match
- service-restart routing (infra → AuthorizationError, unknown → ValidationError,
  known → publishes correct subject)
"""
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from orw_common.exceptions import (
    AuthorizationError,
    NotFoundError,
    ValidationError,
)

from features.settings import service
from features.settings import events
from features.settings import repository as repo


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def actor():
    return {"sub": str(uuid4()), "tenant_id": str(uuid4()), "username": "alice"}


# ---------------------------------------------------------------------------
# Reads + secret masking
# ---------------------------------------------------------------------------

class TestGetAll:
    @pytest.mark.asyncio
    async def test_groups_by_category(self, mock_db, actor):
        rows = [
            {"setting_key": "k1", "setting_value": "v1", "value_type": "str",
             "category": "ui", "description": None, "is_secret": False},
            {"setting_key": "k2", "setting_value": "v2", "value_type": "str",
             "category": "ui", "description": None, "is_secret": False},
            {"setting_key": "k3", "setting_value": "v3", "value_type": "str",
             "category": "auth", "description": None, "is_secret": False},
        ]
        with patch.object(repo, "list_all_settings", AsyncMock(return_value=rows)):
            out = await service.get_all_settings_grouped(
                mock_db, tenant_id=actor["tenant_id"],
            )
        assert set(out["categories"].keys()) == {"ui", "auth"}
        assert len(out["categories"]["ui"]) == 2
        assert len(out["categories"]["auth"]) == 1

    @pytest.mark.asyncio
    async def test_secret_value_masked(self, mock_db, actor):
        rows = [{
            "setting_key": "smtp_password", "setting_value": "real-secret",
            "value_type": "str", "category": "smtp",
            "description": None, "is_secret": True,
        }]
        with patch.object(repo, "list_all_settings", AsyncMock(return_value=rows)):
            out = await service.get_all_settings_grouped(
                mock_db, tenant_id=actor["tenant_id"],
            )
        assert out["categories"]["smtp"][0]["setting_value"] == "********"


class TestGetByCategory:
    @pytest.mark.asyncio
    async def test_empty_raises_not_found(self, mock_db, actor):
        with patch.object(
            repo, "list_settings_by_category", AsyncMock(return_value=[]),
        ):
            with pytest.raises(NotFoundError):
                await service.get_settings_by_category(
                    mock_db, tenant_id=actor["tenant_id"], category="missing",
                )


# ---------------------------------------------------------------------------
# Batch update
# ---------------------------------------------------------------------------

class TestUpdateBatch:
    @pytest.mark.asyncio
    async def test_no_matching_keys_raises_validation(self, mock_db, actor):
        # repo lookup returns nothing for any of the keys → all skipped
        with patch.object(
            repo, "lookup_settings_for_audit", AsyncMock(return_value={}),
        ), patch.object(repo, "update_setting_value", AsyncMock()) as upd:
            with pytest.raises(ValidationError):
                await service.update_settings_batch(
                    mock_db, actor,
                    category="ui",
                    settings_map={"unknown_key": "value"},
                    client_ip=None,
                )
        upd.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_secret_old_and_new_value_masked_in_audit(self, mock_db, actor):
        existing = {
            "smtp_password": {
                "setting_key": "smtp_password",
                "setting_value": "real-secret",
                "is_secret": True,
            },
        }
        with patch.object(
            repo, "lookup_settings_for_audit", AsyncMock(return_value=existing),
        ), patch.object(repo, "update_setting_value", AsyncMock()), \
             patch("features.settings.service.log_audit", AsyncMock()) as audit:
            await service.update_settings_batch(
                mock_db, actor,
                category="smtp",
                settings_map={"smtp_password": "new-real-secret"},
                client_ip=None,
            )
        change = audit.await_args.kwargs["details"]["changes"][0]
        assert change["old_value"] == "********"
        assert change["new_value"] == "********"
        # Critical: real values never reach audit
        assert "real-secret" not in str(audit.await_args.kwargs["details"])

    @pytest.mark.asyncio
    async def test_unknown_keys_silently_skipped(self, mock_db, actor):
        # Two keys requested, only one exists
        existing = {
            "k1": {"setting_key": "k1", "setting_value": "v1", "is_secret": False},
        }
        with patch.object(
            repo, "lookup_settings_for_audit", AsyncMock(return_value=existing),
        ), patch.object(repo, "update_setting_value", AsyncMock()) as upd, \
             patch("features.settings.service.log_audit", AsyncMock()):
            out = await service.update_settings_batch(
                mock_db, actor,
                category="ui",
                settings_map={"k1": "new1", "k_missing": "val"},
                client_ip=None,
            )
        assert out["updated"] == ["k1"]
        upd.assert_awaited_once()


# ---------------------------------------------------------------------------
# Service restart (NATS dispatch)
# ---------------------------------------------------------------------------

class TestRequestServiceRestart:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("infra", ["postgres", "redis", "nats", "frontend"])
    async def test_infra_services_raise_authorization(self, mock_db, actor, infra):
        with pytest.raises(AuthorizationError):
            await service.request_service_restart(
                mock_db, actor, service_name=infra, client_ip=None,
            )

    @pytest.mark.asyncio
    async def test_unknown_service_raises_validation(self, mock_db, actor):
        with pytest.raises(ValidationError):
            await service.request_service_restart(
                mock_db, actor, service_name="nonexistent", client_ip=None,
            )

    @pytest.mark.asyncio
    async def test_known_service_publishes_correct_subject(self, mock_db, actor):
        with patch("features.settings.events.nats_client.publish",
                   AsyncMock()) as pub, \
             patch("features.settings.service.log_audit", AsyncMock()) as audit:
            out = await service.request_service_restart(
                mock_db, actor, service_name="freeradius", client_ip="9.9.9.9",
            )
        pub.assert_awaited_once()
        topic, payload = pub.await_args.args
        assert topic == "orw.service.freeradius.restart"
        assert payload == {"action": "restart", "requested_by": "alice"}
        audit.assert_awaited_once()
        assert audit.await_args.kwargs["action"] == "restart"
        assert out["status"] == "restart_requested"


# ---------------------------------------------------------------------------
# Module-level allowlist invariants
# ---------------------------------------------------------------------------

def test_service_restart_topics_have_consistent_subjects():
    for name, topic in events.SERVICE_RESTART_TOPICS.items():
        assert topic == f"orw.service.{name}.restart"
