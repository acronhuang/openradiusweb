"""Pure unit tests for the vlans service layer.

Mirrors features/auth/tests/test_service.py — these run with no
FastAPI, no DB, no network. They drive the service functions
directly and verify the layered split keeps the logic testable.
"""
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from orw_common.exceptions import NotFoundError, ValidationError

from features.vlans import service
from features.vlans import repository as repo


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def actor():
    return {"sub": str(uuid4()), "tenant_id": str(uuid4())}


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

class TestList:
    @pytest.mark.asyncio
    async def test_list_stringifies_subnet(self, mock_db, actor):
        rows = [
            {"id": uuid4(), "vlan_id": 10, "name": "corp", "description": None,
             "purpose": "corporate", "subnet": "10.0.0.0/24",
             "enabled": True, "created_at": None, "updated_at": None},
        ]
        with patch.object(repo, "list_vlans", AsyncMock(return_value=rows)):
            items = await service.list_vlans(mock_db, tenant_id=actor["tenant_id"])
        assert items[0]["subnet"] == "10.0.0.0/24"

    @pytest.mark.asyncio
    async def test_list_passes_purpose_filter_to_repo(self, mock_db, actor):
        with patch.object(repo, "list_vlans", AsyncMock(return_value=[])) as p:
            await service.list_vlans(
                mock_db, tenant_id=actor["tenant_id"], purpose="guest",
            )
        p.assert_awaited_once_with(
            mock_db, tenant_id=actor["tenant_id"], purpose="guest",
        )


class TestGet:
    @pytest.mark.asyncio
    async def test_get_missing_raises_not_found(self, mock_db, actor):
        vid = uuid4()
        with patch.object(repo, "lookup_vlan", AsyncMock(return_value=None)):
            with pytest.raises(NotFoundError):
                await service.get_vlan(
                    mock_db, tenant_id=actor["tenant_id"], vlan_uuid=vid,
                )

    @pytest.mark.asyncio
    async def test_get_returns_stringified_row(self, mock_db, actor):
        vid = uuid4()
        row = {"id": vid, "vlan_id": 20, "name": "guest", "description": "",
               "purpose": "guest", "subnet": "192.168.10.0/24",
               "enabled": True, "created_at": None, "updated_at": None}
        with patch.object(repo, "lookup_vlan", AsyncMock(return_value=row)):
            out = await service.get_vlan(
                mock_db, tenant_id=actor["tenant_id"], vlan_uuid=vid,
            )
        assert out["subnet"] == "192.168.10.0/24"
        assert out["name"] == "guest"


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

class TestCreate:
    @pytest.mark.asyncio
    async def test_create_inserts_and_audits(self, mock_db, actor):
        vid = uuid4()
        row = {"id": vid, "vlan_id": 30, "name": "iot", "description": None,
               "purpose": "iot", "subnet": None, "enabled": True,
               "created_at": None, "updated_at": None}
        with patch.object(repo, "insert_vlan", AsyncMock(return_value=row)) as ins, \
             patch("features.vlans.service.log_audit", AsyncMock()) as audit:
            out = await service.create_vlan(
                mock_db, actor,
                vlan_id=30, name="iot", description=None, purpose="iot",
                subnet=None, enabled=True, client_ip="1.2.3.4",
            )
        ins.assert_awaited_once()
        audit.assert_awaited_once()
        # Audit was called with the right action / resource type
        kwargs = audit.await_args.kwargs
        assert kwargs["action"] == "create"
        assert kwargs["resource_type"] == "vlan"
        assert out["name"] == "iot"


class TestUpdate:
    @pytest.mark.asyncio
    async def test_update_with_no_fields_raises_validation(self, mock_db, actor):
        with pytest.raises(ValidationError):
            await service.update_vlan(
                mock_db, actor,
                vlan_uuid=uuid4(), updates={}, client_ip=None,
            )

    @pytest.mark.asyncio
    async def test_update_missing_vlan_raises_not_found(self, mock_db, actor):
        with patch.object(repo, "lookup_vlan_summary", AsyncMock(return_value=None)):
            with pytest.raises(NotFoundError):
                await service.update_vlan(
                    mock_db, actor,
                    vlan_uuid=uuid4(),
                    updates={"name": "x"},
                    client_ip=None,
                )

    @pytest.mark.asyncio
    async def test_update_with_no_allowed_columns_raises_validation(
        self, mock_db, actor,
    ):
        existing = {"id": uuid4(), "vlan_id": 10, "name": "old"}
        with patch.object(repo, "lookup_vlan_summary", AsyncMock(return_value=existing)), \
             patch.object(repo, "update_vlan", AsyncMock(side_effect=ValueError("none"))):
            with pytest.raises(ValidationError):
                await service.update_vlan(
                    mock_db, actor,
                    vlan_uuid=uuid4(),
                    updates={"unknown_field": "value"},
                    client_ip=None,
                )

    @pytest.mark.asyncio
    async def test_update_race_returns_not_found(self, mock_db, actor):
        existing = {"id": uuid4(), "vlan_id": 10, "name": "old"}
        with patch.object(repo, "lookup_vlan_summary", AsyncMock(return_value=existing)), \
             patch.object(repo, "update_vlan", AsyncMock(return_value=None)), \
             patch("features.vlans.service.log_audit", AsyncMock()):
            with pytest.raises(NotFoundError):
                await service.update_vlan(
                    mock_db, actor,
                    vlan_uuid=uuid4(),
                    updates={"name": "new"},
                    client_ip=None,
                )


class TestDelete:
    @pytest.mark.asyncio
    async def test_delete_missing_raises_not_found(self, mock_db, actor):
        with patch.object(repo, "lookup_vlan_summary", AsyncMock(return_value=None)):
            with pytest.raises(NotFoundError):
                await service.delete_vlan(
                    mock_db, actor, vlan_uuid=uuid4(), client_ip=None,
                )

    @pytest.mark.asyncio
    async def test_delete_invokes_repo_and_audits(self, mock_db, actor):
        existing = {"id": uuid4(), "vlan_id": 99, "name": "doomed"}
        with patch.object(repo, "lookup_vlan_summary", AsyncMock(return_value=existing)), \
             patch.object(repo, "delete_vlan", AsyncMock()) as dlt, \
             patch("features.vlans.service.log_audit", AsyncMock()) as audit:
            await service.delete_vlan(
                mock_db, actor, vlan_uuid=uuid4(), client_ip="9.9.9.9",
            )
        dlt.assert_awaited_once()
        audit.assert_awaited_once()
        kwargs = audit.await_args.kwargs
        assert kwargs["action"] == "delete"
        assert kwargs["details"]["vlan_id"] == 99
