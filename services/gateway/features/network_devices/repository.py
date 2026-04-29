"""Database atoms for the network_devices feature.

The DB column is `snmp_community_encrypted` but the request field is
`snmp_community` — column-mapping lives in the insert atom so the
route layer doesn't carry SQL detail. List/get include a `port_count`
subquery so the UI can show counts without a second round-trip.

Switch port list does a LEFT JOIN on the devices table to enrich each
port row with `connected_device` (id/mac/hostname/ip).
"""
from typing import Any, Mapping, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# Network devices (switches/routers/APs)
# ---------------------------------------------------------------------------

async def count_network_devices(
    db: AsyncSession,
    *,
    tenant_id: str,
    device_type: Optional[str] = None,
    vendor: Optional[str] = None,
) -> int:
    where, params = _filter_clause(tenant_id, device_type, vendor)
    result = await db.execute(
        text(f"SELECT COUNT(*) FROM network_devices WHERE {where}"), params,
    )
    return int(result.scalar() or 0)


async def list_network_devices(
    db: AsyncSession,
    *,
    tenant_id: str,
    device_type: Optional[str],
    vendor: Optional[str],
    limit: int,
    offset: int,
) -> list[Mapping[str, Any]]:
    where, params = _filter_clause(tenant_id, device_type, vendor)
    params["limit"] = limit
    params["offset"] = offset
    result = await db.execute(
        text(
            "SELECT nd.*, "
            "(SELECT COUNT(*) FROM switch_ports sp "
            "WHERE sp.network_device_id = nd.id) as port_count "
            f"FROM network_devices nd WHERE {where} "
            "ORDER BY nd.hostname, nd.ip_address LIMIT :limit OFFSET :offset"
        ),
        params,
    )
    return list(result.mappings().all())


async def lookup_network_device(
    db: AsyncSession, *, tenant_id: str, device_id: UUID,
) -> Optional[Mapping[str, Any]]:
    result = await db.execute(
        text(
            "SELECT nd.*, "
            "(SELECT COUNT(*) FROM switch_ports sp "
            "WHERE sp.network_device_id = nd.id) as port_count "
            "FROM network_devices nd "
            "WHERE nd.id = :id AND nd.tenant_id = :tenant_id"
        ),
        {"id": str(device_id), "tenant_id": tenant_id},
    )
    return result.mappings().first()


async def network_device_exists(
    db: AsyncSession, *, tenant_id: str, device_id: UUID,
) -> bool:
    """Used by port atoms to validate parent before listing/mutating."""
    result = await db.execute(
        text(
            "SELECT 1 FROM network_devices "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(device_id), "tenant_id": tenant_id},
    )
    return result.first() is not None


async def insert_network_device(
    db: AsyncSession,
    *,
    tenant_id: str,
    ip_address: str,
    hostname: Optional[str],
    vendor: Optional[str],
    model: Optional[str],
    os_version: Optional[str],
    device_type: str,
    management_protocol: str,
    snmp_version: Optional[str],
    snmp_community: Optional[str],
    poll_interval_seconds: int,
) -> Mapping[str, Any]:
    """`snmp_community` (request) → `snmp_community_encrypted` (DB column)."""
    result = await db.execute(
        text(
            "INSERT INTO network_devices "
            "(ip_address, hostname, vendor, model, os_version, device_type, "
            "management_protocol, snmp_version, snmp_community_encrypted, "
            "poll_interval_seconds, tenant_id) "
            "VALUES (:ip_address, :hostname, :vendor, :model, :os_version, "
            ":device_type, :management_protocol, :snmp_version, "
            ":snmp_community, :poll_interval, :tenant_id) "
            "RETURNING *"
        ),
        {
            "ip_address": ip_address,
            "hostname": hostname,
            "vendor": vendor,
            "model": model,
            "os_version": os_version,
            "device_type": device_type,
            "management_protocol": management_protocol,
            "snmp_version": snmp_version,
            "snmp_community": snmp_community,  # TODO: encrypt via Vault
            "poll_interval": poll_interval_seconds,
            "tenant_id": tenant_id,
        },
    )
    row = result.mappings().first()
    if row is None:
        raise RuntimeError("INSERT network_devices RETURNING produced no row")
    return row


async def delete_network_device(
    db: AsyncSession, *, tenant_id: str, device_id: UUID,
) -> bool:
    """Returns True if a row was deleted."""
    result = await db.execute(
        text(
            "DELETE FROM network_devices "
            "WHERE id = :id AND tenant_id = :tenant_id RETURNING id"
        ),
        {"id": str(device_id), "tenant_id": tenant_id},
    )
    return result.first() is not None


# ---------------------------------------------------------------------------
# Switch ports
# ---------------------------------------------------------------------------

async def list_ports_with_connected_device(
    db: AsyncSession, *, network_device_id: UUID,
) -> list[Mapping[str, Any]]:
    """LEFT JOIN devices to enrich each port with `connected_device` JSON."""
    result = await db.execute(
        text(
            "SELECT sp.*, "
            "json_build_object("
            "  'id', d.id, 'mac_address', d.mac_address, "
            "  'hostname', d.hostname, 'ip_address', d.ip_address"
            ") as connected_device "
            "FROM switch_ports sp "
            "LEFT JOIN devices d ON sp.connected_device_id = d.id "
            "WHERE sp.network_device_id = :device_id "
            "ORDER BY sp.port_index, sp.port_name"
        ),
        {"device_id": str(network_device_id)},
    )
    return list(result.mappings().all())


async def lookup_port_for_vlan_set(
    db: AsyncSession,
    *,
    tenant_id: str,
    network_device_id: UUID,
    port_id: UUID,
) -> Optional[Mapping[str, Any]]:
    """JOIN port + parent device. Returns ip/vendor/port_name/port_index."""
    result = await db.execute(
        text(
            "SELECT nd.ip_address, nd.vendor, sp.port_name, sp.port_index "
            "FROM network_devices nd "
            "JOIN switch_ports sp ON sp.network_device_id = nd.id "
            "WHERE nd.id = :device_id AND sp.id = :port_id "
            "AND nd.tenant_id = :tenant_id"
        ),
        {
            "device_id": str(network_device_id),
            "port_id": str(port_id),
            "tenant_id": tenant_id,
        },
    )
    return result.mappings().first()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _filter_clause(
    tenant_id: str,
    device_type: Optional[str],
    vendor: Optional[str],
) -> tuple[str, dict]:
    conditions = ["tenant_id = :tenant_id"]
    params: dict = {"tenant_id": tenant_id}
    if device_type:
        conditions.append("device_type = :device_type")
        params["device_type"] = device_type
    if vendor:
        conditions.append("vendor ILIKE :vendor")
        params["vendor"] = f"%{vendor}%"
    return " AND ".join(conditions), params
