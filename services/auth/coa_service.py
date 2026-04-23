"""OpenRadiusWeb CoA Service - Listens for CoA actions from policy engine and API."""

import asyncio
import signal

from orw_common.logging import setup_logging
from orw_common import nats_client

from coa_manager import handle_coa_action, handle_policy_vlan_assign

log = setup_logging("coa_service")


async def main():
    await nats_client.connect()
    await nats_client.ensure_stream("orw", ["orw.>"])

    # Subscribe to CoA action requests
    await nats_client.subscribe(
        "orw.policy.action.coa",
        handle_coa_action,
        queue="coa-workers",
        durable="coa-action",
    )

    # Subscribe to VLAN assignment (auto-CoA if device has active session)
    await nats_client.subscribe(
        "orw.policy.action.vlan_assign",
        handle_policy_vlan_assign,
        queue="coa-workers",
        durable="coa-vlan",
    )

    log.info("coa_service_ready")

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
    log.info("coa_service_stopped")


if __name__ == "__main__":
    asyncio.run(main())
