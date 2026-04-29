"""NATS publishers for the devices feature."""
from typing import Optional
from uuid import UUID

from orw_common import nats_client


SUBJECT_DEVICE_UPSERTED = "orw.device.upserted"


async def publish_device_upserted(
    *,
    device_id: UUID,
    mac_address: str,
    ip_address: Optional[str],
) -> None:
    """Fired after a successful upsert (create or update by MAC).

    Subscribers (policy_engine, event_service) decide whether to
    re-evaluate policies for the device.
    """
    await nats_client.publish(
        SUBJECT_DEVICE_UPSERTED,
        {
            "device_id": str(device_id),
            "mac_address": mac_address,
            "ip_address": ip_address,
        },
    )
