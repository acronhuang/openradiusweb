"""Use-case composition for the settings feature (Layer 2).

Notes:
- **Secret masking** (`is_secret = true`) hides values on read AND in audit
  details for both old and new values.
- **Restart use-case** routes to `events.publish_service_restart` for
  background services. Gateway self-exit is route-layer concern (it's a
  Layer-3 process operation, not a use case).
- **Health-probe use-case** lives in `routes.py` because it's pure
  outbound I/O against external sockets, not domain logic.
"""
from typing import Any, Mapping, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.exceptions import (
    AuthorizationError,
    NotFoundError,
    ValidationError,
)
from utils.audit import log_audit

from . import events
from . import repository as repo


_INFRA_SERVICES = {"postgres", "redis", "nats", "frontend"}


def _mask_sensitive(row: Mapping[str, Any]) -> dict:
    out = dict(row)
    if out.get("is_secret"):
        out["setting_value"] = "********"
    return out


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

async def get_all_settings_grouped(
    db: AsyncSession, *, tenant_id: Optional[str],
) -> dict:
    """Return `{"categories": {category_name: [setting, ...]}}` with secrets masked."""
    rows = await repo.list_all_settings(db, tenant_id=tenant_id)
    categories: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        entry = _mask_sensitive(row)
        cat = entry.pop("category", "general")
        categories.setdefault(cat, []).append(entry)
    return {"categories": categories}


async def get_settings_by_category(
    db: AsyncSession, *, tenant_id: Optional[str], category: str,
) -> dict:
    """Raises NotFoundError if no settings exist in the category."""
    rows = await repo.list_settings_by_category(
        db, tenant_id=tenant_id, category=category,
    )
    if not rows:
        raise NotFoundError("settings category", category)
    return {
        "category": category,
        "settings": [_mask_sensitive(r) for r in rows],
    }


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

async def update_settings_batch(
    db: AsyncSession,
    actor: dict,
    *,
    category: str,
    settings_map: dict[str, str],
    client_ip: Optional[str],
) -> dict:
    """Apply a batch of (key → value) updates within one category.

    Unknown keys are silently skipped. If no key matches an existing row,
    raises ValidationError.
    """
    existing = await repo.lookup_settings_for_audit(
        db,
        tenant_id=actor["tenant_id"],
        category=category,
        keys=list(settings_map.keys()),
    )

    updated_keys: list[str] = []
    audit_changes: list[dict[str, Any]] = []

    for key, new_value in settings_map.items():
        old_row = existing.get(key)
        if old_row is None:
            continue
        is_secret = old_row["is_secret"]

        await repo.update_setting_value(
            db,
            tenant_id=actor["tenant_id"],
            category=category,
            key=key,
            value=str(new_value),
        )
        updated_keys.append(key)
        audit_changes.append({
            "key": key,
            "old_value": "********" if is_secret else old_row["setting_value"],
            "new_value": "********" if is_secret else str(new_value),
        })

    if not updated_keys:
        raise ValidationError("No valid settings keys found for this category")

    await log_audit(
        db, actor,
        action="update", resource_type="system_settings",
        resource_id=None,
        details={"category": category, "changes": audit_changes},
        ip_address=client_ip,
    )
    return {"updated": updated_keys, "category": category}


# ---------------------------------------------------------------------------
# Service restart (NATS dispatch)
# ---------------------------------------------------------------------------

async def request_service_restart(
    db: AsyncSession,
    actor: dict,
    *,
    service_name: str,
    client_ip: Optional[str],
) -> dict:
    """Publish a restart request for a background service.

    Raises:
        AuthorizationError: if `service_name` is an infrastructure service
            (postgres/redis/nats/frontend) — those must be restarted via SSH.
        ValidationError: if `service_name` is unknown.

    The caller is responsible for handling the special `gateway` case
    (self-exit) at the route layer.
    """
    if service_name in _INFRA_SERVICES:
        raise AuthorizationError(
            f"Cannot restart {service_name} from Web UI. "
            f"Use SSH to restart infrastructure services."
        )
    if service_name not in events.SERVICE_RESTART_TOPICS:
        raise ValidationError(f"Unknown service: {service_name}")

    await events.publish_service_restart(
        service_name=service_name,
        requested_by=actor.get("username", actor.get("sub")),
    )
    await log_audit(
        db, actor,
        action="restart", resource_type="service",
        details={"service": service_name},
        ip_address=client_ip,
    )
    return {
        "status": "restart_requested",
        "service": service_name,
        "message": f"Restart request sent to {service_name}",
    }
