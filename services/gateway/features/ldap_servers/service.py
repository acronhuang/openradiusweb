"""Use-case composition for the ldap_servers feature (Layer 2).

Every mutation publishes `orw.config.freeradius.apply` so the
FreeRADIUS config watcher regenerates clients/realms config.
Reference-check on delete enforces the radius_realms.ldap_server_id FK.
"""
from typing import Any, Mapping, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from utils.audit import log_audit

from . import events
from . import repository as repo


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

async def list_ldap_servers(
    db: AsyncSession,
    *,
    tenant_id: str,
    enabled: Optional[bool],
    page: int,
    page_size: int,
) -> dict:
    total = await repo.count_ldap_servers(
        db, tenant_id=tenant_id, enabled=enabled,
    )
    rows = await repo.list_ldap_servers(
        db, tenant_id=tenant_id, enabled=enabled,
        limit=page_size, offset=(page - 1) * page_size,
    )
    return {
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def get_ldap_server(
    db: AsyncSession, *, tenant_id: str, server_id: UUID,
) -> dict:
    row = await repo.lookup_ldap_server(
        db, tenant_id=tenant_id, server_id=server_id,
    )
    if not row:
        raise NotFoundError("LDAP server", str(server_id))
    return dict(row)


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

async def create_ldap_server(
    db: AsyncSession,
    actor: dict,
    *,
    fields: dict,
    client_ip: Optional[str],
) -> dict:
    row = await repo.insert_ldap_server(
        db, tenant_id=actor["tenant_id"], fields=fields,
    )
    await log_audit(
        db, actor,
        action="create", resource_type="ldap_server",
        resource_id=str(row["id"]),
        details={
            "name": fields["name"],
            "host": fields["host"],
            "port": fields["port"],
        },
        ip_address=client_ip,
    )
    await events.publish_freeradius_apply(
        reason="ldap_server_created", ldap_server_id=row["id"],
    )
    return dict(row)


async def update_ldap_server(
    db: AsyncSession,
    actor: dict,
    *,
    server_id: UUID,
    updates: dict,
    client_ip: Optional[str],
) -> dict:
    if not updates:
        raise ValidationError("No fields to update")

    try:
        row = await repo.update_ldap_server(
            db, tenant_id=actor["tenant_id"],
            server_id=server_id, updates=updates,
        )
    except ValueError:
        raise ValidationError("No valid fields to update")

    if not row:
        raise NotFoundError("LDAP server", str(server_id))

    await log_audit(
        db, actor,
        action="update", resource_type="ldap_server",
        resource_id=str(server_id),
        details={"changed_fields": list(updates.keys())},
        ip_address=client_ip,
    )
    await events.publish_freeradius_apply(
        reason="ldap_server_updated", ldap_server_id=server_id,
    )
    return dict(row)


async def delete_ldap_server(
    db: AsyncSession,
    actor: dict,
    *,
    server_id: UUID,
    client_ip: Optional[str],
) -> None:
    # Reference check first — radius_realms.ldap_server_id FK
    ref_count = await repo.count_realm_references(db, server_id=server_id)
    if ref_count > 0:
        raise ConflictError(
            f"Cannot delete: LDAP server is referenced by {ref_count} "
            f"RADIUS realm(s). Remove the realm references first."
        )

    existing = await repo.lookup_ldap_server_summary(
        db, tenant_id=actor["tenant_id"], server_id=server_id,
    )
    if not existing:
        raise NotFoundError("LDAP server", str(server_id))

    await repo.delete_ldap_server(
        db, tenant_id=actor["tenant_id"], server_id=server_id,
    )
    await log_audit(
        db, actor,
        action="delete", resource_type="ldap_server",
        resource_id=str(server_id),
        details={"name": existing["name"]},
        ip_address=client_ip,
    )
    await events.publish_freeradius_apply(
        reason="ldap_server_deleted", ldap_server_id=server_id,
    )


async def lookup_for_test(
    db: AsyncSession, *, tenant_id: str, server_id: UUID,
) -> Mapping[str, Any]:
    """Loaded by the route-layer LDAP test — includes the password."""
    row = await repo.lookup_full_for_test(
        db, tenant_id=tenant_id, server_id=server_id,
    )
    if not row:
        raise NotFoundError("LDAP server", str(server_id))
    return row


async def record_test_result(
    db: AsyncSession,
    actor: dict,
    *,
    server_id: UUID,
    success: bool,
    message: str,
    audit_details: dict,
) -> None:
    """Persist test outcome + audit it."""
    await repo.update_test_result(
        db, server_id=server_id, success=success, message=message,
    )
    await log_audit(
        db, actor,
        action="test", resource_type="ldap_server",
        resource_id=str(server_id),
        details=audit_details,
    )
