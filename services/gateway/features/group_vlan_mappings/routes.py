"""HTTP routes for the group_vlan_mappings feature (Layer 3)."""
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.database import get_db
from middleware.auth import get_current_user, require_admin

from . import service
from .schemas import GroupVlanMappingCreate, GroupVlanMappingUpdate

router = APIRouter(prefix="/group-vlan-mappings")


def _client_ip(req: Request) -> str | None:
    return req.client.host if req.client else None


@router.get("")
async def list_mappings(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """List all group-to-VLAN mappings, ordered by priority."""
    return await service.list_mappings(db, tenant_id=user["tenant_id"])


@router.get("/lookup/by-groups")
async def lookup_vlan_by_groups(
    groups: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Look up the highest-priority VLAN for a comma-separated group list.

    Used internally by FreeRADIUS post_auth for dynamic VLAN assignment.
    Authenticated; tenant-scoped.
    """
    return await service.lookup_vlan_for_groups(
        db, tenant_id=user["tenant_id"], groups_csv=groups,
    )


@router.get("/{mapping_id}")
async def get_mapping(
    mapping_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Get a specific group-to-VLAN mapping."""
    return await service.get_mapping(
        db, tenant_id=user["tenant_id"], mapping_id=mapping_id,
    )


@router.post("", status_code=201)
async def create_mapping(
    req: GroupVlanMappingCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Create a new group-to-VLAN mapping (admin only)."""
    return await service.create_mapping(
        db, user,
        group_name=req.group_name,
        vlan_id=req.vlan_id,
        priority=req.priority,
        description=req.description,
        ldap_server_id=req.ldap_server_id,
        enabled=req.enabled,
        client_ip=_client_ip(request),
    )


@router.put("/{mapping_id}")
async def update_mapping(
    mapping_id: UUID,
    req: GroupVlanMappingUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Update a group-to-VLAN mapping (admin only)."""
    return await service.update_mapping(
        db, user,
        mapping_id=mapping_id,
        updates=req.model_dump(exclude_unset=True),
        client_ip=_client_ip(request),
    )


@router.delete("/{mapping_id}", status_code=204)
async def delete_mapping(
    mapping_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Delete a group-to-VLAN mapping (admin only)."""
    await service.delete_mapping(
        db, user,
        mapping_id=mapping_id,
        client_ip=_client_ip(request),
    )
