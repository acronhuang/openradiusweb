"""Passive network monitor - ARP and DHCP packet sniffing for device discovery."""

import asyncio
from datetime import datetime, timezone
from typing import Optional

from scapy.all import (
    AsyncSniffer, ARP, DHCP, Ether, IP, UDP,
    conf as scapy_conf,
)

from orw_common.logging import get_logger
from orw_common import nats_client

log = get_logger("passive_monitor")

# Suppress Scapy warnings
scapy_conf.verb = 0


class PassiveMonitor:
    """Passively discovers devices by sniffing ARP and DHCP packets."""

    def __init__(self, interface: str = "eth0"):
        self.interface = interface
        self._sniffer: Optional[AsyncSniffer] = None
        self._running = False
        self._seen_macs: dict[str, datetime] = {}
        self._dedup_seconds = 60  # Don't re-report same MAC within 60s

    async def start(self):
        """Start passive packet capture."""
        self._running = True
        log.info("passive_monitor_starting", interface=self.interface)

        try:
            self._sniffer = AsyncSniffer(
                iface=self.interface,
                filter="arp or (udp and (port 67 or port 68))",
                prn=self._process_packet,
                store=False,
            )
            self._sniffer.start()

            # Keep running until stopped
            while self._running:
                await asyncio.sleep(1)

        except PermissionError:
            log.error("passive_monitor_permission_denied",
                      msg="Need NET_RAW capability or root privileges")
        except Exception as e:
            log.error("passive_monitor_error", error=str(e))

    def stop(self):
        """Stop packet capture."""
        self._running = False
        if self._sniffer:
            self._sniffer.stop()
            log.info("passive_monitor_stopped")

    def _process_packet(self, pkt):
        """Process captured packet (runs in sniffer thread)."""
        try:
            if ARP in pkt:
                self._handle_arp(pkt)
            elif DHCP in pkt:
                self._handle_dhcp(pkt)
        except Exception as e:
            log.debug("packet_processing_error", error=str(e))

    def _handle_arp(self, pkt):
        """Extract device info from ARP packet."""
        if pkt[ARP].op in (1, 2):  # ARP request or reply
            mac = pkt[ARP].hwsrc
            ip = pkt[ARP].psrc

            if not mac or mac == "00:00:00:00:00:00":
                return
            if not ip or ip == "0.0.0.0":
                return

            mac = mac.lower()
            if self._should_report(mac):
                asyncio.get_event_loop().call_soon_threadsafe(
                    asyncio.ensure_future,
                    self._publish_discovery(mac, ip, "arp")
                )

    def _handle_dhcp(self, pkt):
        """Extract device info from DHCP packet."""
        if not pkt.haslayer(DHCP):
            return

        mac = pkt[Ether].src.lower()
        ip = None
        hostname = None

        # Extract DHCP options
        for option in pkt[DHCP].options:
            if isinstance(option, tuple):
                if option[0] == "requested_addr":
                    ip = option[1]
                elif option[0] == "hostname":
                    hostname = option[1]
                    if isinstance(hostname, bytes):
                        hostname = hostname.decode("utf-8", errors="replace")

        # Use IP layer if no requested_addr
        if not ip and IP in pkt:
            ip = pkt[IP].src
            if ip == "0.0.0.0":
                ip = None

        if mac and mac != "00:00:00:00:00:00" and self._should_report(mac):
            asyncio.get_event_loop().call_soon_threadsafe(
                asyncio.ensure_future,
                self._publish_discovery(mac, ip, "dhcp", hostname=hostname)
            )

    def _should_report(self, mac: str) -> bool:
        """Deduplicate: only report if not seen recently."""
        now = datetime.now(timezone.utc)
        last_seen = self._seen_macs.get(mac)
        if last_seen and (now - last_seen).total_seconds() < self._dedup_seconds:
            return False
        self._seen_macs[mac] = now

        # Periodic cleanup of old entries
        if len(self._seen_macs) > 10000:
            cutoff = now.timestamp() - self._dedup_seconds * 2
            self._seen_macs = {
                k: v for k, v in self._seen_macs.items()
                if v.timestamp() > cutoff
            }

        return True

    async def _publish_discovery(
        self, mac: str, ip: str | None, source: str, hostname: str | None = None
    ):
        """Publish device discovery event to NATS."""
        vendor = self._lookup_oui(mac)

        event = {
            "mac_address": mac,
            "ip_address": ip,
            "hostname": hostname,
            "vendor": vendor,
            "source": source,
            "discovered_at": datetime.now(timezone.utc).isoformat(),
        }

        await nats_client.publish("orw.device.discovered", event)
        log.info("device_discovered", mac=mac, ip=ip, source=source, vendor=vendor)

    def _lookup_oui(self, mac: str) -> str | None:
        """Look up vendor from MAC OUI prefix."""
        # Common OUI prefixes (extend with full OUI database in production)
        oui_db = {
            "00:50:56": "VMware",
            "00:0c:29": "VMware",
            "00:15:5d": "Microsoft Hyper-V",
            "08:00:27": "Oracle VirtualBox",
            "52:54:00": "QEMU/KVM",
            "dc:a6:32": "Raspberry Pi",
            "b8:27:eb": "Raspberry Pi",
            "00:1a:2b": "Cisco",
            "00:1b:44": "Cisco",
            "00:50:f2": "Microsoft",
            "aa:bb:cc": "Private",
        }
        prefix = mac[:8].lower()
        return oui_db.get(prefix)
