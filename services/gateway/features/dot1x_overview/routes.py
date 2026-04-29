"""HTTP routes for the dot1x_overview feature (Layer 3)."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.database import get_db
from middleware.auth import get_current_user

from . import service

router = APIRouter(prefix="/dot1x")


@router.get("/overview")
async def get_dot1x_overview(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Aggregated 802.1X status overview for the current tenant."""
    return await service.get_overview(db, tenant_id=user["tenant_id"])
