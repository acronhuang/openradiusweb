"""Use-case composition for the radius_realms feature (Layer 2).

Validation flows:
- **proxy realm** requires `proxy_host` AND `proxy_secret` (on create or
  when becoming proxy via update — falls back to existing values).
- **`ldap_server_id`** must reference an existing LDAP server in the
  same tenant.
- **`fallback_realm_id`** must reference an existing realm in the same
  tenant.
- **delete** is refused when other realms reference this one as fallback.

All mutations publish `orw.config.freeradius.apply` so the config
watcher can regenerate the realm chain.
"""
from typing import Optional
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

async def list_realms(
    db: AsyncSession,
    *,
    tenant_id: str,
    realm_type: Optional[str],
    enabled: Optional[bool],
    page: int,
    page_size: int,
) -> dict:
    total = await repo.count_realms(
        db, tenant_id=tenant_id, realm_type=realm_type, enabled=enabled,
    )
    rows = await repo.list_realms(
        db, tenant_id=tenant_id, realm_type=realm_type, enabled=enabled,
        limit=page_size, offset=(page - 1) * page_size,
    )
    return {
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def get_realm(
    db: AsyncSession, *, tenant_id: str, realm_id: UUID,
) -> dict:
    row = await repo.lookup_realm(
        db, tenant_id=tenant_id, realm_id=realm_id,
    )
    if not row:
        raise NotFoundError("RADIUS realm", str(realm_id))
    return dict(row)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

async def _validate_ldap_server_ref(
    db: AsyncSession, *, tenant_id: str, ldap_server_id: Optional[str],
) -> None:
    if ldap_server_id and not await repo.ldap_server_exists(
        db, tenant_id=tenant_id, ldap_server_id=ldap_server_id,
    ):
        raise ValidationError("Referenced LDAP server not found")


async def _validate_fallback_realm_ref(
    db: AsyncSession, *, tenant_id: str, fallback_realm_id: Optional[str],
) -> None:
    if fallback_realm_id and not await repo.realm_exists(
        db, tenant_id=tenant_id, realm_id=fallback_realm_id,
    ):
        raise ValidationError("Referenced fallback realm not found")


def _validate_proxy_complete(host: Optional[str], secret: Optional[str]) -> None:
    if not host:
        raise ValidationError("proxy_host is required for proxy realm type")
    if not secret:
        raise ValidationError("proxy_secret is required for proxy realm type")


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

async def create_realm(
    db: AsyncSession,
    actor: dict,
    *,
    fields: dict,
    client_ip: Optional[str],
) -> dict:
    if fields["realm_type"] == "proxy":
        _validate_proxy_complete(fields.get("proxy_host"), fields.get("proxy_secret"))

    await _validate_ldap_server_ref(
        db, tenant_id=actor["tenant_id"],
        ldap_server_id=fields.get("ldap_server_id"),
    )
    await _validate_fallback_realm_ref(
        db, tenant_id=actor["tenant_id"],
        fallback_realm_id=fields.get("fallback_realm_id"),
    )

    row = await repo.insert_realm(
        db, tenant_id=actor["tenant_id"], fields=fields,
    )
    await log_audit(
        db, actor,
        action="create", resource_type="radius_realm",
        resource_id=str(row["id"]),
        details={
            "name": fields["name"],
            "realm_type": fields["realm_type"],
            "proxy_host": fields.get("proxy_host"),
        },
        ip_address=client_ip,
    )
    await events.publish_freeradius_apply(
        reason="realm_created",
        realm_id=row["id"],
        realm_name=fields["name"],
    )
    return dict(row)


async def update_realm(
    db: AsyncSession,
    actor: dict,
    *,
    realm_id: UUID,
    updates: dict,
    client_ip: Optional[str],
) -> dict:
    if not updates:
        raise ValidationError("No fields to update")

    # If switching to proxy, verify proxy_host + secret are present (in
    # the update OR already on the row).
    if updates.get("realm_type") == "proxy":
        existing = await repo.lookup_proxy_state(
            db, tenant_id=actor["tenant_id"], realm_id=realm_id,
        )
        if not existing:
            raise NotFoundError("RADIUS realm", str(realm_id))
        _validate_proxy_complete(
            updates.get("proxy_host") or existing["proxy_host"],
            updates.get("proxy_secret") or existing["proxy_secret_encrypted"],
        )

    if "ldap_server_id" in updates:
        await _validate_ldap_server_ref(
            db, tenant_id=actor["tenant_id"],
            ldap_server_id=updates["ldap_server_id"],
        )

    try:
        row = await repo.update_realm(
            db, tenant_id=actor["tenant_id"],
            realm_id=realm_id, updates=updates,
        )
    except ValueError:
        raise ValidationError("No valid fields to update")

    if not row:
        raise NotFoundError("RADIUS realm", str(realm_id))

    await log_audit(
        db, actor,
        action="update", resource_type="radius_realm",
        resource_id=str(realm_id),
        details={"changed_fields": list(updates.keys())},
        ip_address=client_ip,
    )
    await events.publish_freeradius_apply(
        reason="realm_updated", realm_id=realm_id,
    )
    return dict(row)


async def delete_realm(
    db: AsyncSession,
    actor: dict,
    *,
    realm_id: UUID,
    client_ip: Optional[str],
) -> None:
    ref_count = await repo.count_fallback_references(
        db, tenant_id=actor["tenant_id"], realm_id=realm_id,
    )
    if ref_count > 0:
        raise ConflictError(
            f"Cannot delete: realm is referenced as fallback by "
            f"{ref_count} other realm(s)."
        )

    existing = await repo.lookup_realm_summary(
        db, tenant_id=actor["tenant_id"], realm_id=realm_id,
    )
    if not existing:
        raise NotFoundError("RADIUS realm", str(realm_id))

    await repo.delete_realm(
        db, tenant_id=actor["tenant_id"], realm_id=realm_id,
    )
    await log_audit(
        db, actor,
        action="delete", resource_type="radius_realm",
        resource_id=str(realm_id),
        details={
            "name": existing["name"],
            "realm_type": existing["realm_type"],
        },
        ip_address=client_ip,
    )
    await events.publish_freeradius_apply(
        reason="realm_deleted", realm_id=realm_id,
    )
