"""Database atoms for the freeradius_config feature."""
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# Stored configs (status + content)
# ---------------------------------------------------------------------------

async def list_config_status(
    db: AsyncSession, *, tenant_id: str,
) -> list[Mapping[str, Any]]:
    """Status-shape rows for `/config` endpoint."""
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
    return list(result.mappings().all())


async def list_config_preview(
    db: AsyncSession, *, tenant_id: str,
) -> list[Mapping[str, Any]]:
    """Content-shape rows for `/config/preview` endpoint."""
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
    return list(result.mappings().all())


# ---------------------------------------------------------------------------
# Source-data counts (one per table)
# ---------------------------------------------------------------------------

async def count_enabled_ldap_servers(db: AsyncSession, *, tenant_id: str) -> int:
    result = await db.execute(
        text(
            "SELECT COUNT(*) FROM ldap_servers "
            "WHERE tenant_id = :tid AND enabled = true"
        ),
        {"tid": tenant_id},
    )
    return int(result.scalar() or 0)


async def count_enabled_realms(db: AsyncSession, *, tenant_id: str) -> int:
    result = await db.execute(
        text(
            "SELECT COUNT(*) FROM radius_realms "
            "WHERE tenant_id = :tid AND enabled = true"
        ),
        {"tid": tenant_id},
    )
    return int(result.scalar() or 0)


async def count_enabled_nas_clients(db: AsyncSession, *, tenant_id: str) -> int:
    result = await db.execute(
        text(
            "SELECT COUNT(*) FROM radius_nas_clients "
            "WHERE tenant_id = :tid AND enabled = true"
        ),
        {"tid": tenant_id},
    )
    return int(result.scalar() or 0)


async def count_active_certificates(db: AsyncSession, *, tenant_id: str) -> int:
    result = await db.execute(
        text(
            "SELECT COUNT(*) FROM certificates "
            "WHERE tenant_id = :tid AND is_active = true"
        ),
        {"tid": tenant_id},
    )
    return int(result.scalar() or 0)


# ---------------------------------------------------------------------------
# History (audit_log filtered to this resource type)
# ---------------------------------------------------------------------------

async def count_config_history(db: AsyncSession, *, tenant_id: str) -> int:
    result = await db.execute(
        text(
            "SELECT COUNT(*) FROM audit_log "
            "WHERE tenant_id = :tenant_id "
            "AND resource_type = 'freeradius_config'"
        ),
        {"tenant_id": tenant_id},
    )
    return int(result.scalar() or 0)


async def list_config_history(
    db: AsyncSession, *, tenant_id: str, limit: int, offset: int,
) -> list[Mapping[str, Any]]:
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
        {"tenant_id": tenant_id, "limit": limit, "offset": offset},
    )
    return list(result.mappings().all())
