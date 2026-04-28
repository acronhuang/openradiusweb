"""OpenRadiusWeb Device Inventory Service - Manages device records and properties."""

import asyncio
import signal

from sqlalchemy import text

from orw_common.logging import setup_logging
from orw_common import nats_client
from orw_common.database import get_db_context

log = setup_logging("device_inventory")


async def handle_device_discovered(data: dict):
    """
    Handle device discovery events from Discovery Service.
    Upsert device into database and enrich with available data.
    """
    mac = data.get("mac_address")
    if not mac:
        return

    mac = mac.lower()
    ip = data.get("ip_address")
    hostname = data.get("hostname")
    vendor = data.get("vendor")
    source = data.get("source", "unknown")
    os_family = data.get("os_family")
    os_version = data.get("os_version")
    device_type = data.get("device_type")

    async with get_db_context() as db:
        # Upsert device
        result = await db.execute(
            text(
                "INSERT INTO devices (mac_address, ip_address, hostname, vendor, "
                "os_family, os_version, device_type, tenant_id) "
                "VALUES (:mac, :ip, :hostname, :vendor, :os_family, :os_version, "
                ":device_type, (SELECT id FROM tenants WHERE name = 'default')) "
                "ON CONFLICT (mac_address, tenant_id) DO UPDATE SET "
                "ip_address = COALESCE(EXCLUDED.ip_address, devices.ip_address), "
                "hostname = COALESCE(EXCLUDED.hostname, devices.hostname), "
                "vendor = COALESCE(EXCLUDED.vendor, devices.vendor), "
                "os_family = COALESCE(EXCLUDED.os_family, devices.os_family), "
                "os_version = COALESCE(EXCLUDED.os_version, devices.os_version), "
                "device_type = COALESCE(EXCLUDED.device_type, devices.device_type), "
                "last_seen = NOW() "
                "RETURNING id"
            ),
            {
                "mac": mac,
                "ip": ip,
                "hostname": hostname,
                "vendor": vendor,
                "os_family": os_family,
                "os_version": os_version,
                "device_type": device_type,
            },
        )
        device_id = str(result.scalar())

        # Store discovery source as property
        await db.execute(
            text(
                "INSERT INTO device_properties (device_id, category, key, value, source) "
                "VALUES (:device_id, 'discovery', 'last_source', :source, :source) "
                "ON CONFLICT (device_id, category, key) DO UPDATE SET "
                "value = EXCLUDED.value, updated_at = NOW()"
            ),
            {"device_id": device_id, "source": source},
        )

        # Store services if provided
        services = data.get("services", [])
        for svc in services:
            await db.execute(
                text(
                    "INSERT INTO device_properties (device_id, category, key, value, source) "
                    "VALUES (:device_id, 'services', :key, :value, 'nmap') "
                    "ON CONFLICT (device_id, category, key) DO UPDATE SET "
                    "value = EXCLUDED.value, updated_at = NOW()"
                ),
                {
                    "device_id": device_id,
                    "key": f"port_{svc.get('port', 0)}",
                    "value": f"{svc.get('service', '')} {svc.get('version', '')}".strip(),
                },
            )

        # Log event
        await db.execute(
            text(
                "INSERT INTO events (event_type, severity, device_id, source, message, details) "
                "VALUES ('device_discovered', 'info', :device_id, :source, :message, :details::jsonb)"
            ),
            {
                "device_id": device_id,
                "source": f"discovery.{source}",
                "message": f"Device {mac} discovered via {source}" + (f" at {ip}" if ip else ""),
                "details": f'{{"mac": "{mac}", "ip": "{ip}", "source": "{source}"}}',
            },
        )

    log.info("device_upserted", mac=mac, ip=ip, source=source, device_id=device_id)

    # Trigger policy evaluation for this device
    await nats_client.publish("orw.policy.evaluate_device", {
        "device_id": device_id,
        "mac_address": mac,
        "ip_address": ip,
        "trigger": "discovery",
    })


async def main():
    await nats_client.connect()
    await nats_client.ensure_stream("orw", ["orw.>"])

    # Subscribe to device discovery events
    await nats_client.subscribe(
        "orw.device.discovered",
        handle_device_discovered,
        queue="inventory-workers",
        durable="inventory",
    )

    log.info("device_inventory_service_ready")

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
    log.info("device_inventory_service_stopped")


if __name__ == "__main__":
    asyncio.run(main())
