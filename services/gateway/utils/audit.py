"""Audit logging helper for tracking configuration changes."""

import json
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def log_audit(
    db: AsyncSession,
    user: dict,
    action: str,
    resource_type: str,
    resource_id: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
    ip_address: Optional[str] = None,
):
    """Write an audit log entry.

    Args:
        db: Database session
        user: JWT payload dict (must have 'sub' and optionally 'tenant_id')
        action: Action performed (create, update, delete, login, etc.)
        resource_type: Type of resource (user, certificate, policy, etc.)
        resource_id: UUID of the affected resource
        details: Dict with change details (old_value, new_value, changed_fields, etc.)
        ip_address: Client IP address
    """
    await db.execute(
        text(
            "INSERT INTO audit_log "
            "(user_id, action, resource_type, resource_id, details, ip_address, tenant_id) "
            "VALUES (CAST(:user_id AS uuid), :action, :resource_type, CAST(:resource_id AS uuid), "
            "CAST(:details AS jsonb), CAST(:ip_address AS inet), CAST(:tenant_id AS uuid))"
        ),
        {
            "user_id": user.get("sub"),
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "details": json.dumps(details or {}, default=str),
            "ip_address": ip_address,
            "tenant_id": user.get("tenant_id"),
        },
    )
