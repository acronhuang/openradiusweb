"""Active network scanner - ARP scan, Nmap fingerprinting, SNMP MAC table polling."""

import asyncio
import ipaddress
from datetime import datetime, timezone
from typing import Any

from scapy.all import ARP, Ether, srp, conf as scapy_conf

from orw_common.logging import get_logger
from orw_common import nats_client

log = get_logger("active_scanner")
scapy_conf.verb = 0


class ActiveScanner:
    """Active device discovery via ARP sweep, Nmap, and SNMP."""

    async def handle_scan_request(self, data: dict[str, Any]):
        """Handle scan request from NATS message."""
        scan_type = data.get("type", "arp")
        target = data.get("target", "")

        log.info("scan_request_received", type=scan_type, target=target)

        if scan_type == "arp":
            await self.arp_scan(target)
        elif scan_type == "nmap":
            await self.nmap_scan(target, ports=data.get("ports", "22,80,443"))
        elif scan_type == "snmp_mac_table":
            await self.snmp_mac_table(
                target,
                community=data.get("community", "public"),
                version=data.get("snmp_version", "v2c"),
            )
        else:
            log.warning("unknown_scan_type", type=scan_type)

    async def arp_scan(self, target: str, timeout: int = 5):
        """
        ARP scan a subnet to discover active hosts.
        target: CIDR notation (e.g., "192.168.1.0/24")
        """
        log.info("arp_scan_starting", target=target)

        try:
            network = ipaddress.ip_network(target, strict=False)
        except ValueError:
            log.error("invalid_target", target=target)
            return

        # Build ARP request packets
        arp_request = Ether(dst="ff:ff:ff:ff:ff:ff") / ARP(pdst=str(network))

        # Send and receive in thread pool to not block event loop
        loop = asyncio.get_event_loop()
        answered, _ = await loop.run_in_executor(
            None, lambda: srp(arp_request, timeout=timeout, verbose=False)
        )

        discovered = 0
        for sent, received in answered:
            mac = received[Ether].src.lower()
            ip = received[ARP].psrc

            await nats_client.publish("orw.device.discovered", {
                "mac_address": mac,
                "ip_address": ip,
                "source": "arp_scan",
                "discovered_at": datetime.now(timezone.utc).isoformat(),
            })
            discovered += 1

        log.info("arp_scan_completed", target=target, discovered=discovered)

        await nats_client.publish("orw.discovery.scan_completed", {
            "type": "arp",
            "target": target,
            "discovered": discovered,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })

    async def nmap_scan(
        self, target: str, ports: str = "22,80,443,445,3389", arguments: str = "-sV -O"
    ):
        """
        Nmap scan for OS fingerprinting and service detection.
        Uses subprocess to call nmap with XML output.
        """
        log.info("nmap_scan_starting", target=target, ports=ports)

        cmd = [
            "nmap", "-oX", "-",  # XML output to stdout
            "-p", ports,
            "-sV",  # Service version detection
            "-O",   # OS detection
            "--host-timeout", "30s",
            target,
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=120
            )

            if proc.returncode != 0:
                log.warning("nmap_scan_error",
                            target=target, stderr=stderr.decode())
                return

            # Parse XML output
            results = self._parse_nmap_xml(stdout.decode())

            for host in results:
                await nats_client.publish("orw.device.discovered", {
                    "mac_address": host.get("mac"),
                    "ip_address": host.get("ip"),
                    "hostname": host.get("hostname"),
                    "os_family": host.get("os_family"),
                    "os_version": host.get("os_match"),
                    "vendor": host.get("vendor"),
                    "source": "nmap",
                    "services": host.get("services", []),
                    "discovered_at": datetime.now(timezone.utc).isoformat(),
                })

            log.info("nmap_scan_completed",
                     target=target, discovered=len(results))

        except asyncio.TimeoutError:
            log.error("nmap_scan_timeout", target=target)
        except FileNotFoundError:
            log.error("nmap_not_installed")

    async def snmp_mac_table(
        self, switch_ip: str, community: str = "public", version: str = "v2c"
    ):
        """
        Poll switch MAC address table via SNMP.
        Reads dot1dTpFdbTable (1.3.6.1.2.1.17.4.3.1) for learned MACs.
        """
        log.info("snmp_mac_table_starting", switch=switch_ip)

        try:
            from pysnmp.hlapi.v3arch.asyncio import (
                bulk_cmd, SnmpEngine, CommunityData,
                UdpTransportTarget, ContextData, ObjectType, ObjectIdentity,
            )

            # dot1dTpFdbAddress - MAC address table
            mac_table_oid = "1.3.6.1.2.1.17.4.3.1.1"

            engine = SnmpEngine()
            discovered = 0

            # Walk MAC table
            async for error_indication, error_status, error_index, var_binds in bulk_cmd(
                engine,
                CommunityData(community),
                await UdpTransportTarget.create((switch_ip, 161)),
                ContextData(),
                0, 50,  # Non-repeaters, max-repetitions
                ObjectType(ObjectIdentity(mac_table_oid)),
                lexicographicMode=False,
            ):
                if error_indication or error_status:
                    log.warning("snmp_error",
                                switch=switch_ip, error=str(error_indication or error_status))
                    break

                for oid, val in var_binds:
                    oid_str = str(oid)
                    if not oid_str.startswith(mac_table_oid):
                        break
                    # Convert SNMP octet string to MAC address
                    mac_bytes = bytes(val)
                    if len(mac_bytes) == 6:
                        mac = ":".join(f"{b:02x}" for b in mac_bytes)
                        await nats_client.publish("orw.device.discovered", {
                            "mac_address": mac,
                            "source": "snmp_mac_table",
                            "switch_ip": switch_ip,
                            "discovered_at": datetime.now(timezone.utc).isoformat(),
                        })
                        discovered += 1

            log.info("snmp_mac_table_completed",
                     switch=switch_ip, discovered=discovered)

        except ImportError:
            log.error("pysnmp_not_installed")
        except Exception as e:
            log.error("snmp_mac_table_error", switch=switch_ip, error=str(e))

    def _parse_nmap_xml(self, xml_str: str) -> list[dict]:
        """Parse nmap XML output to extract host information."""
        import xml.etree.ElementTree as ET

        results = []
        try:
            root = ET.fromstring(xml_str)
            for host in root.findall("host"):
                info: dict[str, Any] = {}

                # Status
                status = host.find("status")
                if status is not None and status.get("state") != "up":
                    continue

                # IP address
                for addr in host.findall("address"):
                    if addr.get("addrtype") == "ipv4":
                        info["ip"] = addr.get("addr")
                    elif addr.get("addrtype") == "mac":
                        info["mac"] = addr.get("addr", "").lower()
                        info["vendor"] = addr.get("vendor")

                # Hostname
                hostnames = host.find("hostnames")
                if hostnames is not None:
                    hostname_elem = hostnames.find("hostname")
                    if hostname_elem is not None:
                        info["hostname"] = hostname_elem.get("name")

                # OS detection
                os_elem = host.find("os")
                if os_elem is not None:
                    osmatch = os_elem.find("osmatch")
                    if osmatch is not None:
                        info["os_match"] = osmatch.get("name")
                        osclass = osmatch.find("osclass")
                        if osclass is not None:
                            info["os_family"] = osclass.get("osfamily")

                # Services
                ports_elem = host.find("ports")
                services = []
                if ports_elem is not None:
                    for port in ports_elem.findall("port"):
                        service = port.find("service")
                        if service is not None:
                            services.append({
                                "port": int(port.get("portid", 0)),
                                "protocol": port.get("protocol"),
                                "service": service.get("name"),
                                "version": service.get("version"),
                                "product": service.get("product"),
                            })
                info["services"] = services

                if info.get("ip") or info.get("mac"):
                    results.append(info)

        except ET.ParseError as e:
            log.error("nmap_xml_parse_error", error=str(e))

        return results
