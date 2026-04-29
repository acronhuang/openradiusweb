"""Use-case composition for the network_devices feature (Layer 2).

Two NATS-publishing flows:
- create → publish_poll_requested
- set port VLAN → publish_set_vlan

Port-list and set-vlan both validate the parent network_device first
(NotFoundError if missing).
"""
from typing import Any, Mapping, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.exceptions import NotFoundError
from utils.audit import log_audit

from . import events
from . import repository as repo


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

async def list_network_devices(
    db: AsyncSession,
    *,
    tenant_id: str,
    device_type: Optional[str],
    vendor: Optional[str],
    page: int,
    page_size: int,
) -> dict:
    total = await repo.count_network_devices(
        db, tenant_id=tenant_id, device_type=device_type, vendor=vendor,
    )
    rows = await repo.list_network_devices(
        db, tenant_id=tenant_id, device_type=device_type, vendor=vendor,
        limit=page_size, offset=(page - 1) * page_size,
    )
    return {
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def get_network_device(
    db: AsyncSession, *, tenant_id: str, device_id: UUID,
) -> dict:
    row = await repo.lookup_network_device(
        db, tenant_id=tenant_id, device_id=device_id,
    )
    if not row:
        raise NotFoundError("Network device", str(device_id))
    return dict(row)


async def list_switch_ports(
    db: AsyncSession, *, tenant_id: str, device_id: UUID,
) -> list[dict]:
    if not await repo.network_device_exists(
        db, tenant_id=tenant_id, device_id=device_id,
    ):
        raise NotFoundError("Network device", str(device_id))
    rows = await repo.list_ports_with_connected_device(
        db, network_device_id=device_id,
    )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Mutations (NATS-publishing)
# ---------------------------------------------------------------------------

async def create_network_device(
    db: AsyncSession,
    actor: dict,
    *,
    fields: dict,
    client_ip: Optional[str] = None,
) -> Mapping[str, Any]:
    """Inserts the device, fires `orw.switch.poll_requested`, audits.

    Returns the row dict augmented with `port_count = 0` (newly-created
    devices have no discovered ports yet).
    """
    row = await repo.insert_network_device(
        db,
        tenant_id=actor["tenant_id"],
        ip_address=fields["ip_address"],
        hostname=fields.get("hostname"),
        vendor=fields.get("vendor"),
        model=fields.get("model"),
        os_version=fields.get("os_version"),
        device_type=fields["device_type"],
        management_protocol=fields["management_protocol"],
        snmp_version=fields.get("snmp_version"),
        snmp_community=fields.get("snmp_community"),
        poll_interval_seconds=fields["poll_interval_seconds"],
    )
    await events.publish_poll_requested(
        network_device_id=row["id"],
        ip_address=fields["ip_address"],
    )
    await log_audit(
        db, actor,
        action="create", resource_type="network_device",
        resource_id=str(row["id"]),
        details={
            "hostname": fields.get("hostname"),
            "ip_address": fields["ip_address"],
        },
        ip_address=client_ip,
    )
    out = dict(row)
    out["port_count"] = 0
    return out


async def request_port_vlan_change(
    db: AsyncSession,
    actor: dict,
    *,
    device_id: UUID,
    port_id: UUID,
    vlan_id: int,
    client_ip: Optional[str] = None,
) -> dict:
    port = await repo.lookup_port_for_vlan_set(
        db,
        tenant_id=actor["tenant_id"],
        network_device_id=device_id,
        port_id=port_id,
    )
    if not port:
        raise NotFoundError("Port", str(port_id))

    await events.publish_set_vlan(
        network_device_id=device_id,
        port_id=port_id,
        ip_address=str(port["ip_address"]),
        vendor=port["vendor"],
        port_name=port["port_name"],
        port_index=port["port_index"],
        vlan_id=vlan_id,
        requested_by=actor.get("username"),
    )
    await log_audit(
        db, actor,
        action="set_vlan", resource_type="switch_port",
        resource_id=str(port_id),
        details={
            "device_id": str(device_id),
            "port_name": port["port_name"],
            "vlan_id": vlan_id,
        },
        ip_address=client_ip,
    )
    return {"status": "vlan_change_requested", "vlan_id": vlan_id}


async def delete_network_device(
    db: AsyncSession,
    actor: dict,
    *,
    device_id: UUID,
    client_ip: Optional[str] = None,
) -> None:
    if not await repo.delete_network_device(
        db, tenant_id=actor["tenant_id"], device_id=device_id,
    ):
        raise NotFoundError("Network device", str(device_id))

    await log_audit(
        db, actor,
        action="delete", resource_type="network_device",
        resource_id=str(device_id),
        ip_address=client_ip,
    )
