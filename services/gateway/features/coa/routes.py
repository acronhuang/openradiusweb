"""HTTP routes for the coa feature (Layer 3)."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.database import get_db
from middleware.auth import get_current_user, require_operator

from . import service
from .schemas import (
    CoAByMacRequest,
    CoABySessionRequest,
    CoABulkRequest,
    CoAByUsernameRequest,
)

router = APIRouter(prefix="/coa")


# ===========================================================================
# Send (single-target)
# ===========================================================================

@router.post("/by-mac")
async def coa_by_mac(
    req: CoAByMacRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_operator),
):
    """Send CoA by MAC address (operator+ only)."""
    return await service.send_coa_by_mac(
        db, user,
        mac_address=req.mac_address,
        action=req.action,
        vlan_id=req.vlan_id,
        acl_name=req.acl_name,
        reason=req.reason,
    )


@router.post("/by-username")
async def coa_by_username(
    req: CoAByUsernameRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_operator),
):
    """Send CoA for all active sessions of a username (operator+ only)."""
    return await service.send_coa_by_username(
        db, user,
        username=req.username,
        action=req.action,
        vlan_id=req.vlan_id,
        acl_name=req.acl_name,
        reason=req.reason,
    )


@router.post("/by-session")
async def coa_by_session(
    req: CoABySessionRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_operator),
):
    """Send CoA for a specific RADIUS session ID (operator+ only)."""
    return await service.send_coa_by_session(
        db, user,
        session_id=req.session_id,
        action=req.action,
        vlan_id=req.vlan_id,
        acl_name=req.acl_name,
        reason=req.reason,
    )


# ===========================================================================
# Send (bulk)
# ===========================================================================

@router.post("/bulk")
async def coa_bulk(
    req: CoABulkRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_operator),
):
    """Send CoA to multiple devices at once (max 100, operator+ only)."""
    return await service.send_coa_bulk(
        db, user,
        targets=req.targets,
        target_type=req.target_type,
        action=req.action,
        vlan_id=req.vlan_id,
        reason=req.reason,
    )


# ===========================================================================
# History + active sessions
# ===========================================================================

@router.get("/history")
async def coa_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    action: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Get CoA event history from audit log."""
    return await service.get_history(
        db,
        tenant_id=user.get("tenant_id"),
        action=action,
        page=page,
        page_size=page_size,
    )


@router.get("/active-sessions")
async def list_active_sessions(
    nas_ip: str | None = None,
    vlan: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """List active RADIUS sessions eligible for CoA."""
    return await service.list_active_sessions(
        db,
        nas_ip=nas_ip,
        vlan=vlan,
        page=page,
        page_size=page_size,
    )
