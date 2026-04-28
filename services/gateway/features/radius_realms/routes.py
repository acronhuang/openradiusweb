"""HTTP routes for the radius_realms feature (Layer 3)."""
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.database import get_db
from middleware.auth import require_admin, require_operator

from . import service
from .schemas import RealmCreate, RealmUpdate

router = APIRouter(prefix="/radius/realms")


def _client_ip(req: Request) -> str | None:
    return req.client.host if req.client else None


@router.get("")
async def list_realms(
    realm_type: str | None = None,
    enabled: bool | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_operator),
):
    """List RADIUS realms. Joins ldap_servers for ldap_server_name."""
    return await service.list_realms(
        db, tenant_id=user["tenant_id"],
        realm_type=realm_type, enabled=enabled,
        page=page, page_size=page_size,
    )


@router.post("", status_code=201)
async def create_realm(
    req: RealmCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Create a new RADIUS realm (admin only)."""
    return await service.create_realm(
        db, user,
        fields=req.model_dump(),
        client_ip=_client_ip(request),
    )


@router.get("/{realm_id}")
async def get_realm(
    realm_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_operator),
):
    """Get a specific RADIUS realm with ldap_server_name."""
    return await service.get_realm(
        db, tenant_id=user["tenant_id"], realm_id=realm_id,
    )


@router.put("/{realm_id}")
async def update_realm(
    realm_id: UUID,
    req: RealmUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Update a RADIUS realm (admin only). proxy_secret is optional."""
    return await service.update_realm(
        db, user,
        realm_id=realm_id,
        updates=req.model_dump(exclude_unset=True),
        client_ip=_client_ip(request),
    )


@router.delete("/{realm_id}", status_code=204)
async def delete_realm(
    realm_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Delete a RADIUS realm (admin only). Refuses if referenced as fallback."""
    await service.delete_realm(
        db, user,
        realm_id=realm_id,
        client_ip=_client_ip(request),
    )
