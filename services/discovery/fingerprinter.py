"""Device fingerprinting - OUI lookup, DHCP fingerprinting, and classification."""

import os
from typing import Optional

from orw_common.logging import get_logger

log = get_logger("fingerprinter")


# Common device type patterns based on OUI + service combinations
DEVICE_PATTERNS = {
    "printer": {
        "ports": [9100, 515, 631],
        "services": ["ipp", "printer", "jetdirect"],
        "oui_vendors": ["HP", "Hewlett", "Canon", "Epson", "Brother", "Xerox", "Ricoh", "Lexmark"],
    },
    "ip_phone": {
        "ports": [5060, 5061],
        "services": ["sip"],
        "oui_vendors": ["Cisco", "Polycom", "Yealink", "Avaya", "Grandstream"],
    },
    "ip_camera": {
        "ports": [554, 8554],
        "services": ["rtsp"],
        "oui_vendors": ["Hikvision", "Dahua", "Axis", "Hanwha", "Bosch"],
    },
    "access_point": {
        "oui_vendors": ["Ubiquiti", "Ruckus", "Aruba", "Meraki"],
    },
    "iot": {
        "oui_vendors": ["Espressif", "Tuya", "Shenzhen", "Xiaomi"],
    },
}


class DeviceFingerprinter:
    """Classify and fingerprint discovered devices."""

    def __init__(self, oui_db_path: str | None = None):
        self._oui_db: dict[str, str] = {}
        self._dhcp_db: dict[str, dict] = {}
        if oui_db_path and os.path.exists(oui_db_path):
            self._load_oui_db(oui_db_path)

    def _load_oui_db(self, path: str):
        """Load IEEE OUI database from file."""
        try:
            with open(path) as f:
                for line in f:
                    if "(hex)" in line:
                        parts = line.split("(hex)")
                        if len(parts) == 2:
                            prefix = parts[0].strip().replace("-", ":").lower()
                            vendor = parts[1].strip()
                            self._oui_db[prefix] = vendor
            log.info("oui_db_loaded", entries=len(self._oui_db))
        except Exception as e:
            log.warning("oui_db_load_failed", error=str(e))

    def lookup_vendor(self, mac: str) -> Optional[str]:
        """Look up vendor from MAC address OUI prefix."""
        prefix = mac[:8].lower()
        return self._oui_db.get(prefix)

    def classify_device(
        self,
        mac: str,
        vendor: str | None = None,
        services: list[dict] | None = None,
        dhcp_options: dict | None = None,
    ) -> dict:
        """
        Classify a device based on available fingerprint data.
        Returns dict with device_type, os_family, confidence.
        """
        result = {
            "device_type": "unknown",
            "os_family": None,
            "confidence": 0.0,
        }

        if not vendor:
            vendor = self.lookup_vendor(mac) or ""

        open_ports = set()
        service_names = set()
        if services:
            for svc in services:
                open_ports.add(svc.get("port", 0))
                if svc.get("service"):
                    service_names.add(svc["service"].lower())

        # Try to match against known patterns
        best_match = None
        best_score = 0

        for dev_type, patterns in DEVICE_PATTERNS.items():
            score = 0

            # Check OUI vendor match
            if vendor:
                for oui_vendor in patterns.get("oui_vendors", []):
                    if oui_vendor.lower() in vendor.lower():
                        score += 3
                        break

            # Check port matches
            pattern_ports = set(patterns.get("ports", []))
            if pattern_ports and open_ports:
                port_overlap = len(pattern_ports & open_ports)
                score += port_overlap * 2

            # Check service matches
            pattern_services = set(patterns.get("services", []))
            if pattern_services and service_names:
                svc_overlap = len(pattern_services & service_names)
                score += svc_overlap * 2

            if score > best_score:
                best_score = score
                best_match = dev_type

        if best_match and best_score >= 2:
            result["device_type"] = best_match
            result["confidence"] = min(best_score / 10.0, 1.0)

        # Classify by OS if services give hints
        if "microsoft-ds" in service_names or 445 in open_ports:
            result["os_family"] = "windows"
        elif "ssh" in service_names and 22 in open_ports:
            if 548 in open_ports:  # AFP
                result["os_family"] = "macos"
            else:
                result["os_family"] = "linux"

        # Generic classification if still unknown
        if result["device_type"] == "unknown":
            if result["os_family"] in ("windows", "linux", "macos"):
                result["device_type"] = "workstation"
                result["confidence"] = 0.3

        return result

    def fingerprint_dhcp(self, dhcp_options: dict) -> dict:
        """
        Fingerprint device using DHCP option fingerprint.
        Based on option 55 (Parameter Request List) patterns.
        """
        vendor_class = dhcp_options.get("vendor_class", "")

        result = {"os_family": None, "device_type": None}

        # Common DHCP fingerprints
        if vendor_class:
            vc = vendor_class.lower()
            if "msft" in vc:
                result["os_family"] = "windows"
            elif "android" in vc:
                result["os_family"] = "android"
                result["device_type"] = "mobile"
            elif "dhcpcd" in vc:
                result["os_family"] = "linux"

        return result
