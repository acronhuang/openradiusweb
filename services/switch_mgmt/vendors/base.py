"""Base vendor plugin interface for switch management."""

from abc import ABC, abstractmethod
from typing import Any


class SwitchVendorPlugin(ABC):
    """Abstract base class for vendor-specific switch plugins."""

    @property
    @abstractmethod
    def vendor_name(self) -> str:
        """Return the vendor name."""
        ...

    @property
    @abstractmethod
    def supported_models(self) -> list[str]:
        """Return list of supported model patterns."""
        ...

    @abstractmethod
    async def get_mac_table(self, connection: Any) -> list[dict]:
        """Get MAC address table from switch."""
        ...

    @abstractmethod
    async def get_interfaces(self, connection: Any) -> list[dict]:
        """Get interface status from switch."""
        ...

    @abstractmethod
    async def set_port_vlan(
        self, connection: Any, port: str, vlan_id: int
    ) -> bool:
        """Set VLAN on a port. Returns True on success."""
        ...

    @abstractmethod
    async def bounce_port(self, connection: Any, port: str) -> bool:
        """Bounce (shut/no shut) a port. Returns True on success."""
        ...

    async def get_port_status(self, connection: Any, port: str) -> dict:
        """Get status of a specific port."""
        interfaces = await self.get_interfaces(connection)
        for iface in interfaces:
            if iface.get("name") == port:
                return iface
        return {}

    async def get_vlan_table(self, connection: Any) -> list[dict]:
        """Get VLAN table from switch. Override if supported."""
        return []
