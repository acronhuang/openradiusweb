"""Device management routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.database import get_db
from orw_common.models.device import (
    DeviceCreate, DeviceUpdate, DeviceResponse,
    DeviceListResponse, DevicePropertyCreate,
)
from orw_common import nats_client
from middleware.auth import get_current_user, require_operator, require_admin
from utils.audit import log_audit
from utils.safe_sql import build_safe_set_clause, DEVICE_UPDATE_COLUMNS

router = APIRouter(prefix="/devices")


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
    conditions = ["tenant_id = :tenant_id"]
    params: dict = {"tenant_id": user["tenant_id"]}

    if status:
        conditions.append("status = :status")
        params["status"] = status
    if device_type:
        conditions.append("device_type = :device_type")
        params["device_type"] = device_type
    if search:
        conditions.append(
            "(hostname ILIKE :search OR ip_address::text LIKE :search "
            "OR mac_address::text LIKE :search OR vendor ILIKE :search)"
        )
        params["search"] = f"%{search}%"

    where = " AND ".join(conditions)

    # Count total
    count_result = await db.execute(
        text(f"SELECT COUNT(*) FROM devices WHERE {where}"), params
    )
    total = count_result.scalar()

    # Fetch page
    offset = (page - 1) * page_size
    params["limit"] = page_size
    params["offset"] = offset

    result = await db.execute(
        text(
            f"SELECT * FROM devices WHERE {where} "
            f"ORDER BY last_seen DESC LIMIT :limit OFFSET :offset"
        ),
        params,
    )
    rows = result.mappings().all()

    return DeviceListResponse(
        items=[DeviceResponse(**dict(r)) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
        pages=(total + page_size - 1) // page_size if total > 0 else 0,
    )


@router.get("/{device_id}", response_model=DeviceResponse)
async def get_device(
    device_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Get a specific device by ID."""
    result = await db.execute(
        text(
            "SELECT * FROM devices WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(device_id), "tenant_id": user["tenant_id"]},
    )
    device = result.mappings().first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return DeviceResponse(**dict(device))


@router.post("", response_model=DeviceResponse, status_code=201)
async def create_device(
    req: DeviceCreate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_operator),
):
    """Create or update a device (upsert by MAC address)."""
    result = await db.execute(
        text(
            "INSERT INTO devices (mac_address, ip_address, hostname, device_type, "
            "os_family, os_version, vendor, model, tenant_id) "
            "VALUES (:mac_address, :ip_address, :hostname, :device_type, "
            ":os_family, :os_version, :vendor, :model, :tenant_id) "
            "ON CONFLICT (mac_address, tenant_id) DO UPDATE SET "
            "ip_address = COALESCE(EXCLUDED.ip_address, devices.ip_address), "
            "hostname = COALESCE(EXCLUDED.hostname, devices.hostname), "
            "last_seen = NOW() "
            "RETURNING *"
        ),
        {
            "mac_address": req.mac_address,
            "ip_address": req.ip_address,
            "hostname": req.hostname,
            "device_type": req.device_type,
            "os_family": req.os_family,
            "os_version": req.os_version,
            "vendor": req.vendor,
            "model": req.model,
            "tenant_id": user["tenant_id"],
        },
    )
    device = result.mappings().first()

    # Publish event
    await nats_client.publish("orw.device.upserted", {
        "device_id": str(device["id"]),
        "mac_address": req.mac_address,
        "ip_address": req.ip_address,
    })

    await log_audit(db, user, "create", "device", str(device["id"]),
                    {"mac_address": req.mac_address, "ip_address": req.ip_address})

    return DeviceResponse(**dict(device))


@router.patch("/{device_id}", response_model=DeviceResponse)
async def update_device(
    device_id: UUID,
    req: DeviceUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_operator),
):
    """Update device fields."""
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    try:
        set_clause, params = build_safe_set_clause(updates, DEVICE_UPDATE_COLUMNS)
    except ValueError:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    params["id"] = str(device_id)
    params["tenant_id"] = user["tenant_id"]

    result = await db.execute(
        text(
            f"UPDATE devices SET {set_clause}, updated_at = NOW() "
            f"WHERE id = :id AND tenant_id = :tenant_id RETURNING *"
        ),
        params,
    )
    device = result.mappings().first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    await log_audit(db, user, "update", "device", str(device_id),
                    {"changed_fields": list(req.model_dump(exclude_none=True).keys())})

    return DeviceResponse(**dict(device))


@router.delete("/{device_id}", status_code=204)
async def delete_device(
    device_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Delete a device (admin only)."""
    result = await db.execute(
        text(
            "DELETE FROM devices WHERE id = :id AND tenant_id = :tenant_id "
            "RETURNING id"
        ),
        {"id": str(device_id), "tenant_id": user["tenant_id"]},
    )
    if not result.first():
        raise HTTPException(status_code=404, detail="Device not found")

    await log_audit(db, user, "delete", "device", str(device_id))


@router.post("/{device_id}/properties", status_code=201)
async def add_device_property(
    device_id: UUID,
    req: DevicePropertyCreate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_operator),
):
    """Add or update a device property."""
    # Verify device exists and belongs to tenant
    exists = await db.execute(
        text(
            "SELECT 1 FROM devices WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(device_id), "tenant_id": user["tenant_id"]},
    )
    if not exists.first():
        raise HTTPException(status_code=404, detail="Device not found")

    await db.execute(
        text(
            "INSERT INTO device_properties (device_id, category, key, value, source, confidence) "
            "VALUES (:device_id, :category, :key, :value, :source, :confidence) "
            "ON CONFLICT (device_id, category, key) DO UPDATE SET "
            "value = EXCLUDED.value, source = EXCLUDED.source, "
            "confidence = EXCLUDED.confidence, updated_at = NOW()"
        ),
        {
            "device_id": str(device_id),
            "category": req.category,
            "key": req.key,
            "value": req.value,
            "source": req.source,
            "confidence": req.confidence,
        },
    )

    await log_audit(db, user, "set_property", "device", str(device_id),
                    {"category": req.category, "key": req.key})

    return {"status": "ok"}


@router.get("/{device_id}/properties")
async def get_device_properties(
    device_id: UUID,
    category: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Get all properties of a device."""
    # Verify device belongs to tenant
    exists = await db.execute(
        text(
            "SELECT 1 FROM devices WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(device_id), "tenant_id": user["tenant_id"]},
    )
    if not exists.first():
        raise HTTPException(status_code=404, detail="Device not found")

    query = "SELECT * FROM device_properties WHERE device_id = :device_id"
    params: dict = {"device_id": str(device_id)}
    if category:
        query += " AND category = :category"
        params["category"] = category

    result = await db.execute(text(query + " ORDER BY category, key"), params)
    props = result.mappings().all()
    return [dict(p) for p in props]
