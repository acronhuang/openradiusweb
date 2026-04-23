"""MAB (MAC Authentication Bypass) device whitelist routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.database import get_db
from orw_common.models.mab_device import MabDeviceCreate, MabDeviceUpdate, MabDeviceBulkItem
from middleware.auth import get_current_user, require_operator, require_admin
from utils.audit import log_audit
from utils.safe_sql import build_safe_set_clause, MAB_DEVICE_UPDATE_COLUMNS

router = APIRouter(prefix="/mab-devices")


# ============================================================
# List / Read
# ============================================================

@router.get("")
async def list_mab_devices(
    enabled: bool | None = None,
    device_type: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """List MAB devices with optional filters."""
    conditions = ["tenant_id = :tenant_id"]
    params: dict = {"tenant_id": user["tenant_id"]}

    if enabled is not None:
        conditions.append("enabled = :enabled")
        params["enabled"] = enabled
    if device_type:
        conditions.append("device_type = :device_type")
        params["device_type"] = device_type

    where = " AND ".join(conditions)
    offset = (page - 1) * page_size
    params["limit"] = page_size
    params["offset"] = offset

    count_result = await db.execute(
        text(f"SELECT COUNT(*) FROM mab_devices WHERE {where}"), params
    )
    total = count_result.scalar()

    result = await db.execute(
        text(
            f"SELECT id, mac_address, name, description, device_type, "
            f"assigned_vlan_id, enabled, expiry_date, "
            f"created_at, updated_at "
            f"FROM mab_devices WHERE {where} "
            f"ORDER BY name, mac_address "
            f"LIMIT :limit OFFSET :offset"
        ),
        params,
    )
    rows = result.mappings().all()
    items = []
    for r in rows:
        item = dict(r)
        item["mac_address"] = str(item["mac_address"])
        items.append(item)

    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/check/{mac_address}")
async def check_mab_device(
    mac_address: str,
    db: AsyncSession = Depends(get_db),
):
    """Quick MAC lookup for FreeRADIUS authorize hook. No auth required (internal)."""
    import re
    raw = re.sub(r"[^0-9a-fA-F]", "", mac_address)
    if len(raw) != 12:
        raise HTTPException(status_code=400, detail="Invalid MAC address")
    normalized = ":".join(raw[i:i+2].lower() for i in range(0, 12, 2))

    result = await db.execute(
        text(
            "SELECT id, mac_address, name, device_type, assigned_vlan_id, enabled "
            "FROM mab_devices "
            "WHERE mac_address = :mac::macaddr "
            "AND enabled = true "
            "AND (expiry_date IS NULL OR expiry_date > NOW()) "
            "LIMIT 1"
        ),
        {"mac": normalized},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="MAC not in MAB whitelist")

    item = dict(row)
    item["mac_address"] = str(item["mac_address"])
    return item


@router.get("/{device_id}")
async def get_mab_device(
    device_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Get a specific MAB device."""
    result = await db.execute(
        text(
            "SELECT id, mac_address, name, description, device_type, "
            "assigned_vlan_id, enabled, expiry_date, "
            "created_at, updated_at "
            "FROM mab_devices WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(device_id), "tenant_id": user["tenant_id"]},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="MAB device not found")
    item = dict(row)
    item["mac_address"] = str(item["mac_address"])
    return item


# ============================================================
# Create / Update / Delete
# ============================================================

@router.post("", status_code=201)
async def create_mab_device(
    req: MabDeviceCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Add a device to the MAB whitelist."""
    result = await db.execute(
        text(
            "INSERT INTO mab_devices "
            "(mac_address, name, description, device_type, "
            "assigned_vlan_id, enabled, expiry_date, tenant_id, created_by) "
            "VALUES (:mac::macaddr, :name, :description, :device_type, "
            ":assigned_vlan_id, :enabled, :expiry_date, :tenant_id, :created_by) "
            "RETURNING id, mac_address, name, description, device_type, "
            "assigned_vlan_id, enabled, expiry_date, created_at, updated_at"
        ),
        {
            "mac": req.mac_address,
            "name": req.name,
            "description": req.description,
            "device_type": req.device_type,
            "assigned_vlan_id": req.assigned_vlan_id,
            "enabled": req.enabled,
            "expiry_date": req.expiry_date,
            "tenant_id": user["tenant_id"],
            "created_by": user["sub"],
        },
    )
    device = result.mappings().first()
    await db.commit()

    client_ip = request.client.host if request.client else None
    await log_audit(
        db=db, user=user, action="create", resource_type="mab_device",
        resource_id=str(device["id"]),
        details={"mac_address": req.mac_address, "name": req.name},
        ip_address=client_ip,
    )
    await db.commit()

    item = dict(device)
    item["mac_address"] = str(item["mac_address"])
    return item


@router.put("/{device_id}")
async def update_mab_device(
    device_id: UUID,
    req: MabDeviceUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Update a MAB device."""
    existing = await db.execute(
        text(
            "SELECT id, mac_address, name FROM mab_devices "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(device_id), "tenant_id": user["tenant_id"]},
    )
    old = existing.mappings().first()
    if not old:
        raise HTTPException(status_code=404, detail="MAB device not found")

    updates = req.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    try:
        set_clause, params = build_safe_set_clause(updates, MAB_DEVICE_UPDATE_COLUMNS)
    except ValueError:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    params["id"] = str(device_id)
    params["tenant_id"] = user["tenant_id"]

    result = await db.execute(
        text(
            f"UPDATE mab_devices SET {set_clause}, updated_at = NOW() "
            f"WHERE id = :id AND tenant_id = :tenant_id "
            f"RETURNING id, mac_address, name, description, device_type, "
            f"assigned_vlan_id, enabled, expiry_date, created_at, updated_at"
        ),
        params,
    )
    updated = result.mappings().first()
    if not updated:
        raise HTTPException(status_code=404, detail="MAB device not found")
    await db.commit()

    client_ip = request.client.host if request.client else None
    await log_audit(
        db=db, user=user, action="update", resource_type="mab_device",
        resource_id=str(device_id),
        details={"changed_fields": updates, "mac": str(old["mac_address"])},
        ip_address=client_ip,
    )
    await db.commit()

    item = dict(updated)
    item["mac_address"] = str(item["mac_address"])
    return item


@router.delete("/{device_id}", status_code=204)
async def delete_mab_device(
    device_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Remove a device from the MAB whitelist."""
    existing = await db.execute(
        text(
            "SELECT mac_address, name FROM mab_devices "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(device_id), "tenant_id": user["tenant_id"]},
    )
    old = existing.mappings().first()
    if not old:
        raise HTTPException(status_code=404, detail="MAB device not found")

    await db.execute(
        text("DELETE FROM mab_devices WHERE id = :id AND tenant_id = :tenant_id"),
        {"id": str(device_id), "tenant_id": user["tenant_id"]},
    )
    await db.commit()

    client_ip = request.client.host if request.client else None
    await log_audit(
        db=db, user=user, action="delete", resource_type="mab_device",
        resource_id=str(device_id),
        details={"mac": str(old["mac_address"]), "name": old["name"]},
        ip_address=client_ip,
    )
    await db.commit()


@router.post("/bulk-import", status_code=201)
async def bulk_import_mab_devices(
    devices: list[MabDeviceBulkItem],
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Bulk import MAB devices. Skips duplicates."""
    created = 0
    skipped = 0
    for dev in devices:
        try:
            await db.execute(
                text(
                    "INSERT INTO mab_devices "
                    "(mac_address, name, device_type, assigned_vlan_id, "
                    "enabled, tenant_id, created_by) "
                    "VALUES (:mac::macaddr, :name, :device_type, "
                    ":assigned_vlan_id, true, :tenant_id, :created_by) "
                    "ON CONFLICT (mac_address, tenant_id) DO NOTHING"
                ),
                {
                    "mac": dev.mac_address,
                    "name": dev.name,
                    "device_type": dev.device_type,
                    "assigned_vlan_id": dev.assigned_vlan_id,
                    "tenant_id": user["tenant_id"],
                    "created_by": user["sub"],
                },
            )
            created += 1
        except Exception:
            skipped += 1

    await db.commit()

    client_ip = request.client.host if request.client else None
    await log_audit(
        db=db, user=user, action="bulk_import", resource_type="mab_device",
        details={"total": len(devices), "created": created, "skipped": skipped},
        ip_address=client_ip,
    )
    await db.commit()

    return {"created": created, "skipped": skipped, "total": len(devices)}
