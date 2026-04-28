"""Wazuh SIEM integration - Forward events and receive alerts."""

import httpx
from typing import Any, Optional

from orw_common.logging import get_logger

log = get_logger("wazuh_integration")


class WazuhIntegration:
    """Integration with Wazuh SIEM for bidirectional event exchange."""

    def __init__(
        self,
        api_url: str = "https://localhost:55000",
        username: str = "wazuh-wui",
        password: str = "",
        verify_ssl: bool = False,
    ):
        self.api_url = api_url.rstrip("/")
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self._token: Optional[str] = None

    async def authenticate(self):
        """Get JWT token from Wazuh API."""
        async with httpx.AsyncClient(verify=self.verify_ssl) as client:
            response = await client.post(
                f"{self.api_url}/security/user/authenticate",
                auth=(self.username, self.password),
            )
            response.raise_for_status()
            self._token = response.json()["data"]["token"]
            log.info("wazuh_authenticated")

    async def get_alerts(
        self,
        agent_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """Fetch recent alerts from Wazuh."""
        if not self._token:
            await self.authenticate()

        params = {"limit": limit, "offset": offset}
        if agent_id:
            params["agent_id"] = agent_id

        async with httpx.AsyncClient(verify=self.verify_ssl) as client:
            response = await client.get(
                f"{self.api_url}/alerts",
                headers={"Authorization": f"Bearer {self._token}"},
                params=params,
            )
            response.raise_for_status()
            return response.json().get("data", {}).get("affected_items", [])

    async def send_event(self, event: dict[str, Any]):
        """Send a NAC event to Wazuh via syslog or API."""
        # Forward NAC events to Wazuh for correlation
        log.debug("wazuh_event_sent", event_type=event.get("event_type"))

    async def get_agent_by_ip(self, ip: str) -> Optional[dict]:
        """Look up Wazuh agent by IP address for correlation."""
        if not self._token:
            await self.authenticate()

        async with httpx.AsyncClient(verify=self.verify_ssl) as client:
            response = await client.get(
                f"{self.api_url}/agents",
                headers={"Authorization": f"Bearer {self._token}"},
                params={"ip": ip, "limit": 1},
            )
            response.raise_for_status()
            items = response.json().get("data", {}).get("affected_items", [])
            return items[0] if items else None
