"""Use-case composition for the group_vlan_mappings feature (Layer 2).

Notable shapes vs. the vanilla CRUD vlans template:
- **Uniqueness check** on create + on rename (group_name within tenant) →
  raises ConflictError. Centralised in `_assert_group_name_free`.
- **Lookup-by-groups** for FreeRADIUS post_auth — comma-separated input
  parsed in service, NULL-safe response shape `{"match": dict | None}`.
"""
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.exceptions import ConflictError, NotFoundError, ValidationError
from utils.audit import log_audit

from . import repository as repo


async def _assert_group_name_free(
    db: AsyncSession,
    *,
    tenant_id: str,
    group_name: str,
    excluding_id: Optional[UUID] = None,
) -> None:
    if await repo.group_name_taken(
        db, tenant_id=tenant_id, group_name=group_name, excluding_id=excluding_id,
    ):
        raise ConflictError(f"Mapping for group '{group_name}' already exists")


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

async def list_mappings(db: AsyncSession, *, tenant_id: str) -> dict:
    rows = await repo.list_mappings(db, tenant_id=tenant_id)
    items = [dict(r) for r in rows]
    return {"items": items, "total": len(items)}


async def get_mapping(
    db: AsyncSession, *, tenant_id: str, mapping_id: UUID,
) -> dict:
    row = await repo.lookup_mapping(db, tenant_id=tenant_id, mapping_id=mapping_id)
    if not row:
        raise NotFoundError("Mapping", str(mapping_id))
    return dict(row)


async def lookup_vlan_for_groups(
    db: AsyncSession, *, tenant_id: str, groups_csv: str,
) -> dict:
    """Parse comma-separated groups and return the highest-priority VLAN match.

    Always returns `{"match": dict_or_None}` for a stable contract — empty
    input or no match both yield `{"match": None}`.
    """
    parsed = [g.strip() for g in groups_csv.split(",") if g.strip()]
    if not parsed:
        return {"match": None}
    row = await repo.lookup_vlan_for_groups(
        db, tenant_id=tenant_id, groups=parsed,
    )
    return {"match": dict(row) if row else None}


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

async def create_mapping(
    db: AsyncSession,
    actor: dict,
    *,
    group_name: str,
    vlan_id: int,
    priority: int,
    description: Optional[str],
    ldap_server_id: Optional[str],
    enabled: bool,
    client_ip: Optional[str],
) -> dict:
    await _assert_group_name_free(
        db, tenant_id=actor["tenant_id"], group_name=group_name,
    )
    row = await repo.insert_mapping(
        db,
        tenant_id=actor["tenant_id"],
        group_name=group_name,
        vlan_id=vlan_id,
        priority=priority,
        description=description,
        ldap_server_id=ldap_server_id,
        enabled=enabled,
    )
    await log_audit(
        db, actor,
        action="create", resource_type="group_vlan_mapping",
        resource_id=str(row["id"]),
        details={"group_name": group_name, "vlan_id": vlan_id},
        ip_address=client_ip,
    )
    return dict(row)


async def update_mapping(
    db: AsyncSession,
    actor: dict,
    *,
    mapping_id: UUID,
    updates: dict,
    client_ip: Optional[str],
) -> dict:
    if not updates:
        raise ValidationError("No fields to update")

    existing = await repo.lookup_mapping_summary(
        db, tenant_id=actor["tenant_id"], mapping_id=mapping_id,
    )
    if not existing:
        raise NotFoundError("Mapping", str(mapping_id))

    # Conflict check only when renaming to a different name.
    new_name = updates.get("group_name")
    if new_name is not None and new_name != existing["group_name"]:
        await _assert_group_name_free(
            db,
            tenant_id=actor["tenant_id"],
            group_name=new_name,
            excluding_id=mapping_id,
        )

    try:
        row = await repo.update_mapping(
            db, tenant_id=actor["tenant_id"],
            mapping_id=mapping_id, updates=updates,
        )
    except ValueError:
        raise ValidationError("No valid fields to update")

    if not row:
        raise NotFoundError("Mapping", str(mapping_id))

    await log_audit(
        db, actor,
        action="update", resource_type="group_vlan_mapping",
        resource_id=str(mapping_id),
        details={"changed_fields": updates, "group_name": existing["group_name"]},
        ip_address=client_ip,
    )
    return dict(row)


async def delete_mapping(
    db: AsyncSession,
    actor: dict,
    *,
    mapping_id: UUID,
    client_ip: Optional[str],
) -> None:
    existing = await repo.lookup_mapping_summary(
        db, tenant_id=actor["tenant_id"], mapping_id=mapping_id,
    )
    if not existing:
        raise NotFoundError("Mapping", str(mapping_id))

    await repo.delete_mapping(
        db, tenant_id=actor["tenant_id"], mapping_id=mapping_id,
    )
    await log_audit(
        db, actor,
        action="delete", resource_type="group_vlan_mapping",
        resource_id=str(mapping_id),
        details={
            "group_name": existing["group_name"],
            "vlan_id": existing["vlan_id"],
        },
        ip_address=client_ip,
    )
