"""NATS publishers for the radius_realms feature.

Every mutation publishes `orw.config.freeradius.apply` with a `reason`
field so freeradius_config_watcher knows what changed and can regenerate
the relevant config.
"""
from typing import Optional
from uuid import UUID

from orw_common import nats_client


SUBJECT_FREERADIUS_APPLY = "orw.config.freeradius.apply"


async def publish_freeradius_apply(
    *,
    reason: str,
    realm_id: Optional[UUID] = None,
    realm_name: Optional[str] = None,
) -> None:
    payload: dict = {"reason": reason}
    if realm_id is not None:
        payload["realm_id"] = str(realm_id)
    if realm_name is not None:
        payload["realm_name"] = realm_name
    await nats_client.publish(SUBJECT_FREERADIUS_APPLY, payload)
