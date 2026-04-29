"""Database atoms for the dot1x_overview feature.

10 single-responsibility queries across 9 tables. Each atom returns
the smallest shape needed by the service-layer assembly. The service
performs all the dict-building and conditional logic.

Two queries (auth log: stats + by-method) are not tenant-scoped because
the legacy endpoint behaved that way; flagged in dot1x_overview tracker
as a follow-up to reconsider.
"""
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# Settings (radius category)
# ---------------------------------------------------------------------------

_RADIUS_SETTING_KEYS = (
    "default_eap_type", "tls_min_version",
    "auth_port", "acct_port", "coa_port",
)


async def get_radius_settings(
    db: AsyncSession, *, tenant_id: str,
) -> dict[str, str]:
    """Return setting_key → setting_value for known radius settings."""
    result = await db.execute(
        text(
            "SELECT setting_key, setting_value FROM system_settings "
            "WHERE category = 'radius' AND tenant_id = :tid "
            "AND setting_key IN ('default_eap_type', 'tls_min_version', "
            "'auth_port', 'acct_port', 'coa_port')"
        ),
        {"tid": tenant_id},
    )
    return {
        r["setting_key"]: r["setting_value"]
        for r in result.mappings().all()
    }


# ---------------------------------------------------------------------------
# Realms
# ---------------------------------------------------------------------------

async def list_enabled_realm_auth_methods(
    db: AsyncSession, *, tenant_id: str,
) -> list[str]:
    """Distinct auth methods declared by enabled realms."""
    result = await db.execute(
        text(
            "SELECT DISTINCT unnest(auth_types_allowed) AS method "
            "FROM radius_realms WHERE tenant_id = :tid AND enabled = true"
        ),
        {"tid": tenant_id},
    )
    return [r["method"] for r in result.mappings().all()]


async def count_realms_by_type(
    db: AsyncSession, *, tenant_id: str,
) -> dict[str, int]:
    """realm_type → count, including disabled rows."""
    result = await db.execute(
        text(
            "SELECT realm_type, COUNT(*) AS cnt "
            "FROM radius_realms WHERE tenant_id = :tid "
            "GROUP BY realm_type"
        ),
        {"tid": tenant_id},
    )
    return {r["realm_type"]: r["cnt"] for r in result.mappings().all()}


# ---------------------------------------------------------------------------
# Certificates
# ---------------------------------------------------------------------------

async def list_enabled_certificates(
    db: AsyncSession, *, tenant_id: str,
) -> list[Mapping[str, Any]]:
    """Ordered by expiry ascending (nearest first)."""
    result = await db.execute(
        text(
            "SELECT cert_type, is_active, not_after, name "
            "FROM certificates WHERE tenant_id = :tid AND enabled = true "
            "ORDER BY not_after ASC"
        ),
        {"tid": tenant_id},
    )
    return list(result.mappings().all())


# ---------------------------------------------------------------------------
# VLANs
# ---------------------------------------------------------------------------

async def list_enabled_vlans(
    db: AsyncSession, *, tenant_id: str,
) -> list[Mapping[str, Any]]:
    result = await db.execute(
        text(
            "SELECT vlan_id, name, purpose FROM vlans "
            "WHERE tenant_id = :tid AND enabled = true ORDER BY vlan_id"
        ),
        {"tid": tenant_id},
    )
    return list(result.mappings().all())


# ---------------------------------------------------------------------------
# MAB / NAS / Policies / Group-VLAN counts (same shape: total + enabled)
# ---------------------------------------------------------------------------

async def count_mab_devices(
    db: AsyncSession, *, tenant_id: str,
) -> Mapping[str, Any]:
    """Returns total / enabled_count / expired."""
    result = await db.execute(
        text(
            "SELECT "
            "COUNT(*) AS total, "
            "COUNT(*) FILTER (WHERE enabled = true) AS enabled_count, "
            "COUNT(*) FILTER ("
            "    WHERE expiry_date IS NOT NULL AND expiry_date < NOW()"
            ") AS expired "
            "FROM mab_devices WHERE tenant_id = :tid"
        ),
        {"tid": tenant_id},
    )
    return result.mappings().first() or {"total": 0, "enabled_count": 0, "expired": 0}


async def count_nas_clients(
    db: AsyncSession, *, tenant_id: str,
) -> Mapping[str, Any]:
    return await _count_total_and_enabled(db, "radius_nas_clients", tenant_id)


async def count_policies(
    db: AsyncSession, *, tenant_id: str,
) -> Mapping[str, Any]:
    return await _count_total_and_enabled(db, "policies", tenant_id)


async def count_group_vlan_mappings(
    db: AsyncSession, *, tenant_id: str,
) -> Mapping[str, Any]:
    return await _count_total_and_enabled(db, "group_vlan_mappings", tenant_id)


async def _count_total_and_enabled(
    db: AsyncSession, table: str, tenant_id: str,
) -> Mapping[str, Any]:
    """Shared helper for the three identical-shape COUNT queries."""
    result = await db.execute(
        text(
            f"SELECT COUNT(*) AS total, "
            f"COUNT(*) FILTER (WHERE enabled = true) AS enabled_count "
            f"FROM {table} WHERE tenant_id = :tid"
        ),
        {"tid": tenant_id},
    )
    return result.mappings().first() or {"total": 0, "enabled_count": 0}


# ---------------------------------------------------------------------------
# Auth stats (last 24h) — NOT tenant-scoped (legacy behavior preserved)
# ---------------------------------------------------------------------------

async def auth_stats_24h(db: AsyncSession) -> Mapping[str, Any]:
    """Returns total / success / failed counts for the last 24h."""
    result = await db.execute(
        text(
            "SELECT "
            "COUNT(*) AS total, "
            "COUNT(*) FILTER (WHERE auth_result = 'success') AS success, "
            "COUNT(*) FILTER (WHERE auth_result != 'success') AS failed "
            "FROM radius_auth_log "
            "WHERE timestamp > NOW() - INTERVAL '24 hours'"
        ),
    )
    return result.mappings().first() or {"total": 0, "success": 0, "failed": 0}


async def auth_methods_24h(db: AsyncSession) -> dict[str, int]:
    """auth_method → count for the last 24h, ordered by count desc."""
    result = await db.execute(
        text(
            "SELECT auth_method, COUNT(*) AS cnt "
            "FROM radius_auth_log "
            "WHERE timestamp > NOW() - INTERVAL '24 hours' "
            "GROUP BY auth_method ORDER BY cnt DESC"
        ),
    )
    return {r["auth_method"]: r["cnt"] for r in result.mappings().all()}
