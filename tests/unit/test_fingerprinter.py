"""Unit tests for the device fingerprinter."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/discovery"))

from fingerprinter import DeviceFingerprinter


class TestDeviceFingerprinter:
    def setup_method(self):
        self.fp = DeviceFingerprinter()

    def test_classify_printer_by_services(self):
        result = self.fp.classify_device(
            mac="00:11:22:33:44:55",
            vendor="HP Inc",
            services=[
                {"port": 9100, "service": "jetdirect"},
                {"port": 631, "service": "ipp"},
            ],
        )
        assert result["device_type"] == "printer"
        assert result["confidence"] > 0

    def test_classify_ip_phone(self):
        result = self.fp.classify_device(
            mac="00:11:22:33:44:55",
            vendor="Polycom",
            services=[{"port": 5060, "service": "sip"}],
        )
        assert result["device_type"] == "ip_phone"

    def test_classify_camera(self):
        result = self.fp.classify_device(
            mac="00:11:22:33:44:55",
            vendor="Hikvision",
            services=[{"port": 554, "service": "rtsp"}],
        )
        assert result["device_type"] == "ip_camera"

    def test_classify_windows_workstation(self):
        result = self.fp.classify_device(
            mac="00:11:22:33:44:55",
            vendor="Dell",
            services=[
                {"port": 445, "service": "microsoft-ds"},
                {"port": 135, "service": "msrpc"},
            ],
        )
        assert result["os_family"] == "windows"

    def test_classify_linux_by_ssh(self):
        result = self.fp.classify_device(
            mac="00:11:22:33:44:55",
            vendor="Unknown",
            services=[{"port": 22, "service": "ssh"}],
        )
        assert result["os_family"] == "linux"

    def test_classify_macos_by_afp(self):
        result = self.fp.classify_device(
            mac="00:11:22:33:44:55",
            vendor="Apple",
            services=[
                {"port": 22, "service": "ssh"},
                {"port": 548, "service": "afp"},
            ],
        )
        assert result["os_family"] == "macos"

    def test_classify_unknown(self):
        result = self.fp.classify_device(
            mac="aa:bb:cc:dd:ee:ff",
            vendor=None,
            services=[],
        )
        assert result["device_type"] == "unknown"
        assert result["confidence"] == 0.0

    def test_dhcp_fingerprint_windows(self):
        result = self.fp.fingerprint_dhcp({
            "vendor_class": "MSFT 5.0",
            "param_req_list": [1, 3, 6, 15, 31, 33, 43, 44, 46, 47, 119, 121, 249, 252],
        })
        assert result["os_family"] == "windows"

    def test_dhcp_fingerprint_android(self):
        result = self.fp.fingerprint_dhcp({
            "vendor_class": "android-dhcp-12",
        })
        assert result["os_family"] == "android"
        assert result["device_type"] == "mobile"
