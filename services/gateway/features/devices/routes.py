"""HTTP routes for the devices feature (Layer 3)."""
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.database import get_db
from middleware.auth import get_current_user, require_admin, require_operator

from . import service
from .schemas import (
    DeviceCreate,
    DeviceListResponse,
    DevicePropertyCreate,
    DeviceResponse,
    DeviceUpdate,
)

router = APIRouter(prefix="/devices")


def _client_ip(req: Request) -> str | None:
    return req.client.host if req.client else None


# ===========================================================================
# Devices CRUD
# ===========================================================================

@router.get("", response_model=DeviceListResponse)
async def list_devices(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    status: str | None = None,
    device_type: str | None = None,
    search: str | None = Query(None, max_length=100),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """List devices with filtering and pagination."""
    out = await service.list_devices(
        db,
        tenant_id=user["tenant_id"],
        status=status, device_type=device_type, search=search,
        page=page, page_size=page_size,
    )
    return DeviceListResponse(
        items=[DeviceResponse(**r) for r in out["items"]],
        total=out["total"],
        page=out["page"],
        page_size=out["page_size"],
        pages=out["pages"],
    )


@router.get("/{device_id}", response_model=DeviceResponse)
async def get_device(
    device_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Get a specific device by ID."""
    device = await service.get_device(
        db, tenant_id=user["tenant_id"], device_id=device_id,
    )
    return DeviceResponse(**device)


@router.post("", response_model=DeviceResponse, status_code=201)
async def create_device(
    req: DeviceCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_operator),
):
    """Create or update a device (UPSERT by MAC address)."""
    device = await service.ingest_device(
        db, user,
        mac_address=req.mac_address,
        ip_address=req.ip_address,
        hostname=req.hostname,
        device_type=req.device_type,
        os_family=req.os_family,
        os_version=req.os_version,
        vendor=req.vendor,
        model=req.model,
        client_ip=_client_ip(request),
    )
    return DeviceResponse(**device)


@router.patch("/{device_id}", response_model=DeviceResponse)
async def update_device(
    device_id: UUID,
    req: DeviceUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_operator),
):
    """Update device fields."""
    device = await service.update_device(
        db, user,
        device_id=device_id,
        updates=req.model_dump(),
        client_ip=_client_ip(request),
    )
    return DeviceResponse(**device)


@router.delete("/{device_id}", status_code=204)
async def delete_device(
    device_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Delete a device (admin only)."""
    await service.delete_device(
        db, user, device_id=device_id, client_ip=_client_ip(request),
    )


# ===========================================================================
# Device properties (EAV)
# ===========================================================================

@router.post("/{device_id}/properties", status_code=201)
async def add_device_property(
    device_id: UUID,
    req: DevicePropertyCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_operator),
):
    """Add or update a device property."""
    return await service.set_device_property(
        db, user,
        device_id=device_id,
        category=req.category,
        key=req.key,
        value=req.value,
        source=req.source,
        confidence=req.confidence,
        client_ip=_client_ip(request),
    )


@router.get("/{device_id}/properties")
async def get_device_properties(
    device_id: UUID,
    category: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Get all properties of a device."""
    return await service.list_device_properties(
        db,
        tenant_id=user["tenant_id"],
        device_id=device_id,
        category=category,
    )
