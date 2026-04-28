"""NATS publishers for the ldap_servers feature.

Every mutation publishes `orw.config.freeradius.apply` so
freeradius_config_watcher regenerates radius config and reloads.
The `reason` field tells subscribers what happened (created/updated/deleted).
"""
from typing import Optional
from uuid import UUID

from orw_common import nats_client


SUBJECT_FREERADIUS_APPLY = "orw.config.freeradius.apply"


async def publish_freeradius_apply(
    *, reason: str, ldap_server_id: Optional[UUID] = None,
) -> None:
    """Ask freeradius_config_watcher to regenerate config + reload."""
    payload: dict = {"reason": reason}
    if ldap_server_id is not None:
        payload["ldap_server_id"] = str(ldap_server_id)
    await nats_client.publish(SUBJECT_FREERADIUS_APPLY, payload)
