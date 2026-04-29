"""NATS publishers for the policies feature (Layer 2).

Three subjects, all consumed by the policy_engine service. This feature
does not know which subscribers exist — it just publishes the fact.
"""
from orw_common import nats_client


SUBJECT_POLICY_CREATED = "orw.policy.created"
SUBJECT_POLICY_UPDATED = "orw.policy.updated"
SUBJECT_POLICY_DELETED = "orw.policy.deleted"


async def publish_policy_created(*, policy_id: str, name: str) -> None:
    await nats_client.publish(
        SUBJECT_POLICY_CREATED,
        {"policy_id": policy_id, "name": name},
    )


async def publish_policy_updated(*, policy_id: str) -> None:
    await nats_client.publish(
        SUBJECT_POLICY_UPDATED,
        {"policy_id": policy_id},
    )


async def publish_policy_deleted(*, policy_id: str) -> None:
    await nats_client.publish(
        SUBJECT_POLICY_DELETED,
        {"policy_id": policy_id},
    )
