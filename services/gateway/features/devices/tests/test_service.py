"""Pure unit tests for the devices service layer."""
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from orw_common.exceptions import NotFoundError, ValidationError

from features.devices import service
from features.devices import repository as repo


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
    async def test_pagination_math_with_pages(self, mock_db, actor):
        with patch.object(repo, "count_devices", AsyncMock(return_value=125)), \
             patch.object(repo, "list_devices", AsyncMock(return_value=[])) as lst:
            out = await service.list_devices(
                mock_db, tenant_id=actor["tenant_id"],
                status=None, device_type=None, search=None,
                page=2, page_size=50,
            )
        # 125 / 50 = 3 pages (50, 50, 25)
        assert out["total"] == 125
        assert out["pages"] == 3
        assert lst.await_args.kwargs["offset"] == 50

    @pytest.mark.asyncio
    async def test_empty_result_pages_zero(self, mock_db, actor):
        with patch.object(repo, "count_devices", AsyncMock(return_value=0)), \
             patch.object(repo, "list_devices", AsyncMock(return_value=[])):
            out = await service.list_devices(
                mock_db, tenant_id=actor["tenant_id"],
                status=None, device_type=None, search=None,
                page=1, page_size=50,
            )
        assert out["pages"] == 0


class TestGet:
    @pytest.mark.asyncio
    async def test_missing_raises_not_found(self, mock_db, actor):
        with patch.object(repo, "lookup_device", AsyncMock(return_value=None)):
            with pytest.raises(NotFoundError):
                await service.get_device(
                    mock_db, tenant_id=actor["tenant_id"], device_id=uuid4(),
                )


# ---------------------------------------------------------------------------
# Ingest (UPSERT + NATS publish)
# ---------------------------------------------------------------------------

class TestIngest:
    @pytest.mark.asyncio
    async def test_publishes_and_audits(self, mock_db, actor):
        row = {"id": uuid4(), "mac_address": "aa:bb:cc:dd:ee:ff",
               "ip_address": "10.0.0.5"}
        with patch.object(repo, "upsert_device", AsyncMock(return_value=row)), \
             patch("features.devices.events.nats_client.publish",
                   AsyncMock()) as pub, \
             patch("features.devices.service.log_audit",
                   AsyncMock()) as audit:
            out = await service.ingest_device(
                mock_db, actor,
                mac_address="aa:bb:cc:dd:ee:ff",
                ip_address="10.0.0.5",
                hostname=None, device_type=None,
                os_family=None, os_version=None,
                vendor=None, model=None,
            )
        # NATS contract
        subject, payload = pub.await_args.args
        assert subject == "orw.device.upserted"
        assert payload["mac_address"] == "aa:bb:cc:dd:ee:ff"
        assert payload["ip_address"] == "10.0.0.5"
        assert payload["device_id"] == str(row["id"])
        # Audit
        audit.assert_awaited_once()
        assert audit.await_args.kwargs["action"] == "create"
        assert out["mac_address"] == "aa:bb:cc:dd:ee:ff"


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------

class TestUpdate:
    @pytest.mark.asyncio
    async def test_no_non_none_fields_raises_validation(self, mock_db, actor):
        with pytest.raises(ValidationError):
            await service.update_device(
                mock_db, actor, device_id=uuid4(),
                updates={"hostname": None, "status": None},
                client_ip=None,
            )

    @pytest.mark.asyncio
    async def test_no_allowed_columns_raises_validation(self, mock_db, actor):
        with patch.object(
            repo, "update_device", AsyncMock(side_effect=ValueError("none")),
        ):
            with pytest.raises(ValidationError):
                await service.update_device(
                    mock_db, actor, device_id=uuid4(),
                    updates={"unknown_field": "x"}, client_ip=None,
                )

    @pytest.mark.asyncio
    async def test_missing_raises_not_found(self, mock_db, actor):
        with patch.object(repo, "update_device", AsyncMock(return_value=None)):
            with pytest.raises(NotFoundError):
                await service.update_device(
                    mock_db, actor, device_id=uuid4(),
                    updates={"hostname": "x"}, client_ip=None,
                )

    @pytest.mark.asyncio
    async def test_audit_records_only_non_none_fields(self, mock_db, actor):
        row = {"id": uuid4(), "mac_address": "aa:bb:cc:dd:ee:ff"}
        with patch.object(repo, "update_device", AsyncMock(return_value=row)), \
             patch("features.devices.service.log_audit",
                   AsyncMock()) as audit:
            await service.update_device(
                mock_db, actor, device_id=uuid4(),
                # status is None — should be filtered out
                updates={"hostname": "new", "status": None, "vendor": "Cisco"},
                client_ip=None,
            )
        details = audit.await_args.kwargs["details"]
        assert sorted(details["changed_fields"]) == ["hostname", "vendor"]


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

class TestDelete:
    @pytest.mark.asyncio
    async def test_missing_raises_not_found(self, mock_db, actor):
        with patch.object(repo, "delete_device", AsyncMock(return_value=False)):
            with pytest.raises(NotFoundError):
                await service.delete_device(
                    mock_db, actor, device_id=uuid4(), client_ip=None,
                )


# ---------------------------------------------------------------------------
# Properties (EAV) — both endpoints validate parent exists first
# ---------------------------------------------------------------------------

class TestSetProperty:
    @pytest.mark.asyncio
    async def test_missing_device_raises_not_found(self, mock_db, actor):
        with patch.object(repo, "device_exists", AsyncMock(return_value=False)):
            with pytest.raises(NotFoundError):
                await service.set_device_property(
                    mock_db, actor,
                    device_id=uuid4(),
                    category="hardware", key="cpu_cores", value="8",
                    source="nmap", confidence=0.9,
                    client_ip=None,
                )

    @pytest.mark.asyncio
    async def test_existing_device_upserts_and_audits(self, mock_db, actor):
        with patch.object(repo, "device_exists", AsyncMock(return_value=True)), \
             patch.object(repo, "upsert_device_property", AsyncMock()) as ups, \
             patch("features.devices.service.log_audit", AsyncMock()) as audit:
            await service.set_device_property(
                mock_db, actor,
                device_id=uuid4(),
                category="hardware", key="cpu_cores", value="8",
                source="nmap", confidence=0.9,
                client_ip=None,
            )
        ups.assert_awaited_once()
        details = audit.await_args.kwargs["details"]
        assert details == {"category": "hardware", "key": "cpu_cores"}


class TestListProperties:
    @pytest.mark.asyncio
    async def test_missing_device_raises_not_found(self, mock_db, actor):
        with patch.object(repo, "device_exists", AsyncMock(return_value=False)):
            with pytest.raises(NotFoundError):
                await service.list_device_properties(
                    mock_db, tenant_id=actor["tenant_id"],
                    device_id=uuid4(), category=None,
                )

    @pytest.mark.asyncio
    async def test_category_filter_passed_through(self, mock_db, actor):
        with patch.object(repo, "device_exists", AsyncMock(return_value=True)), \
             patch.object(
                 repo, "list_device_properties", AsyncMock(return_value=[]),
             ) as lst:
            await service.list_device_properties(
                mock_db, tenant_id=actor["tenant_id"],
                device_id=uuid4(), category="hardware",
            )
        assert lst.await_args.kwargs["category"] == "hardware"
