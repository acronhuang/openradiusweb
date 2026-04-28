"""Use-case composition for the coa feature (Layer 2).

This feature is "fire and forget" from the gateway's perspective —
each send-call publishes one NATS message to `coa_service` and audits
the request. Actual CoA UDP packet delivery + ACK tracking lives in
coa_service.

`_send_coa_to_target` factors out the publish + audit pattern shared
by the three single-target endpoints (mac/username/session). Bulk has
its own loop because it batches one audit row for the whole batch.
"""
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.exceptions import ValidationError
from utils.audit import log_audit

from . import events
from . import repository as repo


BULK_MAX_TARGETS = 100


# ---------------------------------------------------------------------------
# Send (single-target, internal helper + 3 public functions)
# ---------------------------------------------------------------------------

async def _send_coa_to_target(
    db: AsyncSession,
    actor: dict,
    *,
    target_field: str,
    target_value: str,
    action: str,
    vlan_id: Optional[int],
    acl_name: Optional[str],
    reason: Optional[str],
) -> None:
    """Publish + audit. Caller is responsible for shaping the response."""
    payload: dict[str, Any] = {
        target_field: target_value,
        "action": action,
        "vlan_id": vlan_id,
        "acl_name": acl_name,
        "requested_by": actor.get("sub"),
        "reason": reason,
    }
    await events.publish_coa(payload)
    await log_audit(
        db, actor,
        action=f"coa_{action}", resource_type="coa",
        details={target_field: target_value, "reason": reason},
    )


async def send_coa_by_mac(
    db: AsyncSession, actor: dict, *, mac_address: str, action: str,
    vlan_id: Optional[int], acl_name: Optional[str], reason: Optional[str],
) -> dict:
    await _send_coa_to_target(
        db, actor,
        target_field="mac_address", target_value=mac_address,
        action=action, vlan_id=vlan_id, acl_name=acl_name, reason=reason,
    )
    return {
        "status": "submitted",
        "message": f"CoA {action} request sent for MAC {mac_address}",
        "mac_address": mac_address,
        "action": action,
    }


async def send_coa_by_username(
    db: AsyncSession, actor: dict, *, username: str, action: str,
    vlan_id: Optional[int], acl_name: Optional[str], reason: Optional[str],
) -> dict:
    await _send_coa_to_target(
        db, actor,
        target_field="username", target_value=username,
        action=action, vlan_id=vlan_id, acl_name=acl_name, reason=reason,
    )
    return {
        "status": "submitted",
        "message": f"CoA {action} request sent for user {username}",
        "username": username,
        "action": action,
    }


async def send_coa_by_session(
    db: AsyncSession, actor: dict, *, session_id: str, action: str,
    vlan_id: Optional[int], acl_name: Optional[str], reason: Optional[str],
) -> dict:
    await _send_coa_to_target(
        db, actor,
        target_field="session_id", target_value=session_id,
        action=action, vlan_id=vlan_id, acl_name=acl_name, reason=reason,
    )
    return {
        "status": "submitted",
        "message": f"CoA {action} request sent for session {session_id}",
        "session_id": session_id,
        "action": action,
    }


# ---------------------------------------------------------------------------
# Send (bulk)
# ---------------------------------------------------------------------------

# Map UI-facing target_type → NATS payload field name.
_TARGET_FIELD_BY_TYPE = {
    "mac": "mac_address",
    "session_id": "session_id",
    "username": "username",
}


async def send_coa_bulk(
    db: AsyncSession,
    actor: dict,
    *,
    targets: list[str],
    target_type: str,
    action: str,
    vlan_id: Optional[int],
    reason: Optional[str],
) -> dict:
    if len(targets) > BULK_MAX_TARGETS:
        raise ValidationError(
            f"Maximum {BULK_MAX_TARGETS} targets per bulk request"
        )

    field = _TARGET_FIELD_BY_TYPE.get(target_type, "username")
    submitted = 0
    for target in targets:
        payload: dict[str, Any] = {
            field: target,
            "action": action,
            "vlan_id": vlan_id,
            "requested_by": actor.get("sub"),
            "reason": reason,
        }
        await events.publish_coa(payload)
        submitted += 1

    await log_audit(
        db, actor,
        action=f"coa_{action}", resource_type="coa",
        details={
            "bulk": True,
            "target_type": target_type,
            "target_count": submitted,
            "reason": reason,
        },
    )
    return {
        "status": "submitted",
        "message": f"Bulk CoA {action} sent to {submitted} targets",
        "submitted_count": submitted,
        "action": action,
    }


# ---------------------------------------------------------------------------
# History + active sessions
# ---------------------------------------------------------------------------

async def get_history(
    db: AsyncSession,
    *,
    tenant_id: Optional[str],
    action: Optional[str],
    page: int,
    page_size: int,
) -> dict:
    total = await repo.count_coa_history(
        db, tenant_id=tenant_id, action=action,
    )
    rows = await repo.list_coa_history(
        db, tenant_id=tenant_id, action=action,
        limit=page_size, offset=(page - 1) * page_size,
    )
    return {
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def list_active_sessions(
    db: AsyncSession,
    *,
    nas_ip: Optional[str],
    vlan: Optional[int],
    page: int,
    page_size: int,
) -> dict:
    total = await repo.count_active_sessions(db, nas_ip=nas_ip, vlan=vlan)
    rows = await repo.list_active_sessions(
        db, nas_ip=nas_ip, vlan=vlan,
        limit=page_size, offset=(page - 1) * page_size,
    )
    return {
        "items": [
            {
                **dict(r),
                "coa_available": True,
                "actions_available": [
                    "disconnect", "reauthenticate", "vlan_change", "apply_acl",
                ],
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
