"""VLAN management routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.database import get_db
from orw_common.models.vlan import VlanCreate, VlanUpdate
from middleware.auth import get_current_user, require_admin
from utils.audit import log_audit
from utils.safe_sql import build_safe_set_clause, VLAN_UPDATE_COLUMNS

router = APIRouter(prefix="/vlans")


# ============================================================
# List / Read
# ============================================================

@router.get("")
async def list_vlans(
    purpose: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """List all VLANs. Any authenticated user can read (needed for dropdowns)."""
    conditions = ["tenant_id = :tenant_id"]
    params: dict = {"tenant_id": user["tenant_id"]}

    if purpose:
        conditions.append("purpose = :purpose")
        params["purpose"] = purpose

    where = " AND ".join(conditions)
    result = await db.execute(
        text(
            f"SELECT id, vlan_id, name, description, purpose, "
            f"subnet, enabled, created_at, updated_at "
            f"FROM vlans WHERE {where} "
            f"ORDER BY vlan_id"
        ),
        params,
    )
    rows = result.mappings().all()
    items = []
    for r in rows:
        item = dict(r)
        if item.get("subnet"):
            item["subnet"] = str(item["subnet"])
        items.append(item)
    return {"items": items}


@router.get("/{vlan_uuid}")
async def get_vlan(
    vlan_uuid: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Get a specific VLAN."""
    result = await db.execute(
        text(
            "SELECT id, vlan_id, name, description, purpose, "
            "subnet, enabled, created_at, updated_at "
            "FROM vlans WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(vlan_uuid), "tenant_id": user["tenant_id"]},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="VLAN not found")
    item = dict(row)
    if item.get("subnet"):
        item["subnet"] = str(item["subnet"])
    return item


# ============================================================
# Create / Update / Delete
# ============================================================

@router.post("", status_code=201)
async def create_vlan(
    req: VlanCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Create a new VLAN."""
    result = await db.execute(
        text(
            "INSERT INTO vlans "
            "(vlan_id, name, description, purpose, subnet, enabled, tenant_id) "
            "VALUES (:vlan_id, :name, :description, :purpose, "
            ":subnet::cidr, :enabled, :tenant_id) "
            "RETURNING id, vlan_id, name, description, purpose, "
            "subnet, enabled, created_at, updated_at"
        ),
        {
            "vlan_id": req.vlan_id,
            "name": req.name,
            "description": req.description,
            "purpose": req.purpose,
            "subnet": req.subnet,
            "enabled": req.enabled,
            "tenant_id": user["tenant_id"],
        },
    )
    vlan = result.mappings().first()
    await db.commit()

    client_ip = request.client.host if request.client else None
    await log_audit(
        db=db, user=user, action="create", resource_type="vlan",
        resource_id=str(vlan["id"]),
        details={"vlan_id": req.vlan_id, "name": req.name, "purpose": req.purpose},
        ip_address=client_ip,
    )
    await db.commit()

    item = dict(vlan)
    if item.get("subnet"):
        item["subnet"] = str(item["subnet"])
    return item


@router.put("/{vlan_uuid}")
async def update_vlan(
    vlan_uuid: UUID,
    req: VlanUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Update a VLAN."""
    existing = await db.execute(
        text(
            "SELECT id, name FROM vlans "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(vlan_uuid), "tenant_id": user["tenant_id"]},
    )
    old = existing.mappings().first()
    if not old:
        raise HTTPException(status_code=404, detail="VLAN not found")

    updates = req.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    try:
        set_clause, params = build_safe_set_clause(updates, VLAN_UPDATE_COLUMNS)
    except ValueError:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    # Handle CIDR cast for subnet
    if "subnet" in params:
        set_clause = set_clause.replace(
            "subnet = :subnet", "subnet = :subnet::cidr"
        )

    params["id"] = str(vlan_uuid)
    params["tenant_id"] = user["tenant_id"]

    result = await db.execute(
        text(
            f"UPDATE vlans SET {set_clause}, updated_at = NOW() "
            f"WHERE id = :id AND tenant_id = :tenant_id "
            f"RETURNING id, vlan_id, name, description, purpose, "
            f"subnet, enabled, created_at, updated_at"
        ),
        params,
    )
    updated = result.mappings().first()
    if not updated:
        raise HTTPException(status_code=404, detail="VLAN not found")
    await db.commit()

    client_ip = request.client.host if request.client else None
    await log_audit(
        db=db, user=user, action="update", resource_type="vlan",
        resource_id=str(vlan_uuid),
        details={"changed_fields": updates, "name": old["name"]},
        ip_address=client_ip,
    )
    await db.commit()

    item = dict(updated)
    if item.get("subnet"):
        item["subnet"] = str(item["subnet"])
    return item


@router.delete("/{vlan_uuid}", status_code=204)
async def delete_vlan(
    vlan_uuid: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Delete a VLAN."""
    existing = await db.execute(
        text(
            "SELECT name, vlan_id FROM vlans "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(vlan_uuid), "tenant_id": user["tenant_id"]},
    )
    old = existing.mappings().first()
    if not old:
        raise HTTPException(status_code=404, detail="VLAN not found")

    await db.execute(
        text("DELETE FROM vlans WHERE id = :id AND tenant_id = :tenant_id"),
        {"id": str(vlan_uuid), "tenant_id": user["tenant_id"]},
    )
    await db.commit()

    client_ip = request.client.host if request.client else None
    await log_audit(
        db=db, user=user, action="delete", resource_type="vlan",
        resource_id=str(vlan_uuid),
        details={"name": old["name"], "vlan_id": old["vlan_id"]},
        ip_address=client_ip,
    )
    await db.commit()
