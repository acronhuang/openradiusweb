"""802.1X Overview aggregation endpoint."""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.database import get_db
from middleware.auth import get_current_user

router = APIRouter(prefix="/dot1x")


@router.get("/overview")
async def get_dot1x_overview(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Aggregated 802.1X status overview."""
    tid = user["tenant_id"]

    # EAP methods from system settings
    eap_result = await db.execute(
        text(
            "SELECT setting_key, setting_value FROM system_settings "
            "WHERE category = 'radius' AND tenant_id = :tid "
            "AND setting_key IN ('default_eap_type', 'tls_min_version', "
            "'auth_port', 'acct_port', 'coa_port')"
        ),
        {"tid": tid},
    )
    eap_settings = {r["setting_key"]: r["setting_value"] for r in eap_result.mappings().all()}

    # Auth types from realms
    realm_result = await db.execute(
        text(
            "SELECT DISTINCT unnest(auth_types_allowed) AS method "
            "FROM radius_realms WHERE tenant_id = :tid AND enabled = true"
        ),
        {"tid": tid},
    )
    enabled_methods = [r["method"] for r in realm_result.mappings().all()]

    # Certificates
    cert_result = await db.execute(
        text(
            "SELECT cert_type, is_active, not_after, name "
            "FROM certificates WHERE tenant_id = :tid AND enabled = true "
            "ORDER BY not_after ASC"
        ),
        {"tid": tid},
    )
    certs = cert_result.mappings().all()
    ca_active = any(c["cert_type"] == "ca" and c["is_active"] for c in certs)
    server_active = any(c["cert_type"] == "server" and c["is_active"] for c in certs)
    nearest = None
    nearest_name = None
    for c in certs:
        if c["not_after"] and c["is_active"]:
            nearest = str(c["not_after"])
            nearest_name = c["name"]
            break

    # VLANs
    vlan_result = await db.execute(
        text(
            "SELECT vlan_id, name, purpose FROM vlans "
            "WHERE tenant_id = :tid AND enabled = true ORDER BY vlan_id"
        ),
        {"tid": tid},
    )
    vlans = vlan_result.mappings().all()
    vlans_by_purpose: dict = {}
    for v in vlans:
        p = v["purpose"] or "other"
        vlans_by_purpose.setdefault(p, []).append(
            {"vlan_id": v["vlan_id"], "name": v["name"]}
        )

    # MAB devices
    mab_result = await db.execute(
        text(
            "SELECT "
            "COUNT(*) AS total, "
            "COUNT(*) FILTER (WHERE enabled = true) AS enabled_count, "
            "COUNT(*) FILTER (WHERE expiry_date IS NOT NULL AND expiry_date < NOW()) AS expired "
            "FROM mab_devices WHERE tenant_id = :tid"
        ),
        {"tid": tid},
    )
    mab = mab_result.mappings().first()

    # Realms
    realm_count_result = await db.execute(
        text(
            "SELECT realm_type, COUNT(*) AS cnt "
            "FROM radius_realms WHERE tenant_id = :tid "
            "GROUP BY realm_type"
        ),
        {"tid": tid},
    )
    realm_counts = {r["realm_type"]: r["cnt"] for r in realm_count_result.mappings().all()}

    # NAS clients
    nas_result = await db.execute(
        text(
            "SELECT COUNT(*) AS total, "
            "COUNT(*) FILTER (WHERE enabled = true) AS enabled_count "
            "FROM radius_nas_clients WHERE tenant_id = :tid"
        ),
        {"tid": tid},
    )
    nas = nas_result.mappings().first()

    # Policies
    policy_result = await db.execute(
        text(
            "SELECT COUNT(*) AS total, "
            "COUNT(*) FILTER (WHERE enabled = true) AS enabled_count "
            "FROM policies WHERE tenant_id = :tid"
        ),
        {"tid": tid},
    )
    pol = policy_result.mappings().first()

    # Group VLAN mappings
    gvm_result = await db.execute(
        text(
            "SELECT COUNT(*) AS total, "
            "COUNT(*) FILTER (WHERE enabled = true) AS enabled_count "
            "FROM group_vlan_mappings WHERE tenant_id = :tid"
        ),
        {"tid": tid},
    )
    gvm = gvm_result.mappings().first()

    # Auth stats (last 24h)
    auth_result = await db.execute(
        text(
            "SELECT "
            "COUNT(*) AS total, "
            "COUNT(*) FILTER (WHERE auth_result = 'success') AS success, "
            "COUNT(*) FILTER (WHERE auth_result != 'success') AS failed "
            "FROM radius_auth_log "
            "WHERE timestamp > NOW() - INTERVAL '24 hours'"
        ),
    )
    auth = auth_result.mappings().first()

    auth_method_result = await db.execute(
        text(
            "SELECT auth_method, COUNT(*) AS cnt "
            "FROM radius_auth_log "
            "WHERE timestamp > NOW() - INTERVAL '24 hours' "
            "GROUP BY auth_method ORDER BY cnt DESC"
        ),
    )
    auth_by_method = {
        r["auth_method"]: r["cnt"]
        for r in auth_method_result.mappings().all()
    }

    total_auth = auth["total"] if auth["total"] else 0
    success_auth = auth["success"] if auth["success"] else 0

    return {
        "eap_methods": {
            "enabled": enabled_methods or ["PEAP", "EAP-TLS", "EAP-TTLS"],
            "default": eap_settings.get("default_eap_type", "peap"),
            "tls_min_version": eap_settings.get("tls_min_version", "1.2"),
            "auth_port": eap_settings.get("auth_port", "1812"),
            "acct_port": eap_settings.get("acct_port", "1813"),
        },
        "certificates": {
            "ca_count": sum(1 for c in certs if c["cert_type"] == "ca"),
            "server_count": sum(1 for c in certs if c["cert_type"] == "server"),
            "ca_active": ca_active,
            "server_active": server_active,
            "nearest_expiry": nearest,
            "nearest_expiry_name": nearest_name,
        },
        "vlans": {
            "total": len(vlans),
            "by_purpose": vlans_by_purpose,
        },
        "mab_devices": {
            "total": mab["total"],
            "enabled": mab["enabled_count"],
            "expired": mab["expired"],
        },
        "realms": {
            "total": sum(realm_counts.values()),
            "local": realm_counts.get("local", 0),
            "proxy": realm_counts.get("proxy", 0),
        },
        "nas_clients": {
            "total": nas["total"],
            "enabled": nas["enabled_count"],
        },
        "policies": {
            "total": pol["total"],
            "enabled": pol["enabled_count"],
        },
        "group_vlan_mappings": {
            "total": gvm["total"],
            "enabled": gvm["enabled_count"],
        },
        "auth_stats_24h": {
            "total": total_auth,
            "success": success_auth,
            "failed": total_auth - success_auth,
            "success_rate": round(success_auth / total_auth * 100, 1) if total_auth > 0 else 0,
            "by_method": auth_by_method,
        },
    }
