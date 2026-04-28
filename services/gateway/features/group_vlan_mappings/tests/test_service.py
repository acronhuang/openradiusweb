"""Pure unit tests for the group_vlan_mappings service layer."""
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from orw_common.exceptions import ConflictError, NotFoundError, ValidationError

from features.group_vlan_mappings import service
from features.group_vlan_mappings import repository as repo


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def actor():
    return {"sub": str(uuid4()), "tenant_id": str(uuid4())}


# ---------------------------------------------------------------------------
# List / Get
# ---------------------------------------------------------------------------

class TestList:
    @pytest.mark.asyncio
    async def test_returns_items_and_total(self, mock_db, actor):
        rows = [
            {"id": uuid4(), "group_name": "engineers", "vlan_id": 10, "priority": 100},
            {"id": uuid4(), "group_name": "guests", "vlan_id": 99, "priority": 200},
        ]
        with patch.object(repo, "list_mappings", AsyncMock(return_value=rows)):
            out = await service.list_mappings(mock_db, tenant_id=actor["tenant_id"])
        assert out["total"] == 2
        assert len(out["items"]) == 2


class TestGet:
    @pytest.mark.asyncio
    async def test_missing_raises_not_found(self, mock_db, actor):
        with patch.object(repo, "lookup_mapping", AsyncMock(return_value=None)):
            with pytest.raises(NotFoundError):
                await service.get_mapping(
                    mock_db, tenant_id=actor["tenant_id"], mapping_id=uuid4(),
                )


# ---------------------------------------------------------------------------
# Lookup VLAN by groups (FreeRADIUS post_auth helper)
# ---------------------------------------------------------------------------

class TestLookupByGroups:
    @pytest.mark.asyncio
    async def test_empty_input_returns_match_none(self, mock_db, actor):
        out = await service.lookup_vlan_for_groups(
            mock_db, tenant_id=actor["tenant_id"], groups_csv="",
        )
        assert out == {"match": None}

    @pytest.mark.asyncio
    async def test_whitespace_only_returns_match_none(self, mock_db, actor):
        out = await service.lookup_vlan_for_groups(
            mock_db, tenant_id=actor["tenant_id"], groups_csv=" , , ",
        )
        assert out == {"match": None}

    @pytest.mark.asyncio
    async def test_no_match_returns_match_none(self, mock_db, actor):
        with patch.object(repo, "lookup_vlan_for_groups", AsyncMock(return_value=None)):
            out = await service.lookup_vlan_for_groups(
                mock_db, tenant_id=actor["tenant_id"], groups_csv="unknown",
            )
        assert out == {"match": None}

    @pytest.mark.asyncio
    async def test_match_returned_as_dict(self, mock_db, actor):
        row = {"group_name": "engineers", "vlan_id": 10, "priority": 100}
        with patch.object(
            repo, "lookup_vlan_for_groups", AsyncMock(return_value=row),
        ) as lookup:
            out = await service.lookup_vlan_for_groups(
                mock_db, tenant_id=actor["tenant_id"],
                groups_csv="engineers, ops",
            )
        # Whitespace trimmed, empties dropped
        lookup.assert_awaited_once_with(
            mock_db, tenant_id=actor["tenant_id"], groups=["engineers", "ops"],
        )
        assert out == {"match": dict(row)}


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

class TestCreate:
    @pytest.mark.asyncio
    async def test_duplicate_group_raises_conflict(self, mock_db, actor):
        with patch.object(repo, "group_name_taken", AsyncMock(return_value=True)):
            with pytest.raises(ConflictError):
                await service.create_mapping(
                    mock_db, actor,
                    group_name="engineers", vlan_id=10, priority=100,
                    description=None, ldap_server_id=None, enabled=True,
                    client_ip=None,
                )

    @pytest.mark.asyncio
    async def test_unique_group_inserts_and_audits(self, mock_db, actor):
        row = {"id": uuid4(), "group_name": "engineers", "vlan_id": 10,
               "priority": 100, "description": None, "ldap_server_id": None,
               "enabled": True, "created_at": None, "updated_at": None}
        with patch.object(repo, "group_name_taken", AsyncMock(return_value=False)), \
             patch.object(repo, "insert_mapping", AsyncMock(return_value=row)), \
             patch("features.group_vlan_mappings.service.log_audit",
                   AsyncMock()) as audit:
            out = await service.create_mapping(
                mock_db, actor,
                group_name="engineers", vlan_id=10, priority=100,
                description=None, ldap_server_id=None, enabled=True,
                client_ip="1.2.3.4",
            )
        audit.assert_awaited_once()
        assert audit.await_args.kwargs["action"] == "create"
        assert out["group_name"] == "engineers"


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------

class TestUpdate:
    @pytest.mark.asyncio
    async def test_no_fields_raises_validation(self, mock_db, actor):
        with pytest.raises(ValidationError):
            await service.update_mapping(
                mock_db, actor,
                mapping_id=uuid4(), updates={}, client_ip=None,
            )

    @pytest.mark.asyncio
    async def test_missing_raises_not_found(self, mock_db, actor):
        with patch.object(repo, "lookup_mapping_summary", AsyncMock(return_value=None)):
            with pytest.raises(NotFoundError):
                await service.update_mapping(
                    mock_db, actor,
                    mapping_id=uuid4(), updates={"vlan_id": 50}, client_ip=None,
                )

    @pytest.mark.asyncio
    async def test_rename_to_taken_name_raises_conflict(self, mock_db, actor):
        existing = {"id": uuid4(), "group_name": "engineers", "vlan_id": 10}
        with patch.object(repo, "lookup_mapping_summary",
                          AsyncMock(return_value=existing)), \
             patch.object(repo, "group_name_taken", AsyncMock(return_value=True)):
            with pytest.raises(ConflictError):
                await service.update_mapping(
                    mock_db, actor,
                    mapping_id=uuid4(),
                    updates={"group_name": "ops"},
                    client_ip=None,
                )

    @pytest.mark.asyncio
    async def test_same_name_skips_uniqueness_check(self, mock_db, actor):
        existing = {"id": uuid4(), "group_name": "engineers", "vlan_id": 10}
        new_row = {**existing, "vlan_id": 50, "priority": 100,
                   "description": None, "ldap_server_id": None, "enabled": True,
                   "created_at": None, "updated_at": None}
        # group_name_taken should NOT be called when the name is unchanged
        with patch.object(repo, "lookup_mapping_summary",
                          AsyncMock(return_value=existing)), \
             patch.object(repo, "group_name_taken",
                          AsyncMock(side_effect=AssertionError("must not be called"))), \
             patch.object(repo, "update_mapping", AsyncMock(return_value=new_row)), \
             patch("features.group_vlan_mappings.service.log_audit", AsyncMock()):
            out = await service.update_mapping(
                mock_db, actor,
                mapping_id=uuid4(),
                updates={"group_name": "engineers", "vlan_id": 50},
                client_ip=None,
            )
        assert out["vlan_id"] == 50

    @pytest.mark.asyncio
    async def test_no_allowed_columns_raises_validation(self, mock_db, actor):
        existing = {"id": uuid4(), "group_name": "engineers", "vlan_id": 10}
        with patch.object(repo, "lookup_mapping_summary",
                          AsyncMock(return_value=existing)), \
             patch.object(repo, "update_mapping",
                          AsyncMock(side_effect=ValueError("none"))):
            with pytest.raises(ValidationError):
                await service.update_mapping(
                    mock_db, actor,
                    mapping_id=uuid4(),
                    updates={"unknown_field": "value"},
                    client_ip=None,
                )


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

class TestDelete:
    @pytest.mark.asyncio
    async def test_missing_raises_not_found(self, mock_db, actor):
        with patch.object(repo, "lookup_mapping_summary", AsyncMock(return_value=None)):
            with pytest.raises(NotFoundError):
                await service.delete_mapping(
                    mock_db, actor, mapping_id=uuid4(), client_ip=None,
                )

    @pytest.mark.asyncio
    async def test_delete_audits_with_old_data(self, mock_db, actor):
        existing = {"id": uuid4(), "group_name": "engineers", "vlan_id": 10}
        with patch.object(repo, "lookup_mapping_summary",
                          AsyncMock(return_value=existing)), \
             patch.object(repo, "delete_mapping", AsyncMock()) as dlt, \
             patch("features.group_vlan_mappings.service.log_audit",
                   AsyncMock()) as audit:
            await service.delete_mapping(
                mock_db, actor, mapping_id=uuid4(), client_ip=None,
            )
        dlt.assert_awaited_once()
        audit.assert_awaited_once()
        details = audit.await_args.kwargs["details"]
        assert details["group_name"] == "engineers"
        assert details["vlan_id"] == 10
