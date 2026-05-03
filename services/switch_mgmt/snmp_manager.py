"""SNMP-based switch management - VLAN control, MAC table polling, port status."""

from typing import Any

from orw_common.logging import get_logger
from orw_common import nats_client
from orw_common.database import get_db_context

log = get_logger("snmp_manager")

# Standard SNMP OIDs for switch management
OID = {
    "ifDescr": "1.3.6.1.2.1.2.2.1.2",         # Interface description
    "ifOperStatus": "1.3.6.1.2.1.2.2.1.8",     # Operational status
    "ifAdminStatus": "1.3.6.1.2.1.2.2.1.7",    # Admin status
    "ifSpeed": "1.3.6.1.2.1.2.2.1.5",          # Interface speed
    "dot1dTpFdbAddress": "1.3.6.1.2.1.17.4.3.1.1",  # MAC table
    "dot1dTpFdbPort": "1.3.6.1.2.1.17.4.3.1.2",     # MAC-to-port
    "vmVlan": "1.3.6.1.4.1.9.9.68.1.2.2.1.2",       # Cisco VLAN (vmVlan)
    "dot1qPvid": "1.3.6.1.2.1.17.7.1.4.5.1.1",      # 802.1Q PVID (generic)
    "sysDescr": "1.3.6.1.2.1.1.1.0",                 # System description
    "sysName": "1.3.6.1.2.1.1.5.0",                  # System name
}


class SNMPManager:
    """Manage switches via SNMP v2c/v3."""

    async def handle_set_vlan(self, data: dict[str, Any]):
        """Handle VLAN change request."""
        ip = data["ip_address"]
        port_index = data.get("port_index")
        vlan_id = data["vlan_id"]
        vendor = data.get("vendor", "").lower()

        log.info("set_vlan_request",
                 switch=ip, port=port_index, vlan=vlan_id, vendor=vendor)

        try:
            # Get SNMP community from database
            community = await self._get_community(data["network_device_id"])

            if "cisco" in vendor:
                await self._set_vlan_cisco(ip, community, port_index, vlan_id)
            else:
                await self._set_vlan_generic(ip, community, port_index, vlan_id)

            # Update database
            await self._update_port_vlan(data["port_id"], vlan_id)

            await nats_client.publish("orw.switch.vlan_changed", {
                "network_device_id": data["network_device_id"],
                "port_id": data["port_id"],
                "vlan_id": vlan_id,
                "status": "success",
            })

            log.info("vlan_changed", switch=ip, port=port_index, vlan=vlan_id)

        except Exception as e:
            log.error("set_vlan_failed", switch=ip, error=str(e))
            await nats_client.publish("orw.switch.vlan_change_failed", {
                "network_device_id": data["network_device_id"],
                "port_id": data["port_id"],
                "error": str(e),
            })

    async def handle_poll_request(self, data: dict[str, Any]):
        """Handle switch polling request - collect port/MAC/VLAN data."""
        device_id = data["network_device_id"]
        ip = data["ip_address"]

        log.info("poll_request", switch=ip)

        try:
            community = await self._get_community(device_id)

            # Poll system info
            sys_info = await self._get_system_info(ip, community)

            # Poll interfaces
            interfaces = await self._poll_interfaces(ip, community)

            # Poll MAC table
            mac_table = await self._poll_mac_table(ip, community)

            # Update database with polled data
            await self._save_poll_results(device_id, sys_info, interfaces, mac_table)

            await nats_client.publish("orw.switch.poll_completed", {
                "network_device_id": device_id,
                "interfaces": len(interfaces),
                "macs_learned": len(mac_table),
            })

            log.info("poll_completed", switch=ip,
                     interfaces=len(interfaces), macs=len(mac_table))

        except Exception as e:
            log.error("poll_failed", switch=ip, error=str(e))

    async def _get_community(self, device_id: str) -> str:
        """Get SNMP community string from database."""
        from sqlalchemy import text
        async with get_db_context() as db:
            result = await db.execute(
                text(
                    "SELECT snmp_community_encrypted FROM network_devices "
                    "WHERE id = :id"
                ),
                {"id": device_id},
            )
            row = result.first()
            if row and row[0]:
                # snmp_community_encrypted is AES-256-GCM ciphertext post
                # PR #74; decrypt_secret() falls back to passthrough on
                # legacy plaintext rows during the migration window.
                from orw_common.secrets import decrypt_secret
                return decrypt_secret(row[0]) or "public"
        return "public"  # Fallback for dev

    async def _set_vlan_cisco(
        self, ip: str, community: str, port_index: int, vlan_id: int
    ):
        """Set VLAN on a Cisco switch port using vmVlan OID."""
        from pysnmp.hlapi.v3arch.asyncio import (
            set_cmd, SnmpEngine, CommunityData,
            UdpTransportTarget, ContextData,
            ObjectType, ObjectIdentity, Integer32,
        )

        oid = f"{OID['vmVlan']}.{port_index}"

        error_indication, error_status, error_index, var_binds = await set_cmd(
            SnmpEngine(),
            CommunityData(community),
            await UdpTransportTarget.create((ip, 161)),
            ContextData(),
            ObjectType(ObjectIdentity(oid), Integer32(vlan_id)),
        )

        if error_indication:
            raise RuntimeError(f"SNMP error: {error_indication}")
        if error_status:
            raise RuntimeError(
                f"SNMP error: {error_status.prettyPrint()} at {error_index}"
            )

    async def _set_vlan_generic(
        self, ip: str, community: str, port_index: int, vlan_id: int
    ):
        """Set VLAN using standard 802.1Q PVID OID."""
        from pysnmp.hlapi.v3arch.asyncio import (
            set_cmd, SnmpEngine, CommunityData,
            UdpTransportTarget, ContextData,
            ObjectType, ObjectIdentity, Unsigned32,
        )

        oid = f"{OID['dot1qPvid']}.{port_index}"

        error_indication, error_status, error_index, var_binds = await set_cmd(
            SnmpEngine(),
            CommunityData(community),
            await UdpTransportTarget.create((ip, 161)),
            ContextData(),
            ObjectType(ObjectIdentity(oid), Unsigned32(vlan_id)),
        )

        if error_indication:
            raise RuntimeError(f"SNMP error: {error_indication}")
        if error_status:
            raise RuntimeError(
                f"SNMP error: {error_status.prettyPrint()} at {error_index}"
            )

    async def _get_system_info(self, ip: str, community: str) -> dict:
        """Get switch system description and name."""
        from pysnmp.hlapi.v3arch.asyncio import (
            get_cmd, SnmpEngine, CommunityData,
            UdpTransportTarget, ContextData,
            ObjectType, ObjectIdentity,
        )

        result = {}
        for name, oid in [("sysDescr", OID["sysDescr"]), ("sysName", OID["sysName"])]:
            error_indication, error_status, _, var_binds = await get_cmd(
                SnmpEngine(),
                CommunityData(community),
                await UdpTransportTarget.create((ip, 161)),
                ContextData(),
                ObjectType(ObjectIdentity(oid)),
            )
            if not error_indication and not error_status and var_binds:
                result[name] = str(var_binds[0][1])

        return result

    async def _poll_interfaces(self, ip: str, community: str) -> list[dict]:
        """Poll all interfaces from switch."""
        # Simplified - full implementation would use SNMP bulk walk
        return []

    async def _poll_mac_table(self, ip: str, community: str) -> list[dict]:
        """Poll MAC address table from switch."""
        # Simplified - full implementation would walk dot1dTpFdbTable
        return []

    async def _save_poll_results(
        self, device_id: str, sys_info: dict,
        interfaces: list, mac_table: list
    ):
        """Save polled data to database."""
        from sqlalchemy import text
        async with get_db_context() as db:
            # Update system info
            if sys_info:
                await db.execute(
                    text(
                        "UPDATE network_devices SET "
                        "hostname = COALESCE(:hostname, hostname), "
                        "os_version = COALESCE(:os_version, os_version), "
                        "last_polled = NOW() "
                        "WHERE id = :id"
                    ),
                    {
                        "id": device_id,
                        "hostname": sys_info.get("sysName"),
                        "os_version": sys_info.get("sysDescr", "")[:100],
                    },
                )

    async def _update_port_vlan(self, port_id: str, vlan_id: int):
        """Update port VLAN in database."""
        from sqlalchemy import text
        async with get_db_context() as db:
            await db.execute(
                text(
                    "UPDATE switch_ports SET current_vlan = :vlan, "
                    "assigned_vlan = :vlan, updated_at = NOW() "
                    "WHERE id = :id"
                ),
                {"id": port_id, "vlan": vlan_id},
            )
