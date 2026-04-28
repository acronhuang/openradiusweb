"""RADIUS NAS client management routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.database import get_db
from orw_common.models.nas_client import NASClientCreate, NASClientUpdate
from orw_common import nats_client
from middleware.auth import require_operator, require_admin
from utils.audit import log_audit
from utils.safe_sql import build_safe_set_clause, NAS_CLIENT_UPDATE_COLUMNS

router = APIRouter(prefix="/nas-clients")


# ============================================================
# List / Read
# ============================================================

@router.get("")
async def list_nas_clients(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_operator),
):
    """
    List all NAS clients.
    Never returns the shared_secret_encrypted field.
    """
    result = await db.execute(
        text(
            "SELECT id, name, ip_address, shortname, nas_type, "
            "virtual_server, enabled, description, tenant_id "
            "FROM radius_nas_clients "
            "WHERE tenant_id = :tenant_id "
            "ORDER BY name"
        ),
        {"tenant_id": user["tenant_id"]},
    )
    rows = result.mappings().all()
    return {"items": [dict(r) for r in rows]}


@router.get("/{nas_id}")
async def get_nas_client(
    nas_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_operator),
):
    """Get a specific NAS client (without shared secret)."""
    result = await db.execute(
        text(
            "SELECT id, name, ip_address, shortname, nas_type, "
            "virtual_server, enabled, description, tenant_id "
            "FROM radius_nas_clients "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(nas_id), "tenant_id": user["tenant_id"]},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="NAS client not found")
    return dict(row)


# ============================================================
# Create / Update / Delete
# ============================================================

@router.post("", status_code=201)
async def create_nas_client(
    req: NASClientCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Create a new NAS client."""
    result = await db.execute(
        text(
            "INSERT INTO radius_nas_clients "
            "(name, ip_address, secret_encrypted, shortname, "
            "nas_type, description, tenant_id) "
            "VALUES (:name, :ip_address, :shared_secret, "
            ":shortname, :nas_type, :description, :tenant_id) "
            "RETURNING id, name, ip_address, shortname, nas_type, "
            "virtual_server, enabled, description, tenant_id"
        ),
        {
            "name": req.name,
            "ip_address": req.ip_address,
            "shared_secret": req.shared_secret,
            "shortname": req.shortname or req.name[:31],
            "nas_type": req.nas_type,
            "description": req.description,
            "tenant_id": user["tenant_id"],
        },
    )
    nas = result.mappings().first()
    await db.commit()

    client_ip = request.client.host if request.client else None
    await log_audit(
        db=db,
        user=user,
        action="create",
        resource_type="nas_client",
        resource_id=str(nas["id"]),
        details={"name": req.name, "ip_address": req.ip_address},
        ip_address=client_ip,
    )
    await db.commit()

    return dict(nas)


@router.put("/{nas_id}")
async def update_nas_client(
    nas_id: UUID,
    req: NASClientUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Update a NAS client. shared_secret is optional (only updated if provided)."""
    # Fetch current for audit trail
    existing = await db.execute(
        text(
            "SELECT id, name, ip_address, shortname, nas_type, "
            "enabled, description "
            "FROM radius_nas_clients "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(nas_id), "tenant_id": user["tenant_id"]},
    )
    old = existing.mappings().first()
    if not old:
        raise HTTPException(status_code=404, detail="NAS client not found")

    updates = req.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    # Build safe SET clause
    try:
        set_clause, params = build_safe_set_clause(
            updates, NAS_CLIENT_UPDATE_COLUMNS,
            column_map={"shared_secret": "secret_encrypted"},
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    # Handle inet cast for ip_address
    if "ip_address" in params:
        set_clause = set_clause.replace(
            "ip_address = :ip_address", "ip_address = :ip_address::inet"
        )

    params["id"] = str(nas_id)
    params["tenant_id"] = user["tenant_id"]

    result = await db.execute(
        text(
            f"UPDATE radius_nas_clients SET {set_clause} "
            f"WHERE id = :id AND tenant_id = :tenant_id "
            f"RETURNING id, name, ip_address, shortname, nas_type, "
            f"virtual_server, enabled, description, tenant_id"
        ),
        params,
    )
    updated = result.mappings().first()
    if not updated:
        raise HTTPException(status_code=404, detail="NAS client not found")

    await db.commit()

    # Build audit details (mask shared_secret)
    changed_fields = {
        k: v for k, v in updates.items() if k != "shared_secret"
    }
    if "shared_secret" in updates:
        changed_fields["shared_secret"] = "********"

    client_ip = request.client.host if request.client else None
    await log_audit(
        db=db,
        user=user,
        action="update",
        resource_type="nas_client",
        resource_id=str(nas_id),
        details={"changed_fields": changed_fields, "name": old["name"]},
        ip_address=client_ip,
    )
    await db.commit()

    return dict(updated)


@router.delete("/{nas_id}", status_code=204)
async def delete_nas_client(
    nas_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Delete a NAS client."""
    # Fetch name for audit
    existing = await db.execute(
        text(
            "SELECT name FROM radius_nas_clients "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(nas_id), "tenant_id": user["tenant_id"]},
    )
    old = existing.mappings().first()
    if not old:
        raise HTTPException(status_code=404, detail="NAS client not found")

    await db.execute(
        text(
            "DELETE FROM radius_nas_clients "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(nas_id), "tenant_id": user["tenant_id"]},
    )
    await db.commit()

    client_ip = request.client.host if request.client else None
    await log_audit(
        db=db,
        user=user,
        action="delete",
        resource_type="nas_client",
        resource_id=str(nas_id),
        details={"name": old["name"]},
        ip_address=client_ip,
    )
    await db.commit()


# ============================================================
# Sync FreeRADIUS
# ============================================================

@router.post("/sync-radius")
async def sync_radius(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """
    Publish NATS message to trigger FreeRADIUS config reload.
    This regenerates clients.conf from the database and reloads FreeRADIUS.
    """
    await nats_client.publish("orw.config.freeradius.apply", {
        "triggered_by": user.get("username", user.get("sub")),
        "action": "reload_nas_clients",
    })

    client_ip = request.client.host if request.client else None
    await log_audit(
        db=db,
        user=user,
        action="sync",
        resource_type="nas_client",
        resource_id=None,
        details={"action": "freeradius_reload_triggered"},
        ip_address=client_ip,
    )
    await db.commit()

    return {"status": "sync_requested", "message": "FreeRADIUS reload has been triggered"}
