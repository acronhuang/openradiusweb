"""Device enrichment - OUI vendor lookup, DNS resolution, NetBIOS name."""

import asyncio
import socket
from typing import Optional

from orw_common.logging import get_logger

log = get_logger("enrichment")


async def resolve_hostname(ip: str) -> Optional[str]:
    """Reverse DNS lookup for IP address."""
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None, lambda: socket.gethostbyaddr(ip)
        )
        return result[0]
    except (socket.herror, socket.gaierror, OSError):
        return None


async def resolve_dns(hostname: str) -> Optional[str]:
    """Forward DNS lookup for hostname."""
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None, lambda: socket.gethostbyname(hostname)
        )
        return result
    except (socket.herror, socket.gaierror, OSError):
        return None


async def enrich_device(device_data: dict) -> dict:
    """Enrich device data with additional lookups."""
    enriched = dict(device_data)

    ip = device_data.get("ip_address")
    if ip and not device_data.get("hostname"):
        hostname = await resolve_hostname(ip)
        if hostname:
            enriched["hostname"] = hostname

    return enriched
