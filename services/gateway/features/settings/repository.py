"""Database atoms for the settings feature.

`system_settings` rows can be tenant-scoped or global; the WHERE clause
`(tenant_id = :uuid OR IS NULL)` is shared across every read.
"""
from typing import Any, Mapping, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


_TENANT_CLAUSE = "(tenant_id = :tenant_id OR tenant_id IS NULL)"


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

async def list_all_settings(
    db: AsyncSession, *, tenant_id: Optional[str],
) -> list[Mapping[str, Any]]:
    result = await db.execute(
        text(
            "SELECT setting_key, setting_value, value_type, "
            "category, description, is_secret "
            f"FROM system_settings WHERE {_TENANT_CLAUSE} "
            "ORDER BY category, setting_key"
        ),
        {"tenant_id": tenant_id},
    )
    return list(result.mappings().all())


async def list_settings_by_category(
    db: AsyncSession, *, tenant_id: Optional[str], category: str,
) -> list[Mapping[str, Any]]:
    result = await db.execute(
        text(
            "SELECT setting_key, setting_value, value_type, "
            "category, description, is_secret "
            "FROM system_settings "
            f"WHERE category = :category AND {_TENANT_CLAUSE} "
            "ORDER BY setting_key"
        ),
        {"category": category, "tenant_id": tenant_id},
    )
    return list(result.mappings().all())


async def lookup_settings_for_audit(
    db: AsyncSession,
    *,
    tenant_id: Optional[str],
    category: str,
    keys: list[str],
) -> dict[str, Mapping[str, Any]]:
    """Fetch existing rows for the given keys, returned keyed by setting_key.

    Used by update_settings_batch to capture old values for audit context
    and to filter out unknown keys.
    """
    result = await db.execute(
        text(
            "SELECT setting_key, setting_value, is_secret FROM system_settings "
            f"WHERE category = :category AND setting_key = ANY(:keys) "
            f"AND {_TENANT_CLAUSE}"
        ),
        {"category": category, "keys": keys, "tenant_id": tenant_id},
    )
    return {r["setting_key"]: r for r in result.mappings().all()}


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

async def update_setting_value(
    db: AsyncSession,
    *,
    tenant_id: Optional[str],
    category: str,
    key: str,
    value: str,
) -> None:
    await db.execute(
        text(
            "UPDATE system_settings SET setting_value = :value, updated_at = NOW() "
            f"WHERE category = :category AND setting_key = :key AND {_TENANT_CLAUSE}"
        ),
        {
            "value": value,
            "category": category,
            "key": key,
            "tenant_id": tenant_id,
        },
    )
