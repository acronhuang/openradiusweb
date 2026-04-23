"""OpenRadiusWeb Discovery Service - Device discovery via ARP, DHCP, SNMP, Nmap."""

import asyncio
import signal
from orw_common.config import get_settings
from orw_common.logging import setup_logging
from orw_common import nats_client

from passive_monitor import PassiveMonitor
from active_scanner import ActiveScanner

log = setup_logging("discovery")
running = True


async def main():
    global running
    settings = get_settings()

    # Connect to NATS
    await nats_client.connect()
    await nats_client.ensure_stream("orw", ["orw.>"])

    # Subscribe to scan requests
    scanner = ActiveScanner()
    await nats_client.subscribe(
        "orw.discovery.scan_request",
        scanner.handle_scan_request,
        queue="discovery-workers",
        durable="discovery",
    )

    # Start passive ARP/DHCP monitor
    interface = settings.__dict__.get("scan_interface", "eth0")
    monitor = PassiveMonitor(interface=interface)
    monitor_task = asyncio.create_task(monitor.start())

    log.info("discovery_service_ready", interface=interface)

    # Wait for shutdown
    stop_event = asyncio.Event()

    def _signal_handler():
        stop_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass  # Windows

    await stop_event.wait()

    # Cleanup
    monitor.stop()
    await monitor_task
    await nats_client.close()
    log.info("discovery_service_stopped")


if __name__ == "__main__":
    asyncio.run(main())
