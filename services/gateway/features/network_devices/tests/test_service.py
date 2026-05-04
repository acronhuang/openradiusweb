"""Pure unit tests for the network_devices service layer."""
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from orw_common.exceptions import NotFoundError

from features.network_devices import service
from features.network_devices import repository as repo


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def actor():
    return {"sub": str(uuid4()), "tenant_id": str(uuid4()), "username": "alice"}


def _create_fields(**overrides):
    base = {
        "ip_address": "10.0.0.1",
        "hostname": "core-sw1",
        "vendor": "Cisco",
        "model": "Catalyst 9300",
        "os_version": "16.12",
        "device_type": "switch",
        "management_protocol": "ssh",
        "snmp_version": "v3",
        "snmp_community": "topsecret",
        "poll_interval_seconds": 300,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

class TestList:
    @pytest.mark.asyncio
    async def test_pagination_math(self, mock_db, actor):
        with patch.object(repo, "count_network_devices", AsyncMock(return_value=42)), \
             patch.object(repo, "list_network_devices", AsyncMock(return_value=[])) as lst:
            await service.list_network_devices(
                mock_db, tenant_id=actor["tenant_id"],
                device_type="switch", vendor="cisco",
                page=2, page_size=20,
            )
        assert lst.await_args.kwargs["offset"] == 20
        assert lst.await_args.kwargs["device_type"] == "switch"
        assert lst.await_args.kwargs["vendor"] == "cisco"


class TestGet:
    @pytest.mark.asyncio
    async def test_missing_raises_not_found(self, mock_db, actor):
        with patch.object(repo, "lookup_network_device", AsyncMock(return_value=None)):
            with pytest.raises(NotFoundError):
                await service.get_network_device(
                    mock_db, tenant_id=actor["tenant_id"], device_id=uuid4(),
                )


class TestListPorts:
    @pytest.mark.asyncio
    async def test_missing_device_raises_not_found(self, mock_db, actor):
        with patch.object(repo, "network_device_exists", AsyncMock(return_value=False)):
            with pytest.raises(NotFoundError):
                await service.list_switch_ports(
                    mock_db, tenant_id=actor["tenant_id"], device_id=uuid4(),
                )

    @pytest.mark.asyncio
    async def test_existing_returns_dicts(self, mock_db, actor):
        ports = [{"id": uuid4(), "port_name": "Gi1/0/1"}]
        with patch.object(repo, "network_device_exists", AsyncMock(return_value=True)), \
             patch.object(
                 repo, "list_ports_with_connected_device",
                 AsyncMock(return_value=ports),
             ):
            out = await service.list_switch_ports(
                mock_db, tenant_id=actor["tenant_id"], device_id=uuid4(),
            )
        assert out[0]["port_name"] == "Gi1/0/1"


# ---------------------------------------------------------------------------
# Create — publishes orw.switch.poll_requested
# ---------------------------------------------------------------------------

class TestCreate:
    @pytest.mark.asyncio
    async def test_inserts_publishes_audits_and_zeroes_port_count(self, mock_db, actor):
        new_device = {"id": uuid4(), "ip_address": "10.0.0.1", "hostname": "core-sw1"}
        with patch.object(
            repo, "insert_network_device", AsyncMock(return_value=new_device),
        ), patch("features.network_devices.events.nats_client.publish",
                 AsyncMock()) as pub, \
             patch("features.network_devices.service.log_audit",
                   AsyncMock()) as audit:
            out = await service.create_network_device(
                mock_db, actor,
                fields=_create_fields(),
                client_ip="1.2.3.4",
            )
        # NATS contract
        subject, payload = pub.await_args.args
        assert subject == "orw.switch.poll_requested"
        assert payload["network_device_id"] == str(new_device["id"])
        assert payload["ip_address"] == "10.0.0.1"
        # Audit
        audit.assert_awaited_once()
        assert audit.await_args.kwargs["action"] == "create"
        # snmp_community value never reaches audit
        assert "topsecret" not in str(audit.await_args.kwargs["details"])
        # New device starts with 0 ports
        assert out["port_count"] == 0


# ---------------------------------------------------------------------------
# Set port VLAN — publishes orw.switch.set_vlan
# ---------------------------------------------------------------------------

class TestRequestPortVlanChange:
    @pytest.mark.asyncio
    async def test_missing_port_raises_not_found(self, mock_db, actor):
        with patch.object(repo, "lookup_port_for_vlan_set", AsyncMock(return_value=None)):
            with pytest.raises(NotFoundError):
                await service.request_port_vlan_change(
                    mock_db, actor,
                    device_id=uuid4(), port_id=uuid4(), vlan_id=42,
                )

    @pytest.mark.asyncio
    async def test_publishes_full_payload_and_audits(self, mock_db, actor):
        port_row = {
            "ip_address": "10.0.0.1",
            "vendor": "Cisco",
            "port_name": "Gi1/0/24",
            "port_index": 24,
        }
        with patch.object(
            repo, "lookup_port_for_vlan_set", AsyncMock(return_value=port_row),
        ), patch("features.network_devices.events.nats_client.publish",
                 AsyncMock()) as pub, \
             patch("features.network_devices.service.log_audit",
                   AsyncMock()) as audit:
            device_id, port_id = uuid4(), uuid4()
            out = await service.request_port_vlan_change(
                mock_db, actor,
                device_id=device_id, port_id=port_id, vlan_id=99,
            )
        subject, payload = pub.await_args.args
        assert subject == "orw.switch.set_vlan"
        assert payload["network_device_id"] == str(device_id)
        assert payload["port_id"] == str(port_id)
        assert payload["vendor"] == "Cisco"
        assert payload["port_index"] == 24
        assert payload["vlan_id"] == 99
        assert payload["requested_by"] == "alice"
        # Audit on switch_port resource
        assert audit.await_args.kwargs["resource_type"] == "switch_port"
        assert audit.await_args.kwargs["details"]["vlan_id"] == 99
        assert out == {"status": "vlan_change_requested", "vlan_id": 99}


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

class TestDelete:
    @pytest.mark.asyncio
    async def test_missing_raises_not_found(self, mock_db, actor):
        with patch.object(repo, "delete_network_device", AsyncMock(return_value=False)):
            with pytest.raises(NotFoundError):
                await service.delete_network_device(
                    mock_db, actor, device_id=uuid4(),
                )


# ---------------------------------------------------------------------------
# SSH credentials passthrough (PR #100)
# ---------------------------------------------------------------------------

class TestCreateSshCredsPassthrough:
    """The service must forward ssh_username + ssh_password to the
    repo, where they're encrypted before INSERT. Pre-PR-#100 these
    fields were silently dropped at the service layer (the repo's
    insert signature didn't take them); this test pins the fix."""

    @pytest.mark.asyncio
    async def test_ssh_creds_forwarded_to_repo(self, mock_db, actor):
        captured = {}

        async def fake_insert(_db, **kwargs):
            captured.update(kwargs)
            return {"id": uuid4(), "ip_address": "10.0.0.1", "hostname": "x"}

        with patch.object(repo, "insert_network_device", fake_insert), \
             patch("features.network_devices.events.nats_client.publish",
                   AsyncMock()), \
             patch("features.network_devices.service.log_audit", AsyncMock()):
            await service.create_network_device(
                mock_db, actor,
                fields=_create_fields(
                    ssh_username="netadmin",
                    ssh_password="rotated-2026Q2",
                ),
                client_ip="1.2.3.4",
            )
        assert captured["ssh_username"] == "netadmin"
        assert captured["ssh_password"] == "rotated-2026Q2"

    @pytest.mark.asyncio
    async def test_omitted_ssh_creds_become_none(self, mock_db, actor):
        """A device created without SSH creds (e.g. SNMP-only) must
        get None for both fields, not '' or KeyError."""
        captured = {}

        async def fake_insert(_db, **kwargs):
            captured.update(kwargs)
            return {"id": uuid4(), "ip_address": "10.0.0.1", "hostname": "x"}

        with patch.object(repo, "insert_network_device", fake_insert), \
             patch("features.network_devices.events.nats_client.publish",
                   AsyncMock()), \
             patch("features.network_devices.service.log_audit", AsyncMock()):
            await service.create_network_device(
                mock_db, actor,
                fields=_create_fields(),  # no ssh_* keys
                client_ip="1.2.3.4",
            )
        assert captured["ssh_username"] is None
        assert captured["ssh_password"] is None
