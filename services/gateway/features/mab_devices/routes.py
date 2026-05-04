"""HTTP routes for the mab_devices feature (Layer 3).

Route declaration order matters in FastAPI. All static-path routes
(/check/..., /bulk-import, /import-csv, /export-csv) MUST be declared
BEFORE the /{device_id} catch-all, otherwise a request like
GET /export-csv matches /{device_id} first and returns
`uuid_parsing` 422 instead of running the export. PR #98 reorganised
the file after exactly that bug bit production right after PR #97
deploy.
"""
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.database import get_db
from middleware.auth import get_current_user, require_admin

from . import service
from .schemas import MabDeviceBulkItem, MabDeviceCreate, MabDeviceUpdate

router = APIRouter(prefix="/mab-devices")


def _client_ip(req: Request) -> str | None:
    return req.client.host if req.client else None


# ---------------------------------------------------------------------------
# List + create on the collection
# ---------------------------------------------------------------------------

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
    return await service.list_mab_devices(
        db,
        tenant_id=user["tenant_id"],
        enabled=enabled,
        device_type=device_type,
        page=page,
        page_size=page_size,
    )


@router.post("", status_code=201)
async def create_mab_device(
    req: MabDeviceCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Add a device to the MAB whitelist (admin only)."""
    return await service.create_mab_device(
        db, user,
        mac_address=req.mac_address,
        name=req.name,
        description=req.description,
        device_type=req.device_type,
        assigned_vlan_id=req.assigned_vlan_id,
        enabled=req.enabled,
        expiry_date=req.expiry_date,
        client_ip=_client_ip(request),
    )


# ---------------------------------------------------------------------------
# Static sub-paths — MUST come before /{device_id} so FastAPI's
# declaration-order match doesn't try to parse "export-csv" as a UUID.
# ---------------------------------------------------------------------------

@router.get("/check/{mac_address}")
async def check_mab_device(
    mac_address: str,
    db: AsyncSession = Depends(get_db),
):
    """Quick MAC lookup for FreeRADIUS authorize hook. Unauthenticated by design."""
    return await service.check_mac_for_radius(db, raw_mac=mac_address)


@router.post("/bulk-import", status_code=201)
async def bulk_import_mab_devices(
    devices: list[MabDeviceBulkItem],
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Bulk import MAB devices. Skips duplicates (admin only)."""
    return await service.bulk_import(
        db, user,
        devices=devices,
        client_ip=_client_ip(request),
    )


@router.post("/import-csv", status_code=201)
async def import_csv_mab_devices(
    request: Request,
    csv_text: str = Body(
        ...,
        media_type="text/csv",
        description="CSV with header row. Required column: mac_address. "
                    "Optional: name, description, device_type, "
                    "assigned_vlan_id, expiry_date.",
    ),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Bulk import MAB devices from a CSV blob (admin only).

    Returns {created, skipped, total, parse_errors} so the operator
    can surface per-row failures while still accepting the valid rows.
    Header-based: column order is irrelevant; unknown columns are
    silently dropped.
    """
    return await service.import_csv(
        db, user,
        csv_text=csv_text,
        client_ip=_client_ip(request),
    )


@router.get("/export-csv", response_class=PlainTextResponse)
async def export_csv_mab_devices(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Download every MAB device in the tenant as CSV.

    Column set matches the import format so the export is round-trip
    safe (export → edit → re-import). Returns text/csv with the
    standard header row.
    """
    csv_text = await service.export_csv(db, tenant_id=user["tenant_id"])
    return PlainTextResponse(
        content=csv_text,
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="mab-devices.csv"',
        },
    )


# ---------------------------------------------------------------------------
# Per-device CRUD — these must come LAST because /{device_id} is a
# catch-all that would otherwise swallow the static paths above.
# ---------------------------------------------------------------------------

@router.get("/{device_id}")
async def get_mab_device(
    device_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Get a specific MAB device."""
    return await service.get_mab_device(
        db, tenant_id=user["tenant_id"], device_id=device_id,
    )


@router.put("/{device_id}")
async def update_mab_device(
    device_id: UUID,
    req: MabDeviceUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Update a MAB device (admin only)."""
    return await service.update_mab_device(
        db, user,
        device_id=device_id,
        updates=req.model_dump(exclude_unset=True),
        client_ip=_client_ip(request),
    )


@router.delete("/{device_id}", status_code=204)
async def delete_mab_device(
    device_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Remove a device from the MAB whitelist (admin only)."""
    await service.delete_mab_device(
        db, user,
        device_id=device_id,
        client_ip=_client_ip(request),
    )
