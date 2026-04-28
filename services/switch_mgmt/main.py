"""OpenRadiusWeb Switch Management Service - SNMP/SSH switch control."""

import asyncio
import signal

from orw_common.logging import setup_logging
from orw_common import nats_client

from snmp_manager import SNMPManager
from ssh_manager import SSHManager

log = setup_logging("switch_mgmt")


async def main():
    await nats_client.connect()
    await nats_client.ensure_stream("orw", ["orw.>"])

    snmp_mgr = SNMPManager()
    ssh_mgr = SSHManager()

    # Subscribe to switch management commands
    await nats_client.subscribe(
        "orw.switch.set_vlan",
        snmp_mgr.handle_set_vlan,
        queue="switch-workers",
        durable="switch-vlan",
    )

    await nats_client.subscribe(
        "orw.switch.poll_requested",
        snmp_mgr.handle_poll_request,
        queue="switch-workers",
        durable="switch-poll",
    )

    await nats_client.subscribe(
        "orw.switch.bounce_port",
        ssh_mgr.handle_bounce_port,
        queue="switch-workers",
        durable="switch-bounce",
    )

    log.info("switch_mgmt_service_ready")

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
    log.info("switch_mgmt_service_stopped")


if __name__ == "__main__":
    asyncio.run(main())
