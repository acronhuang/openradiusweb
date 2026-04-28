"""Network device (switch/router/AP) management routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.database import get_db
from orw_common.models.network_device import (
    NetworkDeviceCreate, NetworkDeviceResponse,
    SwitchPortResponse,
)
from orw_common import nats_client
from middleware.auth import get_current_user, require_operator, require_admin
from utils.audit import log_audit

router = APIRouter(prefix="/network-devices")


@router.get("")
async def list_network_devices(
    device_type: str | None = None,
    vendor: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """List network devices (switches, routers, APs)."""
    conditions = ["tenant_id = :tenant_id"]
    params: dict = {"tenant_id": user["tenant_id"]}

    if device_type:
        conditions.append("device_type = :device_type")
        params["device_type"] = device_type
    if vendor:
        conditions.append("vendor ILIKE :vendor")
        params["vendor"] = f"%{vendor}%"

    where = " AND ".join(conditions)
    offset = (page - 1) * page_size
    params["limit"] = page_size
    params["offset"] = offset

    count_result = await db.execute(
        text(f"SELECT COUNT(*) FROM network_devices WHERE {where}"), params
    )
    total = count_result.scalar()

    result = await db.execute(
        text(
            f"SELECT nd.*, "
            f"(SELECT COUNT(*) FROM switch_ports sp WHERE sp.network_device_id = nd.id) as port_count "
            f"FROM network_devices nd WHERE {where} "
            f"ORDER BY nd.hostname, nd.ip_address LIMIT :limit OFFSET :offset"
        ),
        params,
    )
    rows = result.mappings().all()

    return {
        "items": [NetworkDeviceResponse(**dict(r)) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("", status_code=201)
async def create_network_device(
    req: NetworkDeviceCreate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_operator),
):
    """Add a network device to management."""
    result = await db.execute(
        text(
            "INSERT INTO network_devices "
            "(ip_address, hostname, vendor, model, os_version, device_type, "
            "management_protocol, snmp_version, snmp_community_encrypted, "
            "poll_interval_seconds, tenant_id) "
            "VALUES (:ip_address, :hostname, :vendor, :model, :os_version, "
            ":device_type, :management_protocol, :snmp_version, "
            ":snmp_community, :poll_interval, :tenant_id) "
            "RETURNING *"
        ),
        {
            "ip_address": req.ip_address,
            "hostname": req.hostname,
            "vendor": req.vendor,
            "model": req.model,
            "os_version": req.os_version,
            "device_type": req.device_type,
            "management_protocol": req.management_protocol,
            "snmp_version": req.snmp_version,
            "snmp_community": req.snmp_community,  # TODO: encrypt via Vault
            "poll_interval": req.poll_interval_seconds,
            "tenant_id": user["tenant_id"],
        },
    )
    device = result.mappings().first()

    # Trigger initial poll
    await nats_client.publish("orw.switch.poll_requested", {
        "network_device_id": str(device["id"]),
        "ip_address": req.ip_address,
    })

    await log_audit(db, user, "create", "network_device", str(device["id"]),
                    {"hostname": req.hostname, "ip_address": req.ip_address})

    return NetworkDeviceResponse(**dict(device), port_count=0)


@router.get("/{device_id}")
async def get_network_device(
    device_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Get a specific network device."""
    result = await db.execute(
        text(
            "SELECT nd.*, "
            "(SELECT COUNT(*) FROM switch_ports sp WHERE sp.network_device_id = nd.id) as port_count "
            "FROM network_devices nd "
            "WHERE nd.id = :id AND nd.tenant_id = :tenant_id"
        ),
        {"id": str(device_id), "tenant_id": user["tenant_id"]},
    )
    device = result.mappings().first()
    if not device:
        raise HTTPException(status_code=404, detail="Network device not found")
    return NetworkDeviceResponse(**dict(device))


@router.get("/{device_id}/ports")
async def get_switch_ports(
    device_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Get all ports of a switch."""
    # Verify access
    exists = await db.execute(
        text(
            "SELECT 1 FROM network_devices "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(device_id), "tenant_id": user["tenant_id"]},
    )
    if not exists.first():
        raise HTTPException(status_code=404, detail="Network device not found")

    result = await db.execute(
        text(
            "SELECT sp.*, "
            "json_build_object('id', d.id, 'mac_address', d.mac_address, "
            "'hostname', d.hostname, 'ip_address', d.ip_address) as connected_device "
            "FROM switch_ports sp "
            "LEFT JOIN devices d ON sp.connected_device_id = d.id "
            "WHERE sp.network_device_id = :device_id "
            "ORDER BY sp.port_index, sp.port_name"
        ),
        {"device_id": str(device_id)},
    )
    ports = result.mappings().all()
    return [SwitchPortResponse(**dict(p)) for p in ports]


@router.post("/{device_id}/ports/{port_id}/vlan")
async def set_port_vlan(
    device_id: UUID,
    port_id: UUID,
    vlan_id: int = Query(..., ge=1, le=4094),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_operator),
):
    """Set VLAN on a switch port (triggers SNMP/SSH action)."""
    # Verify access
    result = await db.execute(
        text(
            "SELECT nd.ip_address, nd.vendor, sp.port_name, sp.port_index "
            "FROM network_devices nd "
            "JOIN switch_ports sp ON sp.network_device_id = nd.id "
            "WHERE nd.id = :device_id AND sp.id = :port_id "
            "AND nd.tenant_id = :tenant_id"
        ),
        {
            "device_id": str(device_id),
            "port_id": str(port_id),
            "tenant_id": user["tenant_id"],
        },
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Port not found")

    # Publish VLAN change request to switch management service
    await nats_client.publish("orw.switch.set_vlan", {
        "network_device_id": str(device_id),
        "port_id": str(port_id),
        "ip_address": str(row["ip_address"]),
        "vendor": row["vendor"],
        "port_name": row["port_name"],
        "port_index": row["port_index"],
        "vlan_id": vlan_id,
        "requested_by": user["username"],
    })

    await log_audit(db, user, "set_vlan", "switch_port", str(port_id),
                    {"device_id": str(device_id), "port_name": row["port_name"],
                     "vlan_id": vlan_id})

    return {"status": "vlan_change_requested", "vlan_id": vlan_id}


@router.delete("/{device_id}", status_code=204)
async def delete_network_device(
    device_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Remove a network device from management."""
    result = await db.execute(
        text(
            "DELETE FROM network_devices "
            "WHERE id = :id AND tenant_id = :tenant_id RETURNING id"
        ),
        {"id": str(device_id), "tenant_id": user["tenant_id"]},
    )
    if not result.first():
        raise HTTPException(status_code=404, detail="Network device not found")

    await log_audit(db, user, "delete", "network_device", str(device_id))
