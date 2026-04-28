"""Pure unit tests for the nas_clients service layer.

Adds two patterns over the vlans test suite:
- secret-masking in audit (`shared_secret` never reaches the audit log)
- NATS publish (`sync_radius` calls events.publish_freeradius_apply)
"""
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from orw_common.exceptions import NotFoundError, ValidationError

from features.nas_clients import service
from features.nas_clients import repository as repo


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def actor():
    return {"sub": str(uuid4()), "tenant_id": str(uuid4()), "username": "alice"}


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

class TestList:
    @pytest.mark.asyncio
    async def test_returns_dicts(self, mock_db, actor):
        rows = [{"id": uuid4(), "name": "n1", "ip_address": "1.1.1.1"}]
        with patch.object(repo, "list_nas_clients", AsyncMock(return_value=rows)):
            items = await service.list_nas_clients(mock_db, tenant_id=actor["tenant_id"])
        assert items[0]["name"] == "n1"


class TestGet:
    @pytest.mark.asyncio
    async def test_missing_raises_not_found(self, mock_db, actor):
        with patch.object(repo, "lookup_nas_client", AsyncMock(return_value=None)):
            with pytest.raises(NotFoundError):
                await service.get_nas_client(
                    mock_db, tenant_id=actor["tenant_id"], nas_id=uuid4(),
                )


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

class TestCreate:
    @pytest.mark.asyncio
    async def test_create_inserts_and_audits(self, mock_db, actor):
        row = {"id": uuid4(), "name": "n1", "ip_address": "1.1.1.1"}
        with patch.object(repo, "insert_nas_client", AsyncMock(return_value=row)) as ins, \
             patch("features.nas_clients.service.log_audit", AsyncMock()) as audit:
            out = await service.create_nas_client(
                mock_db, actor,
                name="n1", ip_address="1.1.1.1",
                shared_secret="topsecret", shortname=None,
                nas_type="other", description=None, client_ip="9.9.9.9",
            )
        ins.assert_awaited_once()
        audit.assert_awaited_once()
        kwargs = audit.await_args.kwargs
        assert kwargs["action"] == "create"
        assert kwargs["resource_type"] == "nas_client"
        # Secret never reaches audit details on create
        assert "shared_secret" not in kwargs["details"]
        assert out["name"] == "n1"


class TestUpdate:
    @pytest.mark.asyncio
    async def test_no_fields_raises_validation(self, mock_db, actor):
        with pytest.raises(ValidationError):
            await service.update_nas_client(
                mock_db, actor,
                nas_id=uuid4(), updates={}, client_ip=None,
            )

    @pytest.mark.asyncio
    async def test_missing_raises_not_found(self, mock_db, actor):
        with patch.object(repo, "lookup_nas_client_summary", AsyncMock(return_value=None)):
            with pytest.raises(NotFoundError):
                await service.update_nas_client(
                    mock_db, actor,
                    nas_id=uuid4(), updates={"name": "x"}, client_ip=None,
                )

    @pytest.mark.asyncio
    async def test_no_allowed_columns_raises_validation(self, mock_db, actor):
        existing = {"id": uuid4(), "name": "old"}
        with patch.object(repo, "lookup_nas_client_summary", AsyncMock(return_value=existing)), \
             patch.object(repo, "update_nas_client", AsyncMock(side_effect=ValueError("none"))):
            with pytest.raises(ValidationError):
                await service.update_nas_client(
                    mock_db, actor,
                    nas_id=uuid4(),
                    updates={"unknown_field": "value"},
                    client_ip=None,
                )

    @pytest.mark.asyncio
    async def test_secret_is_masked_in_audit(self, mock_db, actor):
        existing = {"id": uuid4(), "name": "old"}
        new_row = {"id": uuid4(), "name": "old", "ip_address": "1.1.1.1"}
        with patch.object(repo, "lookup_nas_client_summary", AsyncMock(return_value=existing)), \
             patch.object(repo, "update_nas_client", AsyncMock(return_value=new_row)), \
             patch("features.nas_clients.service.log_audit", AsyncMock()) as audit:
            await service.update_nas_client(
                mock_db, actor,
                nas_id=uuid4(),
                updates={"shared_secret": "newsecret123", "name": "new"},
                client_ip=None,
            )
        details = audit.await_args.kwargs["details"]
        assert details["changed_fields"]["shared_secret"] == "********"
        assert details["changed_fields"]["name"] == "new"
        # Critical: real secret value never made it into audit
        assert "newsecret123" not in str(details)


class TestDelete:
    @pytest.mark.asyncio
    async def test_missing_raises_not_found(self, mock_db, actor):
        with patch.object(repo, "lookup_nas_client_summary", AsyncMock(return_value=None)):
            with pytest.raises(NotFoundError):
                await service.delete_nas_client(
                    mock_db, actor, nas_id=uuid4(), client_ip=None,
                )


# ---------------------------------------------------------------------------
# NATS publish
# ---------------------------------------------------------------------------

class TestSyncRadius:
    @pytest.mark.asyncio
    async def test_publishes_event_and_audits(self, mock_db, actor):
        with patch("features.nas_clients.events.nats_client.publish",
                   AsyncMock()) as pub, \
             patch("features.nas_clients.service.log_audit", AsyncMock()) as audit:
            out = await service.sync_radius(
                mock_db, actor, client_ip="1.2.3.4",
            )
        # Subject + payload contract
        pub.assert_awaited_once()
        subject, payload = pub.await_args.args
        assert subject == "orw.config.freeradius.apply"
        assert payload == {"triggered_by": "alice", "action": "reload_nas_clients"}
        # Audit recorded the sync action
        audit.assert_awaited_once()
        assert audit.await_args.kwargs["action"] == "sync"
        assert out["status"] == "sync_requested"
