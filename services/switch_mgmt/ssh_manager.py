"""SSH-based switch management using Netmiko."""

import asyncio
from typing import Any

from orw_common.logging import get_logger
from orw_common import nats_client
from orw_common.database import get_db_context

log = get_logger("ssh_manager")

# Netmiko device_type mapping
VENDOR_DEVICE_TYPE = {
    "cisco": "cisco_ios",
    "cisco_xe": "cisco_xe",
    "cisco_nxos": "cisco_nxos",
    "aruba": "aruba_osswitch",
    "aruba_cx": "aruba_oscx",
    "juniper": "juniper_junos",
    "fortinet": "fortinet",
    "hp_procurve": "hp_procurve",
    "dell": "dell_force10",
    "extreme": "extreme_exos",
}


class SSHManager:
    """Manage switches via SSH using Netmiko."""

    async def handle_bounce_port(self, data: dict[str, Any]):
        """Bounce (shut/no shut) a switch port."""
        ip = data["ip_address"]
        port_name = data["port_name"]
        vendor = data.get("vendor", "cisco").lower()

        log.info("bounce_port_request", switch=ip, port=port_name)

        try:
            creds = await self._get_ssh_credentials(data["network_device_id"])
            device_type = VENDOR_DEVICE_TYPE.get(vendor, "cisco_ios")

            commands = self._get_bounce_commands(vendor, port_name)

            result = await self._execute_ssh(
                ip, device_type,
                creds["username"], creds["password"],
                commands,
            )

            await nats_client.publish("orw.switch.port_bounced", {
                "network_device_id": data["network_device_id"],
                "port_name": port_name,
                "status": "success",
                "output": result,
            })

            log.info("port_bounced", switch=ip, port=port_name)

        except Exception as e:
            log.error("bounce_port_failed", switch=ip, port=port_name, error=str(e))

    async def set_vlan_ssh(
        self, ip: str, vendor: str, port_name: str,
        vlan_id: int, credentials: dict
    ) -> str:
        """Set VLAN on a port via SSH CLI commands."""
        device_type = VENDOR_DEVICE_TYPE.get(vendor.lower(), "cisco_ios")

        commands = self._get_vlan_commands(vendor.lower(), port_name, vlan_id)

        return await self._execute_ssh(
            ip, device_type,
            credentials["username"], credentials["password"],
            commands,
        )

    async def get_mac_table_ssh(
        self, ip: str, vendor: str, credentials: dict
    ) -> str:
        """Get MAC address table via SSH."""
        device_type = VENDOR_DEVICE_TYPE.get(vendor.lower(), "cisco_ios")
        command = self._get_mac_table_command(vendor.lower())

        return await self._execute_ssh(
            ip, device_type,
            credentials["username"], credentials["password"],
            [command],
        )

    async def _execute_ssh(
        self, ip: str, device_type: str,
        username: str, password: str,
        commands: list[str],
    ) -> str:
        """Execute commands on a switch via Netmiko (in thread pool)."""
        from netmiko import ConnectHandler

        loop = asyncio.get_event_loop()

        def _run():
            device = {
                "device_type": device_type,
                "host": ip,
                "username": username,
                "password": password,
                "timeout": 30,
            }
            with ConnectHandler(**device) as conn:
                output = ""
                for cmd in commands:
                    if cmd.startswith("conf"):
                        # Config mode commands
                        output += conn.send_config_set(commands[commands.index(cmd):])
                        break
                    else:
                        output += conn.send_command(cmd) + "\n"
                return output

        return await loop.run_in_executor(None, _run)

    async def _get_ssh_credentials(self, device_id: str) -> dict:
        """Get SSH credentials from database/Vault."""
        from sqlalchemy import text
        async with get_db_context() as db:
            result = await db.execute(
                text(
                    "SELECT ssh_credential_ref FROM network_devices "
                    "WHERE id = :id"
                ),
                {"id": device_id},
            )
            row = result.first()
            # TODO: Look up in Vault using credential_ref
            if not row or not row[0]:
                raise ValueError(f"No SSH credentials configured for device {device_id}")
            return {"username": "", "password": ""}

    def _get_bounce_commands(self, vendor: str, port_name: str) -> list[str]:
        """Get vendor-specific port bounce commands."""
        if vendor in ("cisco", "cisco_xe", "cisco_nxos"):
            return [
                "configure terminal",
                f"interface {port_name}",
                "shutdown",
                "no shutdown",
                "end",
            ]
        elif vendor in ("aruba", "aruba_cx"):
            return [
                "configure terminal",
                f"interface {port_name}",
                "shutdown",
                "no shutdown",
                "end",
            ]
        elif vendor == "juniper":
            return [
                "configure",
                f"set interfaces {port_name} disable",
                "commit",
                f"delete interfaces {port_name} disable",
                "commit",
            ]
        else:
            return [
                "configure terminal",
                f"interface {port_name}",
                "shutdown",
                "no shutdown",
                "end",
            ]

    def _get_vlan_commands(
        self, vendor: str, port_name: str, vlan_id: int
    ) -> list[str]:
        """Get vendor-specific VLAN assignment commands."""
        if vendor in ("cisco", "cisco_xe"):
            return [
                "configure terminal",
                f"interface {port_name}",
                f"switchport access vlan {vlan_id}",
                "end",
            ]
        elif vendor == "cisco_nxos":
            return [
                "configure terminal",
                f"interface {port_name}",
                f"switchport access vlan {vlan_id}",
                "end",
                "copy running-config startup-config",
            ]
        elif vendor in ("aruba", "aruba_cx"):
            return [
                "configure terminal",
                f"interface {port_name}",
                f"vlan access {vlan_id}",
                "end",
            ]
        elif vendor == "juniper":
            return [
                "configure",
                f"set interfaces {port_name} unit 0 family ethernet-switching vlan members vlan{vlan_id}",
                "commit",
            ]
        else:
            return [
                "configure terminal",
                f"interface {port_name}",
                f"switchport access vlan {vlan_id}",
                "end",
            ]

    def _get_mac_table_command(self, vendor: str) -> str:
        """Get vendor-specific MAC table display command."""
        if vendor in ("cisco", "cisco_xe", "cisco_nxos"):
            return "show mac address-table"
        elif vendor in ("aruba", "aruba_cx"):
            return "show mac-address-table"
        elif vendor == "juniper":
            return "show ethernet-switching table"
        elif vendor == "hp_procurve":
            return "show mac-address"
        else:
            return "show mac address-table"
