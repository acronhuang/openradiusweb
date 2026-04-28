"""RADIUS realm management routes - local, proxy, and reject realms."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.database import get_db
from orw_common.models.radius_realm import RealmCreate, RealmUpdate
from orw_common import nats_client
from middleware.auth import require_operator, require_admin
from utils.audit import log_audit
from utils.safe_sql import build_safe_set_clause, REALM_UPDATE_COLUMNS

router = APIRouter(prefix="/radius/realms")


# Columns safe to return (excludes proxy_secret_encrypted)
_SAFE_COLUMNS = (
    "r.id, r.name, r.description, r.realm_type, r.strip_username, "
    "r.proxy_host, r.proxy_port, r.proxy_nostrip, "
    "r.proxy_retry_count, r.proxy_retry_delay_seconds, r.proxy_dead_time_seconds, "
    "r.ldap_server_id, r.auth_types_allowed, "
    "r.default_vlan, r.default_filter_id, r.fallback_realm_id, "
    "r.priority, r.enabled, r.tenant_id"
)

_SAFE_COLUMNS_PLAIN = (
    "id, name, description, realm_type, strip_username, "
    "proxy_host, proxy_port, proxy_nostrip, "
    "proxy_retry_count, proxy_retry_delay_seconds, proxy_dead_time_seconds, "
    "ldap_server_id, auth_types_allowed, "
    "default_vlan, default_filter_id, fallback_realm_id, "
    "priority, enabled, tenant_id"
)


# ============================================================
# Endpoints
# ============================================================

@router.get("")
async def list_realms(
    realm_type: str | None = None,
    enabled: bool | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_operator),
):
    """List RADIUS realms. Joins ldap_servers for ldap_server_name. Never returns proxy_secret."""
    conditions = ["r.tenant_id = :tenant_id"]
    params: dict = {"tenant_id": user["tenant_id"]}

    if realm_type:
        conditions.append("r.realm_type = :realm_type")
        params["realm_type"] = realm_type
    if enabled is not None:
        conditions.append("r.enabled = :enabled")
        params["enabled"] = enabled

    where = " AND ".join(conditions)
    offset = (page - 1) * page_size
    params["limit"] = page_size
    params["offset"] = offset

    count_result = await db.execute(
        text(f"SELECT COUNT(*) FROM radius_realms r WHERE {where}"), params
    )
    total = count_result.scalar()

    result = await db.execute(
        text(
            f"SELECT {_SAFE_COLUMNS}, "
            f"ls.name as ldap_server_name "
            f"FROM radius_realms r "
            f"LEFT JOIN ldap_servers ls ON r.ldap_server_id = ls.id "
            f"WHERE {where} "
            f"ORDER BY r.priority ASC, r.name ASC "
            f"LIMIT :limit OFFSET :offset"
        ),
        params,
    )
    rows = result.mappings().all()

    return {
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("", status_code=201)
async def create_realm(
    req: RealmCreate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Create a new RADIUS realm."""
    # Validate proxy requirements
    if req.realm_type == "proxy":
        if not req.proxy_host:
            raise HTTPException(
                status_code=400,
                detail="proxy_host is required for proxy realm type",
            )
        if not req.proxy_secret:
            raise HTTPException(
                status_code=400,
                detail="proxy_secret is required for proxy realm type",
            )

    # Validate ldap_server_id exists if provided
    if req.ldap_server_id:
        ldap_check = await db.execute(
            text(
                "SELECT 1 FROM ldap_servers "
                "WHERE id = :id AND tenant_id = :tenant_id"
            ),
            {"id": req.ldap_server_id, "tenant_id": user["tenant_id"]},
        )
        if not ldap_check.first():
            raise HTTPException(
                status_code=400,
                detail="Referenced LDAP server not found",
            )

    # Validate fallback_realm_id exists if provided
    if req.fallback_realm_id:
        fb_check = await db.execute(
            text(
                "SELECT 1 FROM radius_realms "
                "WHERE id = :id AND tenant_id = :tenant_id"
            ),
            {"id": req.fallback_realm_id, "tenant_id": user["tenant_id"]},
        )
        if not fb_check.first():
            raise HTTPException(
                status_code=400,
                detail="Referenced fallback realm not found",
            )

    result = await db.execute(
        text(
            "INSERT INTO radius_realms "
            "(name, description, realm_type, strip_username, "
            "proxy_host, proxy_port, proxy_secret_encrypted, proxy_nostrip, "
            "proxy_retry_count, proxy_retry_delay_seconds, proxy_dead_time_seconds, "
            "ldap_server_id, auth_types_allowed, "
            "default_vlan, default_filter_id, fallback_realm_id, "
            "priority, enabled, tenant_id) "
            "VALUES (:name, :description, :realm_type, :strip_username, "
            ":proxy_host, :proxy_port, :proxy_secret_encrypted, :proxy_nostrip, "
            ":proxy_retry_count, :proxy_retry_delay_seconds, :proxy_dead_time_seconds, "
            ":ldap_server_id, :auth_types_allowed, "
            ":default_vlan, :default_filter_id, :fallback_realm_id, "
            ":priority, :enabled, :tenant_id) "
            f"RETURNING {_SAFE_COLUMNS_PLAIN}"
        ),
        {
            "name": req.name,
            "description": req.description,
            "realm_type": req.realm_type,
            "strip_username": req.strip_username,
            "proxy_host": req.proxy_host,
            "proxy_port": req.proxy_port,
            "proxy_secret_encrypted": req.proxy_secret,  # TODO: encrypt via Vault
            "proxy_nostrip": req.proxy_nostrip,
            "proxy_retry_count": req.proxy_retry_count,
            "proxy_retry_delay_seconds": req.proxy_retry_delay_seconds,
            "proxy_dead_time_seconds": req.proxy_dead_time_seconds,
            "ldap_server_id": req.ldap_server_id,
            "auth_types_allowed": req.auth_types_allowed,
            "default_vlan": req.default_vlan,
            "default_filter_id": req.default_filter_id,
            "fallback_realm_id": req.fallback_realm_id,
            "priority": req.priority,
            "enabled": req.enabled,
            "tenant_id": user["tenant_id"],
        },
    )
    row = result.mappings().first()

    await log_audit(
        db, user, "create", "radius_realm",
        resource_id=str(row["id"]),
        details={
            "name": req.name,
            "realm_type": req.realm_type,
            "proxy_host": req.proxy_host,
        },
    )

    await nats_client.publish("orw.config.freeradius.apply", {
        "reason": "realm_created",
        "realm_id": str(row["id"]),
        "realm_name": req.name,
    })

    return dict(row)


@router.get("/{realm_id}")
async def get_realm(
    realm_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_operator),
):
    """Get a specific RADIUS realm with ldap_server_name."""
    result = await db.execute(
        text(
            f"SELECT {_SAFE_COLUMNS}, "
            f"ls.name as ldap_server_name "
            f"FROM radius_realms r "
            f"LEFT JOIN ldap_servers ls ON r.ldap_server_id = ls.id "
            f"WHERE r.id = :id AND r.tenant_id = :tenant_id"
        ),
        {"id": str(realm_id), "tenant_id": user["tenant_id"]},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="RADIUS realm not found")
    return dict(row)


@router.put("/{realm_id}")
async def update_realm(
    realm_id: UUID,
    req: RealmUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Update a RADIUS realm. proxy_secret is optional (only updated if provided)."""
    raw = req.model_dump(exclude_unset=True)
    if not raw:
        raise HTTPException(status_code=400, detail="No fields to update")

    # If changing to proxy type, validate requirements
    if raw.get("realm_type") == "proxy":
        # Need to check existing record for proxy_host and secret if not provided in update
        existing = await db.execute(
            text(
                "SELECT proxy_host, proxy_secret_encrypted FROM radius_realms "
                "WHERE id = :id AND tenant_id = :tenant_id"
            ),
            {"id": str(realm_id), "tenant_id": user["tenant_id"]},
        )
        existing_row = existing.mappings().first()
        if not existing_row:
            raise HTTPException(status_code=404, detail="RADIUS realm not found")

        effective_host = raw.get("proxy_host") or existing_row["proxy_host"]
        effective_secret = raw.get("proxy_secret") or existing_row["proxy_secret_encrypted"]

        if not effective_host:
            raise HTTPException(
                status_code=400,
                detail="proxy_host is required for proxy realm type",
            )
        if not effective_secret:
            raise HTTPException(
                status_code=400,
                detail="proxy_secret is required for proxy realm type",
            )

    # Validate ldap_server_id if provided
    if "ldap_server_id" in raw and raw["ldap_server_id"]:
        ldap_check = await db.execute(
            text(
                "SELECT 1 FROM ldap_servers "
                "WHERE id = :ldap_id AND tenant_id = :tenant_id"
            ),
            {"ldap_id": raw["ldap_server_id"], "tenant_id": user["tenant_id"]},
        )
        if not ldap_check.first():
            raise HTTPException(
                status_code=400,
                detail="Referenced LDAP server not found",
            )

    # Build safe SET clause
    try:
        set_clause, params = build_safe_set_clause(
            raw, REALM_UPDATE_COLUMNS,
            column_map={"proxy_secret": "proxy_secret_encrypted"},
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    params["id"] = str(realm_id)
    params["tenant_id"] = user["tenant_id"]

    result = await db.execute(
        text(
            f"UPDATE radius_realms SET {set_clause}, updated_at = NOW() "
            f"WHERE id = :id AND tenant_id = :tenant_id "
            f"RETURNING {_SAFE_COLUMNS_PLAIN}"
        ),
        params,
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="RADIUS realm not found")

    await log_audit(
        db, user, "update", "radius_realm",
        resource_id=str(realm_id),
        details={"changed_fields": list(raw.keys())},
    )

    await nats_client.publish("orw.config.freeradius.apply", {
        "reason": "realm_updated",
        "realm_id": str(realm_id),
    })

    return dict(row)


@router.delete("/{realm_id}", status_code=204)
async def delete_realm(
    realm_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Delete a RADIUS realm."""
    # Check for fallback references from other realms
    ref_check = await db.execute(
        text(
            "SELECT COUNT(*) FROM radius_realms "
            "WHERE fallback_realm_id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(realm_id), "tenant_id": user["tenant_id"]},
    )
    ref_count = ref_check.scalar()
    if ref_count > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete: realm is referenced as fallback by {ref_count} other realm(s).",
        )

    # Get name for audit before deleting
    name_result = await db.execute(
        text(
            "SELECT name, realm_type FROM radius_realms "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(realm_id), "tenant_id": user["tenant_id"]},
    )
    name_row = name_result.mappings().first()
    if not name_row:
        raise HTTPException(status_code=404, detail="RADIUS realm not found")

    await db.execute(
        text(
            "DELETE FROM radius_realms "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(realm_id), "tenant_id": user["tenant_id"]},
    )

    await log_audit(
        db, user, "delete", "radius_realm",
        resource_id=str(realm_id),
        details={"name": name_row["name"], "realm_type": name_row["realm_type"]},
    )

    await nats_client.publish("orw.config.freeradius.apply", {
        "reason": "realm_deleted",
        "realm_id": str(realm_id),
    })
