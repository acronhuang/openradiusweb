"""HTTP routes for the vlans feature (Layer 3).

Each handler is a thin shell: extract input → call `service` → return.
Domain exceptions are translated to HTTP status codes by the global
handler in `gateway.main`.

This is the canonical CRUD route shape for the remaining migrations
(nas_clients, mab_devices, ldap_servers, radius_realms, settings,
group_vlan_mappings, audit, dot1x_overview).
"""
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.database import get_db
from middleware.auth import get_current_user, require_admin

from . import service
from .schemas import VlanCreate, VlanUpdate

router = APIRouter(prefix="/vlans")


def _client_ip(req: Request) -> str | None:
    return req.client.host if req.client else None


@router.get("")
async def list_vlans(
    purpose: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """List all VLANs. Any authenticated user can read (needed for dropdowns)."""
    items = await service.list_vlans(
        db, tenant_id=user["tenant_id"], purpose=purpose,
    )
    return {"items": items}


@router.get("/{vlan_uuid}")
async def get_vlan(
    vlan_uuid: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Get a specific VLAN."""
    return await service.get_vlan(
        db, tenant_id=user["tenant_id"], vlan_uuid=vlan_uuid,
    )


@router.post("", status_code=201)
async def create_vlan(
    req: VlanCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Create a new VLAN (admin only)."""
    return await service.create_vlan(
        db, user,
        vlan_id=req.vlan_id,
        name=req.name,
        description=req.description,
        purpose=req.purpose,
        subnet=req.subnet,
        enabled=req.enabled,
        client_ip=_client_ip(request),
    )


@router.put("/{vlan_uuid}")
async def update_vlan(
    vlan_uuid: UUID,
    req: VlanUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Update a VLAN (admin only)."""
    return await service.update_vlan(
        db, user,
        vlan_uuid=vlan_uuid,
        updates=req.model_dump(exclude_unset=True),
        client_ip=_client_ip(request),
    )


@router.delete("/{vlan_uuid}", status_code=204)
async def delete_vlan(
    vlan_uuid: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Delete a VLAN (admin only)."""
    await service.delete_vlan(
        db, user,
        vlan_uuid=vlan_uuid,
        client_ip=_client_ip(request),
    )
