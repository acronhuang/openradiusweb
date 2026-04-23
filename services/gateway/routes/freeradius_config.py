"""FreeRADIUS configuration management routes."""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.database import get_db
from orw_common import nats_client
from middleware.auth import get_current_user, require_admin
from utils.audit import log_audit

router = APIRouter(prefix="/freeradius")


@router.get("/config")
async def get_config_status(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    """Get current FreeRADIUS configuration status."""
    tenant_id = current_user.get("tenant_id")
    result = await db.execute(
        text(
            "SELECT id, config_type, config_name, config_hash, "
            "last_applied_at, last_applied_hash, status, error_message, "
            "created_at, updated_at "
            "FROM freeradius_config "
            "WHERE tenant_id = :tenant_id "
            "ORDER BY config_type, config_name"
        ),
        {"tenant_id": tenant_id},
    )
    configs = [dict(row) for row in result.mappings().all()]

    # Check if any configs need applying (hash mismatch)
    needs_apply = any(
        c["config_hash"] != c.get("last_applied_hash")
        for c in configs
        if c["config_hash"]
    )

    return {
        "configs": configs,
        "needs_apply": needs_apply,
        "total": len(configs),
    }


@router.post("/config/preview")
async def preview_config(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    """Preview the FreeRADIUS configuration that would be generated.

    Returns the current stored configs from the database.
    The actual generation happens in the config manager service.
    """
    tenant_id = current_user.get("tenant_id")
    result = await db.execute(
        text(
            "SELECT config_type, config_name, config_content, config_hash, "
            "status, last_applied_at "
            "FROM freeradius_config "
            "WHERE tenant_id = :tenant_id "
            "ORDER BY config_type, config_name"
        ),
        {"tenant_id": tenant_id},
    )
    configs = [dict(row) for row in result.mappings().all()]

    # Also gather source data counts for context
    ldap_count = await db.execute(
        text("SELECT COUNT(*) FROM ldap_servers WHERE tenant_id = :tid AND enabled = true"),
        {"tid": tenant_id},
    )
    realm_count = await db.execute(
        text("SELECT COUNT(*) FROM radius_realms WHERE tenant_id = :tid AND enabled = true"),
        {"tid": tenant_id},
    )
    nas_count = await db.execute(
        text("SELECT COUNT(*) FROM radius_nas_clients WHERE tenant_id = :tid AND enabled = true"),
        {"tid": tenant_id},
    )
    cert_count = await db.execute(
        text("SELECT COUNT(*) FROM certificates WHERE tenant_id = :tid AND is_active = true"),
        {"tid": tenant_id},
    )

    return {
        "configs": configs,
        "source_data": {
            "ldap_servers": ldap_count.scalar(),
            "realms": realm_count.scalar(),
            "nas_clients": nas_count.scalar(),
            "active_certificates": cert_count.scalar(),
        },
    }


@router.post("/config/apply")
async def apply_config(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    """Trigger FreeRADIUS configuration regeneration and reload.

    Publishes a NATS message that the config watcher service will pick up
    to regenerate configs from the database and send HUP to FreeRADIUS.
    """
    from starlette.requests import Request

    tenant_id = current_user.get("tenant_id")

    await nats_client.publish(
        "orw.config.freeradius.apply",
        {
            "action": "apply",
            "tenant_id": tenant_id,
            "requested_by": current_user.get("sub"),
            "requested_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    await log_audit(
        db, current_user,
        action="freeradius_config_apply",
        resource_type="freeradius_config",
        details={"description": "Triggered FreeRADIUS configuration apply and reload"},
    )

    return {"status": "apply_triggered", "message": "Configuration apply request sent to FreeRADIUS"}


@router.get("/config/history")
async def get_config_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    """Get FreeRADIUS configuration change history from audit log."""
    tenant_id = current_user.get("tenant_id")
    offset = (page - 1) * page_size

    # Count
    count_result = await db.execute(
        text(
            "SELECT COUNT(*) FROM audit_log "
            "WHERE tenant_id = :tenant_id "
            "AND resource_type = 'freeradius_config'"
        ),
        {"tenant_id": tenant_id},
    )
    total = count_result.scalar()

    # Fetch
    result = await db.execute(
        text(
            "SELECT a.id, a.timestamp, a.user_id, a.action, "
            "a.resource_type, a.resource_id, a.details, a.ip_address, "
            "u.username "
            "FROM audit_log a "
            "LEFT JOIN users u ON a.user_id = u.id "
            "WHERE a.tenant_id = :tenant_id "
            "AND a.resource_type = 'freeradius_config' "
            "ORDER BY a.timestamp DESC "
            "LIMIT :limit OFFSET :offset"
        ),
        {"tenant_id": tenant_id, "limit": page_size, "offset": offset},
    )
    items = [dict(row) for row in result.mappings().all()]

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }
