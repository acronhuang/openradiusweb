"""NATS publishers for the freeradius_config feature.

Same subject as the LDAP/realm/NAS-client mutators
(`orw.config.freeradius.apply`) — `freeradius_config_watcher` subscribes
once and acts on every variant via the `action`/`reason` field.
"""
from datetime import datetime, timezone
from typing import Optional

from orw_common import nats_client


SUBJECT_FREERADIUS_APPLY = "orw.config.freeradius.apply"


async def publish_freeradius_apply(
    *, tenant_id: Optional[str], requested_by: Optional[str],
) -> None:
    """Trigger a full FreeRADIUS config regeneration + reload."""
    await nats_client.publish(
        SUBJECT_FREERADIUS_APPLY,
        {
            "action": "apply",
            "tenant_id": tenant_id,
            "requested_by": requested_by,
            "requested_at": datetime.now(timezone.utc).isoformat(),
        },
    )
