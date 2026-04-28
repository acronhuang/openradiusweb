"""NATS publishers for the coa feature.

Single subject — `coa_service` subscribes and translates each message
into a real RFC 5176 CoA UDP packet to the relevant NAS.
"""
from typing import Any

from orw_common import nats_client


SUBJECT_COA_SEND = "orw.policy.action.coa"


async def publish_coa(payload: dict[str, Any]) -> None:
    """Publish a single CoA request.

    The payload shape is whatever the service layer assembled — typically
    one of `mac_address`/`username`/`session_id` plus `action`, optional
    `vlan_id`/`acl_name`, `requested_by`, and `reason`.
    """
    await nats_client.publish(SUBJECT_COA_SEND, payload)
