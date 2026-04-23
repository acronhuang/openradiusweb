"""Group-to-VLAN mapping routes for Dynamic VLAN Assignment."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.database import get_db
from orw_common.models.group_vlan_mapping import (
    GroupVlanMappingCreate, GroupVlanMappingUpdate,
)
from middleware.auth import get_current_user, require_admin
from utils.audit import log_audit
from utils.safe_sql import build_safe_set_clause, GROUP_VLAN_MAPPING_UPDATE_COLUMNS

router = APIRouter(prefix="/group-vlan-mappings")

COLUMNS = (
    "id, group_name, vlan_id, priority, description, "
    "ldap_server_id, enabled, created_at, updated_at"
)


@router.get("")
async def list_mappings(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """List all group-to-VLAN mappings, ordered by priority."""
    result = await db.execute(
        text(
            f"SELECT {COLUMNS} FROM group_vlan_mappings "
            f"WHERE tenant_id = :tenant_id "
            f"ORDER BY priority ASC, group_name ASC"
        ),
        {"tenant_id": user["tenant_id"]},
    )
    rows = result.mappings().all()
    items = [dict(r) for r in rows]
    return {"items": items, "total": len(items)}


@router.get("/{mapping_id}")
async def get_mapping(
    mapping_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    result = await db.execute(
        text(
            f"SELECT {COLUMNS} FROM group_vlan_mappings "
            f"WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(mapping_id), "tenant_id": user["tenant_id"]},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Mapping not found")
    return dict(row)


@router.post("", status_code=201)
async def create_mapping(
    req: GroupVlanMappingCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Create a new group-to-VLAN mapping."""
    # Check for duplicate group_name
    dup = await db.execute(
        text(
            "SELECT id FROM group_vlan_mappings "
            "WHERE group_name = :group_name AND tenant_id = :tenant_id"
        ),
        {"group_name": req.group_name, "tenant_id": user["tenant_id"]},
    )
    if dup.first():
        raise HTTPException(
            status_code=409,
            detail=f"Mapping for group '{req.group_name}' already exists",
        )

    result = await db.execute(
        text(
            f"INSERT INTO group_vlan_mappings "
            f"(group_name, vlan_id, priority, description, "
            f"ldap_server_id, enabled, tenant_id) "
            f"VALUES (:group_name, :vlan_id, :priority, :description, "
            f":ldap_server_id, :enabled, :tenant_id) "
            f"RETURNING {COLUMNS}"
        ),
        {
            "group_name": req.group_name,
            "vlan_id": req.vlan_id,
            "priority": req.priority,
            "description": req.description,
            "ldap_server_id": req.ldap_server_id,
            "enabled": req.enabled,
            "tenant_id": user["tenant_id"],
        },
    )
    row = result.mappings().first()
    await db.commit()

    client_ip = request.client.host if request.client else None
    await log_audit(
        db=db, user=user, action="create", resource_type="group_vlan_mapping",
        resource_id=str(row["id"]),
        details={"group_name": req.group_name, "vlan_id": req.vlan_id},
        ip_address=client_ip,
    )
    await db.commit()

    return dict(row)


@router.put("/{mapping_id}")
async def update_mapping(
    mapping_id: UUID,
    req: GroupVlanMappingUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Update a group-to-VLAN mapping."""
    existing = await db.execute(
        text(
            "SELECT id, group_name FROM group_vlan_mappings "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(mapping_id), "tenant_id": user["tenant_id"]},
    )
    old = existing.mappings().first()
    if not old:
        raise HTTPException(status_code=404, detail="Mapping not found")

    updates = req.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    # Check group_name uniqueness if changing
    if "group_name" in updates and updates["group_name"] != old["group_name"]:
        dup = await db.execute(
            text(
                "SELECT id FROM group_vlan_mappings "
                "WHERE group_name = :group_name AND tenant_id = :tenant_id "
                "AND id != :id"
            ),
            {
                "group_name": updates["group_name"],
                "tenant_id": user["tenant_id"],
                "id": str(mapping_id),
            },
        )
        if dup.first():
            raise HTTPException(
                status_code=409,
                detail=f"Mapping for group '{updates['group_name']}' already exists",
            )

    try:
        set_clause, params = build_safe_set_clause(
            updates, GROUP_VLAN_MAPPING_UPDATE_COLUMNS,
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    params["id"] = str(mapping_id)
    params["tenant_id"] = user["tenant_id"]

    result = await db.execute(
        text(
            f"UPDATE group_vlan_mappings SET {set_clause}, updated_at = NOW() "
            f"WHERE id = :id AND tenant_id = :tenant_id "
            f"RETURNING {COLUMNS}"
        ),
        params,
    )
    updated = result.mappings().first()
    if not updated:
        raise HTTPException(status_code=404, detail="Mapping not found")
    await db.commit()

    client_ip = request.client.host if request.client else None
    await log_audit(
        db=db, user=user, action="update", resource_type="group_vlan_mapping",
        resource_id=str(mapping_id),
        details={"changed_fields": updates, "group_name": old["group_name"]},
        ip_address=client_ip,
    )
    await db.commit()

    return dict(updated)


@router.delete("/{mapping_id}", status_code=204)
async def delete_mapping(
    mapping_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Delete a group-to-VLAN mapping."""
    existing = await db.execute(
        text(
            "SELECT group_name, vlan_id FROM group_vlan_mappings "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(mapping_id), "tenant_id": user["tenant_id"]},
    )
    old = existing.mappings().first()
    if not old:
        raise HTTPException(status_code=404, detail="Mapping not found")

    await db.execute(
        text(
            "DELETE FROM group_vlan_mappings "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(mapping_id), "tenant_id": user["tenant_id"]},
    )
    await db.commit()

    client_ip = request.client.host if request.client else None
    await log_audit(
        db=db, user=user, action="delete", resource_type="group_vlan_mapping",
        resource_id=str(mapping_id),
        details={"group_name": old["group_name"], "vlan_id": old["vlan_id"]},
        ip_address=client_ip,
    )
    await db.commit()


@router.get("/lookup/by-groups")
async def lookup_vlan_by_groups(
    groups: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Look up VLAN for a list of group names (comma-separated).

    Returns the highest-priority (lowest number) matching mapping.
    Used internally by FreeRADIUS post_auth for dynamic VLAN assignment.
    """
    group_list = [g.strip() for g in groups.split(",") if g.strip()]
    if not group_list:
        return {"match": None}

    # Use ANY() for array matching
    result = await db.execute(
        text(
            "SELECT group_name, vlan_id, priority FROM group_vlan_mappings "
            "WHERE group_name = ANY(:groups) "
            "AND enabled = true AND tenant_id = :tenant_id "
            "ORDER BY priority ASC LIMIT 1"
        ),
        {"groups": group_list, "tenant_id": user["tenant_id"]},
    )
    row = result.mappings().first()
    if row:
        return {"match": dict(row)}
    return {"match": None}
