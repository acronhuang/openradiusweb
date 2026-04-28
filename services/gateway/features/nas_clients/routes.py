"""HTTP routes for the nas_clients feature (Layer 3)."""
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.database import get_db
from middleware.auth import require_admin, require_operator

from . import service
from .schemas import NASClientCreate, NASClientUpdate

router = APIRouter(prefix="/nas-clients")


def _client_ip(req: Request) -> str | None:
    return req.client.host if req.client else None


@router.get("")
async def list_nas_clients(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_operator),
):
    """List all NAS clients (without shared secrets)."""
    items = await service.list_nas_clients(db, tenant_id=user["tenant_id"])
    return {"items": items}


@router.get("/{nas_id}")
async def get_nas_client(
    nas_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_operator),
):
    """Get a specific NAS client (without shared secret)."""
    return await service.get_nas_client(
        db, tenant_id=user["tenant_id"], nas_id=nas_id,
    )


@router.post("", status_code=201)
async def create_nas_client(
    req: NASClientCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Create a new NAS client (admin only)."""
    return await service.create_nas_client(
        db, user,
        name=req.name,
        ip_address=req.ip_address,
        shared_secret=req.shared_secret,
        shortname=req.shortname,
        nas_type=req.nas_type,
        description=req.description,
        client_ip=_client_ip(request),
    )


@router.put("/{nas_id}")
async def update_nas_client(
    nas_id: UUID,
    req: NASClientUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Update a NAS client (admin only). shared_secret is optional."""
    return await service.update_nas_client(
        db, user,
        nas_id=nas_id,
        updates=req.model_dump(exclude_unset=True),
        client_ip=_client_ip(request),
    )


@router.delete("/{nas_id}", status_code=204)
async def delete_nas_client(
    nas_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Delete a NAS client (admin only)."""
    await service.delete_nas_client(
        db, user,
        nas_id=nas_id,
        client_ip=_client_ip(request),
    )


@router.post("/sync-radius")
async def sync_radius(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Trigger a FreeRADIUS clients.conf regeneration + reload (admin only)."""
    return await service.sync_radius(
        db, user, client_ip=_client_ip(request),
    )
