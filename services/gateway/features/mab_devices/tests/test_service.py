"""Pure unit tests for the mab_devices service layer.

Beyond the standard CRUD assertions, two specific behaviors are verified:
- MAC normalization in `check_mac_for_radius` (any format → colon form)
- Bulk import accurately distinguishes created vs. skipped (the legacy
  route always reported skipped=0 because of dead try/except handling)
"""
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from orw_common.exceptions import NotFoundError, ValidationError

from features.mab_devices import service
from features.mab_devices import repository as repo
from features.mab_devices.schemas import MabDeviceBulkItem


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def actor():
    return {"sub": str(uuid4()), "tenant_id": str(uuid4())}


# ---------------------------------------------------------------------------
# MAC normalization (pure)
# ---------------------------------------------------------------------------

class TestMacNormalize:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("aa:bb:cc:dd:ee:ff", "aa:bb:cc:dd:ee:ff"),
            ("AA-BB-CC-DD-EE-FF", "aa:bb:cc:dd:ee:ff"),
            ("aabb.ccdd.eeff", "aa:bb:cc:dd:ee:ff"),  # Cisco format
            ("aabbccddeeff", "aa:bb:cc:dd:ee:ff"),
            ("AA:bb:CC:dd:EE:ff", "aa:bb:cc:dd:ee:ff"),
        ],
    )
    def test_known_formats_normalize_to_colon_lower(self, raw, expected):
        assert service._normalize_mac(raw) == expected

    @pytest.mark.parametrize("bad", ["", "aa:bb", "not-a-mac", "aabbccddeefg" * 2])
    def test_invalid_raises_validation(self, bad):
        with pytest.raises(ValidationError):
            service._normalize_mac(bad)


# ---------------------------------------------------------------------------
# RADIUS check (unauthenticated lookup)
# ---------------------------------------------------------------------------

class TestRadiusCheck:
    @pytest.mark.asyncio
    async def test_invalid_mac_raises_validation(self, mock_db):
        with pytest.raises(ValidationError):
            await service.check_mac_for_radius(mock_db, raw_mac="not-a-mac")

    @pytest.mark.asyncio
    async def test_unknown_mac_raises_not_found(self, mock_db):
        with patch.object(repo, "radius_lookup_mac", AsyncMock(return_value=None)):
            with pytest.raises(NotFoundError):
                await service.check_mac_for_radius(
                    mock_db, raw_mac="AA-BB-CC-DD-EE-FF",
                )

    @pytest.mark.asyncio
    async def test_known_mac_returns_stringified(self, mock_db):
        row = {"id": uuid4(), "mac_address": "aa:bb:cc:dd:ee:ff",
               "name": "kiosk", "device_type": "kiosk",
               "assigned_vlan_id": 30, "enabled": True}
        with patch.object(repo, "radius_lookup_mac",
                          AsyncMock(return_value=row)) as lookup:
            out = await service.check_mac_for_radius(
                mock_db, raw_mac="aabbccddeeff",
            )
        # repo was called with the *normalized* form
        lookup.assert_awaited_once_with(mock_db, normalized_mac="aa:bb:cc:dd:ee:ff")
        assert out["mac_address"] == "aa:bb:cc:dd:ee:ff"
        assert out["name"] == "kiosk"


# ---------------------------------------------------------------------------
# Standard CRUD
# ---------------------------------------------------------------------------

class TestList:
    @pytest.mark.asyncio
    async def test_pagination_math(self, mock_db, actor):
        with patch.object(repo, "count_mab_devices", AsyncMock(return_value=125)), \
             patch.object(repo, "list_mab_devices", AsyncMock(return_value=[])):
            out = await service.list_mab_devices(
                mock_db,
                tenant_id=actor["tenant_id"],
                enabled=None, device_type=None,
                page=2, page_size=50,
            )
        assert out["total"] == 125
        assert out["page"] == 2
        assert out["page_size"] == 50

    @pytest.mark.asyncio
    async def test_passes_filters_to_repo(self, mock_db, actor):
        with patch.object(repo, "count_mab_devices", AsyncMock(return_value=0)), \
             patch.object(repo, "list_mab_devices", AsyncMock(return_value=[])) as lst:
            await service.list_mab_devices(
                mock_db,
                tenant_id=actor["tenant_id"],
                enabled=True, device_type="kiosk",
                page=1, page_size=20,
            )
        lst.assert_awaited_once_with(
            mock_db,
            tenant_id=actor["tenant_id"],
            enabled=True, device_type="kiosk",
            limit=20, offset=0,
        )


class TestGet:
    @pytest.mark.asyncio
    async def test_missing_raises_not_found(self, mock_db, actor):
        with patch.object(repo, "lookup_mab_device", AsyncMock(return_value=None)):
            with pytest.raises(NotFoundError):
                await service.get_mab_device(
                    mock_db, tenant_id=actor["tenant_id"], device_id=uuid4(),
                )


class TestCreate:
    @pytest.mark.asyncio
    async def test_inserts_and_audits(self, mock_db, actor):
        row = {"id": uuid4(), "mac_address": "aa:bb:cc:dd:ee:ff",
               "name": "kiosk", "description": None, "device_type": "kiosk",
               "assigned_vlan_id": 30, "enabled": True, "expiry_date": None,
               "created_at": None, "updated_at": None}
        with patch.object(repo, "insert_mab_device", AsyncMock(return_value=row)), \
             patch("features.mab_devices.service.log_audit", AsyncMock()) as audit:
            out = await service.create_mab_device(
                mock_db, actor,
                mac_address="aa:bb:cc:dd:ee:ff",
                name="kiosk", description=None, device_type="kiosk",
                assigned_vlan_id=30, enabled=True, expiry_date=None,
                client_ip=None,
            )
        audit.assert_awaited_once()
        assert audit.await_args.kwargs["action"] == "create"
        assert out["mac_address"] == "aa:bb:cc:dd:ee:ff"


class TestUpdate:
    @pytest.mark.asyncio
    async def test_no_fields_raises_validation(self, mock_db, actor):
        with pytest.raises(ValidationError):
            await service.update_mab_device(
                mock_db, actor, device_id=uuid4(), updates={}, client_ip=None,
            )

    @pytest.mark.asyncio
    async def test_missing_raises_not_found(self, mock_db, actor):
        with patch.object(repo, "lookup_mab_device_summary", AsyncMock(return_value=None)):
            with pytest.raises(NotFoundError):
                await service.update_mab_device(
                    mock_db, actor,
                    device_id=uuid4(),
                    updates={"name": "x"}, client_ip=None,
                )


class TestDelete:
    @pytest.mark.asyncio
    async def test_missing_raises_not_found(self, mock_db, actor):
        with patch.object(repo, "lookup_mab_device_summary", AsyncMock(return_value=None)):
            with pytest.raises(NotFoundError):
                await service.delete_mab_device(
                    mock_db, actor, device_id=uuid4(), client_ip=None,
                )


# ---------------------------------------------------------------------------
# Bulk import (correctness fix)
# ---------------------------------------------------------------------------

class TestBulkImport:
    @pytest.mark.asyncio
    async def test_counts_created_and_skipped_accurately(self, mock_db, actor):
        # 3 items, repo says first 2 inserted, third was a conflict
        with patch.object(
            repo, "bulk_insert_mab_device",
            AsyncMock(side_effect=[True, True, False]),
        ), patch("features.mab_devices.service.log_audit", AsyncMock()) as audit:
            out = await service.bulk_import(
                mock_db, actor,
                devices=[
                    MabDeviceBulkItem(mac_address="aa:bb:cc:dd:ee:01"),
                    MabDeviceBulkItem(mac_address="aa:bb:cc:dd:ee:02"),
                    MabDeviceBulkItem(mac_address="aa:bb:cc:dd:ee:03"),
                ],
                client_ip=None,
            )
        assert out == {"created": 2, "skipped": 1, "total": 3}
        audit.assert_awaited_once()
        assert audit.await_args.kwargs["details"] == out

    @pytest.mark.asyncio
    async def test_empty_list_audits_zeros(self, mock_db, actor):
        with patch("features.mab_devices.service.log_audit", AsyncMock()) as audit:
            out = await service.bulk_import(
                mock_db, actor, devices=[], client_ip=None,
            )
        assert out == {"created": 0, "skipped": 0, "total": 0}
        audit.assert_awaited_once()
