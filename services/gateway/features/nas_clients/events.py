"""NATS publishers for the nas_clients feature (Layer 2).

Each function corresponds to one subject documented in
[development-manual §8.2.6](../../../../../docs/development-manual.md#826-event-catalog-current).
Subscribers (e.g. `freeradius_config_watcher`) react to these
events; this feature does not know they exist.

This is the canonical shape for a feature's `events.py` slot —
publisher atoms only, no business logic, no DB. The schema stays
flat so `serialize_event` (in shared/orw_common/nats_client) can
just pass it through.
"""
from typing import Optional

from orw_common import nats_client


SUBJECT_FREERADIUS_APPLY = "orw.config.freeradius.apply"


async def publish_freeradius_apply(
    *,
    triggered_by: Optional[str],
    action: str,
) -> None:
    """Ask freeradius_config_watcher to regenerate clients.conf and reload.

    Args:
        triggered_by: Username or sub of the actor (for audit trace in subscriber)
        action: Short verb describing why (e.g. "reload_nas_clients")
    """
    await nats_client.publish(
        SUBJECT_FREERADIUS_APPLY,
        {"triggered_by": triggered_by, "action": action},
    )
