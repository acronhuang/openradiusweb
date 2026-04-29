"""Use-case composition for the dot1x_overview feature (Layer 2).

Single use case: orchestrate 10 repository atoms and assemble a
dashboard payload. All shape-and-default logic lives here so the
repo atoms stay schema-pure.
"""
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from . import repository as repo


_DEFAULT_EAP_METHODS = ["PEAP", "EAP-TLS", "EAP-TTLS"]


async def get_overview(db: AsyncSession, *, tenant_id: str) -> dict:
    """Aggregated 802.1X status overview — 10 atoms, one dashboard payload."""
    eap_settings = await repo.get_radius_settings(db, tenant_id=tenant_id)
    enabled_methods = await repo.list_enabled_realm_auth_methods(
        db, tenant_id=tenant_id,
    )
    certs = await repo.list_enabled_certificates(db, tenant_id=tenant_id)
    vlans = await repo.list_enabled_vlans(db, tenant_id=tenant_id)
    mab = await repo.count_mab_devices(db, tenant_id=tenant_id)
    realm_counts = await repo.count_realms_by_type(db, tenant_id=tenant_id)
    nas = await repo.count_nas_clients(db, tenant_id=tenant_id)
    pol = await repo.count_policies(db, tenant_id=tenant_id)
    gvm = await repo.count_group_vlan_mappings(db, tenant_id=tenant_id)
    auth = await repo.auth_stats_24h(db)
    auth_by_method = await repo.auth_methods_24h(db)

    return {
        "eap_methods": _eap_block(eap_settings, enabled_methods),
        "certificates": _certs_block(certs),
        "vlans": _vlans_block(vlans),
        "mab_devices": _count_triple(
            mab, ("total", "enabled_count", "expired"),
            ("total", "enabled", "expired"),
        ),
        "realms": _realms_block(realm_counts),
        "nas_clients": _count_pair(nas),
        "policies": _count_pair(pol),
        "group_vlan_mappings": _count_pair(gvm),
        "auth_stats_24h": _auth_stats_block(auth, auth_by_method),
    }


# ---------------------------------------------------------------------------
# Block builders (small, named helpers — easier to test than one giant fn)
# ---------------------------------------------------------------------------

def _eap_block(
    settings: dict[str, str], enabled_methods: list[str],
) -> dict[str, Any]:
    return {
        "enabled": enabled_methods or _DEFAULT_EAP_METHODS,
        "default": settings.get("default_eap_type", "peap"),
        "tls_min_version": settings.get("tls_min_version", "1.2"),
        "auth_port": settings.get("auth_port", "1812"),
        "acct_port": settings.get("acct_port", "1813"),
    }


def _certs_block(certs: list) -> dict[str, Any]:
    """ca/server counts + nearest-expiry helpers."""
    nearest_str: str | None = None
    nearest_name: str | None = None
    for c in certs:
        if c["not_after"] and c["is_active"]:
            nearest_str = str(c["not_after"])
            nearest_name = c["name"]
            break
    return {
        "ca_count": sum(1 for c in certs if c["cert_type"] == "ca"),
        "server_count": sum(1 for c in certs if c["cert_type"] == "server"),
        "ca_active": any(
            c["cert_type"] == "ca" and c["is_active"] for c in certs
        ),
        "server_active": any(
            c["cert_type"] == "server" and c["is_active"] for c in certs
        ),
        "nearest_expiry": nearest_str,
        "nearest_expiry_name": nearest_name,
    }


def _vlans_block(vlans: list) -> dict[str, Any]:
    by_purpose: dict[str, list] = {}
    for v in vlans:
        purpose = v["purpose"] or "other"
        by_purpose.setdefault(purpose, []).append(
            {"vlan_id": v["vlan_id"], "name": v["name"]}
        )
    return {"total": len(vlans), "by_purpose": by_purpose}


def _realms_block(counts: dict[str, int]) -> dict[str, int]:
    return {
        "total": sum(counts.values()),
        "local": counts.get("local", 0),
        "proxy": counts.get("proxy", 0),
    }


def _count_pair(row) -> dict[str, int]:
    """Map (total, enabled_count) → response shape (total, enabled)."""
    return {"total": row["total"], "enabled": row["enabled_count"]}


def _count_triple(
    row, src_keys: tuple[str, ...], dst_keys: tuple[str, ...],
) -> dict[str, int]:
    """Map arbitrary triple of repo cols to api response keys."""
    return {dst: row[src] for src, dst in zip(src_keys, dst_keys)}


def _auth_stats_block(stats, by_method: dict[str, int]) -> dict[str, Any]:
    total = stats["total"] or 0
    success = stats["success"] or 0
    return {
        "total": total,
        "success": success,
        "failed": total - success,
        "success_rate": round(success / total * 100, 1) if total > 0 else 0,
        "by_method": by_method,
    }
