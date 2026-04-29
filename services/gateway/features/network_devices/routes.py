"""HTTP routes for the network_devices feature (Layer 3)."""
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.database import get_db
from middleware.auth import get_current_user, require_admin, require_operator

from . import service
from .schemas import (
    NetworkDeviceCreate,
    NetworkDeviceResponse,
    SwitchPortResponse,
)

router = APIRouter(prefix="/network-devices")


def _client_ip(req: Request) -> str | None:
    return req.client.host if req.client else None


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
    out = await service.list_network_devices(
        db,
        tenant_id=user["tenant_id"],
        device_type=device_type, vendor=vendor,
        page=page, page_size=page_size,
    )
    return {
        "items": [NetworkDeviceResponse(**r) for r in out["items"]],
        "total": out["total"],
        "page": out["page"],
        "page_size": out["page_size"],
    }


@router.post("", status_code=201)
async def create_network_device(
    req: NetworkDeviceCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_operator),
):
    """Add a network device to management (operator+ only)."""
    device = await service.create_network_device(
        db, user,
        fields=req.model_dump(),
        client_ip=_client_ip(request),
    )
    return NetworkDeviceResponse(**device)


@router.get("/{device_id}")
async def get_network_device(
    device_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Get a specific network device."""
    device = await service.get_network_device(
        db, tenant_id=user["tenant_id"], device_id=device_id,
    )
    return NetworkDeviceResponse(**device)


@router.get("/{device_id}/ports")
async def get_switch_ports(
    device_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Get all ports of a switch."""
    ports = await service.list_switch_ports(
        db, tenant_id=user["tenant_id"], device_id=device_id,
    )
    return [SwitchPortResponse(**p) for p in ports]


@router.post("/{device_id}/ports/{port_id}/vlan")
async def set_port_vlan(
    device_id: UUID,
    port_id: UUID,
    request: Request,
    vlan_id: int = Query(..., ge=1, le=4094),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_operator),
):
    """Set VLAN on a switch port (operator+ only). Triggers SNMP/SSH action."""
    return await service.request_port_vlan_change(
        db, user,
        device_id=device_id,
        port_id=port_id,
        vlan_id=vlan_id,
        client_ip=_client_ip(request),
    )


@router.delete("/{device_id}", status_code=204)
async def delete_network_device(
    device_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Remove a network device from management (admin only)."""
    await service.delete_network_device(
        db, user,
        device_id=device_id,
        client_ip=_client_ip(request),
    )
