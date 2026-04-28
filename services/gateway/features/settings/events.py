"""NATS publishers for the settings feature."""
from typing import Optional

from orw_common import nats_client


# Allowed services for restart via NATS. Module-level so callers see what's allowed.
SERVICE_RESTART_TOPICS: dict[str, str] = {
    "freeradius": "orw.service.freeradius.restart",
    "discovery": "orw.service.discovery.restart",
    "device_inventory": "orw.service.device_inventory.restart",
    "policy_engine": "orw.service.policy_engine.restart",
    "switch_mgmt": "orw.service.switch_mgmt.restart",
    "coa": "orw.service.coa.restart",
}


async def publish_service_restart(
    *, service_name: str, requested_by: Optional[str],
) -> None:
    """Ask the named service to restart itself via NATS.

    Caller is responsible for verifying `service_name` is in
    SERVICE_RESTART_TOPICS before invoking.
    """
    topic = SERVICE_RESTART_TOPICS[service_name]
    await nats_client.publish(
        topic,
        {"action": "restart", "requested_by": requested_by},
    )
