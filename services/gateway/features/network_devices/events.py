"""NATS publishers for the network_devices feature.

Two subjects, both consumed by `switch_mgmt`:
- `orw.switch.poll_requested` — fire after device created so SNMP/SSH
  polling can begin populating switch_ports immediately.
- `orw.switch.set_vlan` — request a VLAN change on a specific port
  (switch_mgmt translates to vendor-specific SNMP/SSH commands).
"""
from typing import Optional
from uuid import UUID

from orw_common import nats_client


SUBJECT_POLL_REQUESTED = "orw.switch.poll_requested"
SUBJECT_SET_VLAN = "orw.switch.set_vlan"


async def publish_poll_requested(
    *, network_device_id: UUID, ip_address: str,
) -> None:
    await nats_client.publish(
        SUBJECT_POLL_REQUESTED,
        {
            "network_device_id": str(network_device_id),
            "ip_address": ip_address,
        },
    )


async def publish_set_vlan(
    *,
    network_device_id: UUID,
    port_id: UUID,
    ip_address: str,
    vendor: Optional[str],
    port_name: Optional[str],
    port_index: Optional[int],
    vlan_id: int,
    requested_by: Optional[str],
) -> None:
    await nats_client.publish(
        SUBJECT_SET_VLAN,
        {
            "network_device_id": str(network_device_id),
            "port_id": str(port_id),
            "ip_address": ip_address,
            "vendor": vendor,
            "port_name": port_name,
            "port_index": port_index,
            "vlan_id": vlan_id,
            "requested_by": requested_by,
        },
    )
