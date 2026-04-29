"""HTTP routes for the freeradius_config feature (Layer 3)."""
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.database import get_db
from middleware.auth import require_admin

from . import service

router = APIRouter(prefix="/freeradius")


@router.get("/config")
async def get_config_status(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Get current FreeRADIUS configuration status (admin only)."""
    return await service.get_config_status(db, tenant_id=user.get("tenant_id"))


@router.post("/config/preview")
async def preview_config(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Preview the stored FreeRADIUS configs + source-data counts (admin only)."""
    return await service.preview_config(db, tenant_id=user.get("tenant_id"))


@router.post("/config/apply")
async def apply_config(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Trigger FreeRADIUS configuration regeneration + reload (admin only)."""
    client_ip = request.client.host if request.client else None
    return await service.trigger_apply(db, user, client_ip=client_ip)


@router.get("/config/history")
async def get_config_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Get FreeRADIUS configuration change history (admin only)."""
    return await service.get_history(
        db, tenant_id=user.get("tenant_id"),
        page=page, page_size=page_size,
    )
