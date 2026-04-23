"""OpenRadiusWeb Policy Engine - Evaluates policies against devices and triggers actions."""

import asyncio
import signal
from typing import Any

from sqlalchemy import text

from orw_common.config import get_settings
from orw_common.logging import setup_logging
from orw_common import nats_client
from orw_common.database import get_db_context

from evaluator import PolicyEvaluator

log = setup_logging("policy_engine")
evaluator = PolicyEvaluator()


async def handle_evaluate_device(data: dict[str, Any]):
    """Evaluate all policies for a specific device."""
    device_id = data["device_id"]
    trigger = data.get("trigger", "manual")

    log.info("evaluating_device", device_id=device_id, trigger=trigger)

    async with get_db_context() as db:
        # Get device data
        result = await db.execute(
            text("SELECT * FROM devices WHERE id = :id"),
            {"id": device_id},
        )
        device = result.mappings().first()
        if not device:
            log.warning("device_not_found", device_id=device_id)
            return

        # Get device properties
        props_result = await db.execute(
            text(
                "SELECT category, key, value FROM device_properties "
                "WHERE device_id = :id"
            ),
            {"id": device_id},
        )
        properties = {}
        for row in props_result.mappings():
            cat = row["category"]
            if cat not in properties:
                properties[cat] = {}
            properties[cat][row["key"]] = row["value"]

        device_context = {
            **dict(device),
            "properties": properties,
        }

        # Get all enabled policies, ordered by priority
        policies_result = await db.execute(
            text(
                "SELECT * FROM policies "
                "WHERE enabled = true AND tenant_id = :tenant_id "
                "ORDER BY priority ASC"
            ),
            {"tenant_id": str(device["tenant_id"])},
        )
        policies = policies_result.mappings().all()

        # Evaluate each policy
        for policy in policies:
            matched = evaluator.evaluate(policy, device_context)
            actions = policy["match_actions"] if matched else policy["no_match_actions"]

            # Record evaluation
            await db.execute(
                text(
                    "INSERT INTO policy_evaluations "
                    "(policy_id, device_id, result, actions_taken) "
                    "VALUES (:policy_id, :device_id, :result, :actions::jsonb)"
                ),
                {
                    "policy_id": str(policy["id"]),
                    "device_id": device_id,
                    "result": "match" if matched else "no_match",
                    "actions": str(actions) if actions else "[]",
                },
            )

            # Execute actions
            if actions:
                for action in actions:
                    if isinstance(action, dict):
                        await execute_action(action, device_context)

    log.info("device_evaluated",
             device_id=device_id, policies_checked=len(policies))


async def execute_action(action: dict, device: dict):
    """Execute a policy action."""
    action_type = action.get("type", "")
    params = action.get("params", {})

    if action_type == "vlan_assign":
        # Find the switch port this device is on and change VLAN
        vlan_id = params.get("vlan") or params.get("vlan_id")
        if vlan_id:
            await nats_client.publish("orw.policy.action.vlan_assign", {
                "device_id": str(device.get("id")),
                "mac_address": str(device.get("mac_address")),
                "target_vlan": vlan_id,
            })
            log.info("action_vlan_assign",
                     device=str(device.get("mac_address")), vlan=vlan_id)

    elif action_type == "quarantine":
        await nats_client.publish("orw.policy.action.quarantine", {
            "device_id": str(device.get("id")),
            "mac_address": str(device.get("mac_address")),
            "reason": params.get("reason", "policy_violation"),
        })
        log.info("action_quarantine", device=str(device.get("mac_address")))

    elif action_type == "notify":
        await nats_client.publish("orw.policy.action.notify", {
            "device_id": str(device.get("id")),
            "template": params.get("template", "default"),
            "recipients": params.get("recipients", []),
        })

    elif action_type == "acl_apply":
        await nats_client.publish("orw.policy.action.acl_apply", {
            "device_id": str(device.get("id")),
            "acl_name": params.get("acl"),
        })

    elif action_type == "coa":
        coa_action = params.get("action", "reauthenticate")
        await nats_client.publish("orw.policy.action.coa", {
            "mac_address": str(device.get("mac_address")),
            "action": coa_action,
            "vlan_id": params.get("vlan_id"),
            "acl_name": params.get("acl_name"),
        })
        log.info("action_coa",
                 device=str(device.get("mac_address")), coa_action=coa_action)

    elif action_type == "reject":
        await nats_client.publish("orw.policy.action.reject", {
            "device_id": str(device.get("id")),
            "reason": params.get("reason"),
        })

    elif action_type == "bounce_port":
        await nats_client.publish("orw.switch.bounce_port", {
            "device_id": str(device.get("id")),
            "mac_address": str(device.get("mac_address")),
        })

    elif action_type == "create_incident":
        await nats_client.publish("orw.policy.action.create_incident", {
            "device_id": str(device.get("id")),
            "title": params.get("title"),
            "severity": params.get("severity", "medium"),
            "integration": params.get("integration", "thehive"),
        })

    elif action_type == "tag_device":
        await nats_client.publish("orw.policy.action.tag_device", {
            "device_id": str(device.get("id")),
            "tag": params.get("tag"),
        })

    elif action_type in ("log", "captive_portal", "qos_apply"):
        # Forward to appropriate handler
        await nats_client.publish(f"orw.policy.action.{action_type}", {
            "device_id": str(device.get("id")),
            **params,
        })

    else:
        log.warning("unknown_action_type", type=action_type)


async def main():
    await nats_client.connect()
    await nats_client.ensure_stream("orw", ["orw.>"])

    await nats_client.subscribe(
        "orw.policy.evaluate_device",
        handle_evaluate_device,
        queue="policy-workers",
        durable="policy-eval",
    )

    log.info("policy_engine_service_ready")

    stop_event = asyncio.Event()
    def _signal_handler():
        stop_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass

    await stop_event.wait()
    await nats_client.close()
    log.info("policy_engine_service_stopped")


if __name__ == "__main__":
    asyncio.run(main())
