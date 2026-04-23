"""OpenRadiusWeb Event Service - Centralized event logging and external integrations."""

import asyncio
import signal
from typing import Any

from sqlalchemy import text

from orw_common.config import get_settings
from orw_common.logging import setup_logging
from orw_common import nats_client
from orw_common.database import get_db_context

log = setup_logging("event_service")


async def handle_event(data: dict[str, Any]):
    """Store events from all services into the events table."""
    async with get_db_context() as db:
        await db.execute(
            text(
                "INSERT INTO events (event_type, severity, device_id, source, message, details) "
                "VALUES (:type, :severity, :device_id, :source, :message, :details::jsonb)"
            ),
            {
                "type": data.get("event_type", "unknown"),
                "severity": data.get("severity", "info"),
                "device_id": data.get("device_id"),
                "source": data.get("source", "unknown"),
                "message": data.get("message", ""),
                "details": str(data.get("details", "{}")),
            },
        )


async def handle_policy_action(data: dict[str, Any]):
    """Log policy action events."""
    action_type = data.get("type", "unknown")
    device_id = data.get("device_id")

    async with get_db_context() as db:
        await db.execute(
            text(
                "INSERT INTO events (event_type, severity, device_id, source, message, details) "
                "VALUES ('policy_action', 'medium', :device_id, 'policy_engine', :message, :details::jsonb)"
            ),
            {
                "device_id": device_id,
                "message": f"Policy action executed: {action_type}",
                "details": str(data),
            },
        )

    log.info("policy_action_logged", action=action_type, device=device_id)


async def main():
    await nats_client.connect()
    await nats_client.ensure_stream("orw", ["orw.>"])

    # Subscribe to all events
    await nats_client.subscribe(
        "orw.event.>",
        handle_event,
        queue="event-workers",
        durable="events",
    )

    # Subscribe to policy actions for audit logging
    await nats_client.subscribe(
        "orw.policy.action.>",
        handle_policy_action,
        queue="event-workers",
        durable="events-policy",
    )

    log.info("event_service_ready")

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
    log.info("event_service_stopped")


if __name__ == "__main__":
    asyncio.run(main())
