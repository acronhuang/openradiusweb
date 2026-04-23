"""NATS JetStream client wrapper for inter-service communication."""

import json
import asyncio
from typing import Any, Callable, Awaitable

import nats
from nats.aio.client import Client as NATSClient
from nats.js import JetStreamContext

from .config import get_settings
from .logging import get_logger

log = get_logger("nats")

_nc: NATSClient | None = None
_js: JetStreamContext | None = None


async def connect() -> tuple[NATSClient, JetStreamContext]:
    """Connect to NATS and get JetStream context."""
    global _nc, _js
    if _nc is None or _nc.is_closed:
        settings = get_settings()
        _nc = await nats.connect(settings.nats_url)
        _js = _nc.jetstream()
        log.info("nats_connected", url=settings.nats_url)
    return _nc, _js


async def publish(subject: str, data: dict[str, Any]):
    """Publish a message to a NATS subject."""
    _, js = await connect()
    payload = json.dumps(data, default=str).encode()
    await js.publish(subject, payload)
    log.debug("nats_published", subject=subject)


async def subscribe(
    subject: str,
    handler: Callable[[dict[str, Any]], Awaitable[None]],
    queue: str | None = None,
    durable: str | None = None,
):
    """Subscribe to a NATS subject with a message handler."""
    _, js = await connect()

    async def _wrapper(msg):
        try:
            data = json.loads(msg.data.decode())
            await handler(data)
            await msg.ack()
        except Exception as e:
            log.error("nats_handler_error", subject=subject, error=str(e))
            await msg.nak()

    # Delete any stale consumer first to avoid config conflicts
    if durable:
        try:
            stream_name = await js.find_stream_name_by_subject(subject)
            await js.delete_consumer(stream_name, durable)
        except Exception:
            pass
        await asyncio.sleep(0.3)

    # Subscribe without queue group (single instance per service)
    sub = await js.subscribe(subject, cb=_wrapper, durable=durable)
    log.info("nats_subscribed", subject=subject, durable=durable)
    return sub


async def ensure_stream(name: str, subjects: list[str]):
    """Ensure a JetStream stream exists."""
    _, js = await connect()
    try:
        await js.find_stream_name_by_subject(subjects[0])
    except Exception:
        await js.add_stream(name=name, subjects=subjects)
        log.info("nats_stream_created", name=name, subjects=subjects)


async def close():
    """Close NATS connection."""
    global _nc, _js
    if _nc and not _nc.is_closed:
        await _nc.close()
        _nc = None
        _js = None
        log.info("nats_disconnected")
