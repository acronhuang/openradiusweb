"""Pure unit tests for the coa service layer.

Verifies the publish + audit contract for each send variant, the bulk
limit, and pagination math for the read endpoints.
"""
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from orw_common.exceptions import ValidationError

from features.coa import service
from features.coa import repository as repo


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def actor():
    return {"sub": str(uuid4()), "tenant_id": str(uuid4()), "username": "alice"}


# ---------------------------------------------------------------------------
# Send (single-target) — three near-identical paths share a helper
# ---------------------------------------------------------------------------

class TestSendByMac:
    @pytest.mark.asyncio
    async def test_publishes_with_mac_field(self, mock_db, actor):
        with patch("features.coa.events.nats_client.publish",
                   AsyncMock()) as pub, \
             patch("features.coa.service.log_audit", AsyncMock()) as audit:
            out = await service.send_coa_by_mac(
                mock_db, actor,
                mac_address="aa:bb:cc:dd:ee:ff",
                action="disconnect",
                vlan_id=None, acl_name=None, reason="incident-42",
            )
        # NATS contract
        subject, payload = pub.await_args.args
        assert subject == "orw.policy.action.coa"
        assert payload["mac_address"] == "aa:bb:cc:dd:ee:ff"
        assert payload["action"] == "disconnect"
        assert payload["requested_by"] == actor["sub"]
        # Audit
        audit.assert_awaited_once()
        assert audit.await_args.kwargs["action"] == "coa_disconnect"
        assert audit.await_args.kwargs["details"]["mac_address"] == "aa:bb:cc:dd:ee:ff"
        # Response shape
        assert out["status"] == "submitted"
        assert "MAC aa:bb:cc:dd:ee:ff" in out["message"]


class TestSendByUsername:
    @pytest.mark.asyncio
    async def test_publishes_with_username_field(self, mock_db, actor):
        with patch("features.coa.events.nats_client.publish",
                   AsyncMock()) as pub, \
             patch("features.coa.service.log_audit", AsyncMock()):
            await service.send_coa_by_username(
                mock_db, actor,
                username="alice@example.com",
                action="reauthenticate",
                vlan_id=42, acl_name=None, reason=None,
            )
        _, payload = pub.await_args.args
        assert payload["username"] == "alice@example.com"
        assert payload["vlan_id"] == 42
        assert "mac_address" not in payload
        assert "session_id" not in payload


class TestSendBySession:
    @pytest.mark.asyncio
    async def test_publishes_with_session_field(self, mock_db, actor):
        with patch("features.coa.events.nats_client.publish",
                   AsyncMock()) as pub, \
             patch("features.coa.service.log_audit", AsyncMock()):
            await service.send_coa_by_session(
                mock_db, actor,
                session_id="ABCD1234",
                action="vlan_change",
                vlan_id=99, acl_name=None, reason=None,
            )
        _, payload = pub.await_args.args
        assert payload["session_id"] == "ABCD1234"
        assert payload["action"] == "vlan_change"


# ---------------------------------------------------------------------------
# Bulk send
# ---------------------------------------------------------------------------

class TestSendBulk:
    @pytest.mark.asyncio
    async def test_over_limit_raises_validation(self, mock_db, actor):
        with pytest.raises(ValidationError, match="100"):
            await service.send_coa_bulk(
                mock_db, actor,
                targets=[f"aa:bb:cc:dd:ee:{i:02x}" for i in range(101)],
                target_type="mac",
                action="disconnect",
                vlan_id=None, reason=None,
            )

    @pytest.mark.asyncio
    async def test_publishes_one_message_per_target(self, mock_db, actor):
        targets = [f"aa:bb:cc:dd:ee:{i:02x}" for i in range(3)]
        with patch("features.coa.events.nats_client.publish",
                   AsyncMock()) as pub, \
             patch("features.coa.service.log_audit",
                   AsyncMock()) as audit:
            out = await service.send_coa_bulk(
                mock_db, actor,
                targets=targets,
                target_type="mac",
                action="disconnect",
                vlan_id=None, reason="mass-quarantine",
            )
        # 3 publishes, 1 batched audit
        assert pub.await_count == 3
        audit.assert_awaited_once()
        details = audit.await_args.kwargs["details"]
        assert details["bulk"] is True
        assert details["target_count"] == 3
        assert out["submitted_count"] == 3

    @pytest.mark.asyncio
    @pytest.mark.parametrize("target_type, expected_field", [
        ("mac", "mac_address"),
        ("session_id", "session_id"),
        ("username", "username"),
    ])
    async def test_target_type_maps_to_payload_field(
        self, mock_db, actor, target_type, expected_field,
    ):
        with patch("features.coa.events.nats_client.publish",
                   AsyncMock()) as pub, \
             patch("features.coa.service.log_audit", AsyncMock()):
            await service.send_coa_bulk(
                mock_db, actor,
                targets=["the-target"],
                target_type=target_type,
                action="disconnect",
                vlan_id=None, reason=None,
            )
        _, payload = pub.await_args.args
        assert payload[expected_field] == "the-target"


# ---------------------------------------------------------------------------
# History + active sessions
# ---------------------------------------------------------------------------

class TestGetHistory:
    @pytest.mark.asyncio
    async def test_pagination_math(self, mock_db, actor):
        with patch.object(repo, "count_coa_history", AsyncMock(return_value=53)), \
             patch.object(repo, "list_coa_history", AsyncMock(return_value=[])) as lst:
            out = await service.get_history(
                mock_db, tenant_id=actor["tenant_id"],
                action=None, page=2, page_size=20,
            )
        assert out == {"items": [], "total": 53, "page": 2, "page_size": 20}
        assert lst.await_args.kwargs["offset"] == 20

    @pytest.mark.asyncio
    async def test_action_filter_passed_through(self, mock_db, actor):
        with patch.object(repo, "count_coa_history", AsyncMock(return_value=0)) as cnt, \
             patch.object(repo, "list_coa_history", AsyncMock(return_value=[])):
            await service.get_history(
                mock_db, tenant_id=actor["tenant_id"],
                action="disconnect", page=1, page_size=10,
            )
        assert cnt.await_args.kwargs["action"] == "disconnect"


class TestListActiveSessions:
    @pytest.mark.asyncio
    async def test_enriches_with_actions_available(self, mock_db):
        rows = [{"id": uuid4(), "username": "alice", "nas_ip": "10.0.0.1"}]
        with patch.object(
            repo, "count_active_sessions", AsyncMock(return_value=1),
        ), patch.object(
            repo, "list_active_sessions", AsyncMock(return_value=rows),
        ):
            out = await service.list_active_sessions(
                mock_db, nas_ip=None, vlan=None, page=1, page_size=50,
            )
        item = out["items"][0]
        assert item["coa_available"] is True
        assert "vlan_change" in item["actions_available"]

    @pytest.mark.asyncio
    async def test_filters_passed_to_repo(self, mock_db):
        with patch.object(
            repo, "count_active_sessions", AsyncMock(return_value=0),
        ) as cnt, patch.object(
            repo, "list_active_sessions", AsyncMock(return_value=[]),
        ):
            await service.list_active_sessions(
                mock_db, nas_ip="10.0.0.1", vlan=42, page=1, page_size=20,
            )
        assert cnt.await_args.kwargs["nas_ip"] == "10.0.0.1"
        assert cnt.await_args.kwargs["vlan"] == 42
