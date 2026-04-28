"""CoA (Change of Authorization) API routes."""


from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.database import get_db
from orw_common.models.coa import (
    CoAByMacRequest, CoAByUsernameRequest,
    CoABySessionRequest, CoABulkRequest,
)
from orw_common import nats_client
from middleware.auth import get_current_user, require_operator
from utils.audit import log_audit

router = APIRouter(prefix="/coa")


# ============================================================
# CoA Endpoints
# ============================================================

@router.post("/by-mac")
async def coa_by_mac(
    req: CoAByMacRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_operator),
):
    """
    Send CoA by MAC address.

    Actions:
    - **disconnect**: Terminate the session, device must reconnect
    - **reauthenticate**: Force 802.1X re-authentication with current policies
    - **vlan_change**: Move device to a different VLAN (requires vlan_id)
    - **apply_acl**: Apply/change ACL on the session (requires acl_name)
    """
    await nats_client.publish("orw.policy.action.coa", {
        "mac_address": req.mac_address,
        "action": req.action,
        "vlan_id": req.vlan_id,
        "acl_name": req.acl_name,
        "requested_by": user.get("sub"),
        "reason": req.reason,
    })

    await log_audit(db, user, f"coa_{req.action}", "coa",
                    details={"mac_address": req.mac_address, "reason": req.reason})

    return {
        "status": "submitted",
        "message": f"CoA {req.action} request sent for MAC {req.mac_address}",
        "mac_address": req.mac_address,
        "action": req.action,
    }


@router.post("/by-username")
async def coa_by_username(
    req: CoAByUsernameRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_operator),
):
    """Send CoA for all active sessions of a username."""
    await nats_client.publish("orw.policy.action.coa", {
        "username": req.username,
        "action": req.action,
        "vlan_id": req.vlan_id,
        "acl_name": req.acl_name,
        "requested_by": user.get("sub"),
        "reason": req.reason,
    })

    await log_audit(db, user, f"coa_{req.action}", "coa",
                    details={"username": req.username, "reason": req.reason})

    return {
        "status": "submitted",
        "message": f"CoA {req.action} request sent for user {req.username}",
        "username": req.username,
        "action": req.action,
    }


@router.post("/by-session")
async def coa_by_session(
    req: CoABySessionRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_operator),
):
    """Send CoA for a specific RADIUS session ID."""
    await nats_client.publish("orw.policy.action.coa", {
        "session_id": req.session_id,
        "action": req.action,
        "vlan_id": req.vlan_id,
        "acl_name": req.acl_name,
        "requested_by": user.get("sub"),
        "reason": req.reason,
    })

    await log_audit(db, user, f"coa_{req.action}", "coa",
                    details={"session_id": req.session_id, "reason": req.reason})

    return {
        "status": "submitted",
        "message": f"CoA {req.action} request sent for session {req.session_id}",
        "session_id": req.session_id,
        "action": req.action,
    }


@router.post("/bulk")
async def coa_bulk(
    req: CoABulkRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_operator),
):
    """
    Send CoA to multiple devices at once.

    Useful for:
    - Mass quarantine during security incident
    - VLAN migration (move all devices to new VLAN)
    - Force re-authentication after policy update
    """
    if len(req.targets) > 100:
        raise HTTPException(status_code=400, detail="Maximum 100 targets per bulk request")

    submitted = 0
    for target in req.targets:
        data = {
            "action": req.action,
            "vlan_id": req.vlan_id,
            "requested_by": user.get("sub"),
            "reason": req.reason,
        }

        if req.target_type == "mac":
            data["mac_address"] = target
        elif req.target_type == "session_id":
            data["session_id"] = target
        else:
            data["username"] = target

        await nats_client.publish("orw.policy.action.coa", data)
        submitted += 1

    await log_audit(db, user, f"coa_{req.action}", "coa",
                    details={
                        "bulk": True,
                        "target_type": req.target_type,
                        "target_count": submitted,
                        "reason": req.reason,
                    })

    return {
        "status": "submitted",
        "message": f"Bulk CoA {req.action} sent to {submitted} targets",
        "submitted_count": submitted,
        "action": req.action,
    }


# ============================================================
# CoA History & Status
# ============================================================

@router.get("/history")
async def coa_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    action: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Get CoA event history from audit log."""
    conditions = ["a.resource_type = 'coa'"]
    params: dict = {"tenant_id": user.get("tenant_id")}

    # Scope to tenant if available
    conditions.append("(a.tenant_id = CAST(:tenant_id AS uuid) OR a.tenant_id IS NULL)")

    if action:
        conditions.append("a.action = :action")
        params["action"] = f"coa_{action}"

    where = " AND ".join(conditions)
    offset = (page - 1) * page_size
    params["limit"] = page_size
    params["offset"] = offset

    count_result = await db.execute(
        text(f"SELECT COUNT(*) FROM audit_log a WHERE {where}"), params
    )
    total = count_result.scalar()

    result = await db.execute(
        text(
            f"SELECT a.id, a.timestamp, a.user_id, u.username, "
            f"a.action, a.resource_type, a.details, a.ip_address "
            f"FROM audit_log a "
            f"LEFT JOIN users u ON a.user_id = u.id "
            f"WHERE {where} "
            f"ORDER BY a.timestamp DESC "
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


@router.get("/active-sessions")
async def list_active_sessions(
    nas_ip: str | None = None,
    vlan: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """
    List active RADIUS sessions that can receive CoA.

    This is useful for the UI to show which devices can be
    disconnected/reauthenticated/moved to another VLAN.
    """
    conditions = ["rs.status = 'active'"]
    params: dict = {}

    if nas_ip:
        conditions.append("rs.nas_ip = :nas_ip::inet")
        params["nas_ip"] = nas_ip

    if vlan:
        conditions.append("rs.assigned_vlan = :vlan")
        params["vlan"] = vlan

    where = " AND ".join(conditions)
    offset = (page - 1) * page_size
    params["limit"] = page_size
    params["offset"] = offset

    count_result = await db.execute(
        text(f"SELECT COUNT(*) FROM radius_sessions rs WHERE {where}"), params
    )
    total = count_result.scalar()

    result = await db.execute(
        text(f"""
            SELECT rs.*,
                   d.hostname AS device_hostname,
                   d.device_type,
                   d.os_family,
                   nd.hostname AS switch_hostname,
                   nd.vendor AS switch_vendor
            FROM radius_sessions rs
            LEFT JOIN devices d ON rs.device_id = d.id
            LEFT JOIN network_devices nd ON rs.nas_ip::text = nd.ip_address::text
            WHERE {where}
            ORDER BY rs.started_at DESC
            LIMIT :limit OFFSET :offset
        """),
        params,
    )
    rows = result.mappings().all()

    return {
        "items": [
            {
                **dict(r),
                "coa_available": True,
                "actions_available": ["disconnect", "reauthenticate", "vlan_change", "apply_acl"],
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
