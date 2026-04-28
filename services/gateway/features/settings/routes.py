"""HTTP routes for the settings feature (Layer 3).

Two route-level concerns kept out of service.py:
- **Health probes** (`/service-status`) — pure outbound socket I/O against
  external services; no domain logic. Lives here.
- **Gateway self-restart** — `sys.exit(0)` is a process-level operation,
  not a use case. Other services go through `service.request_service_restart`.
"""
import asyncio
import os
import socket
import sys

import httpx
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.database import get_db
from middleware.auth import get_current_user, require_admin
from utils.audit import log_audit

from . import service
from .schemas import SettingsUpdate

router = APIRouter(prefix="/settings")


# ===========================================================================
# Health probe helpers (route-layer because they're pure outbound I/O)
# ===========================================================================

async def _check_http(client: httpx.AsyncClient, url: str) -> str:
    try:
        resp = await client.get(url)
        return "healthy" if resp.status_code < 400 else "unhealthy"
    except Exception:
        return "unreachable"


async def _check_tcp(host: str, port: int, timeout: float = 3.0) -> str:
    try:
        loop = asyncio.get_event_loop()
        fut = loop.run_in_executor(None, _tcp_connect, host, port, timeout)
        await asyncio.wait_for(fut, timeout=timeout + 1)
        return "healthy"
    except Exception:
        return "unreachable"


def _tcp_connect(host: str, port: int, timeout: float) -> None:
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.close()


async def _check_dns(host: str) -> str:
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, socket.gethostbyname, host)
        return "healthy"
    except Exception:
        return "unreachable"


async def _check_redis(host: str = "redis", port: int = 6379) -> str:
    try:
        loop = asyncio.get_event_loop()
        fut = loop.run_in_executor(None, _redis_ping, host, port)
        result = await asyncio.wait_for(fut, timeout=4.0)
        return "healthy" if result else "unhealthy"
    except Exception:
        return "unreachable"


def _redis_ping(host: str, port: int) -> bool:
    sock = socket.create_connection((host, port), timeout=3.0)
    redis_url = os.environ.get("REDIS_URL", "")
    password = None
    if ":/" in redis_url:
        try:
            password = redis_url.split("://:", 1)[1].split("@", 1)[0]
        except (IndexError, ValueError):
            pass
    if password:
        sock.sendall(f"AUTH {password}\r\n".encode())
        sock.recv(64)
    sock.sendall(b"PING\r\n")
    data = sock.recv(64)
    sock.close()
    return b"+PONG" in data


_SERVICE_STATUS_DEFS = [
    ("gateway", "API Gateway"),
    ("frontend", "Web Frontend"),
    ("freeradius", "FreeRADIUS"),
    ("postgres", "PostgreSQL"),
    ("redis", "Redis"),
    ("nats", "NATS"),
]


# ===========================================================================
# Routes
# ===========================================================================

@router.get("/service-status")
async def service_status(user: dict = Depends(get_current_user)):
    """Return service list with real health status."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        checks = await asyncio.gather(
            _check_http(client, "http://localhost:8000/health"),
            _check_http(client, "http://frontend:80/"),
            _check_dns("freeradius"),
            _check_tcp("postgres", 5432),
            _check_redis("redis", 6379),
            _check_http(client, "http://nats:8222/healthz"),
            return_exceptions=True,
        )

    results = []
    for i, (name, display) in enumerate(_SERVICE_STATUS_DEFS):
        status = checks[i] if isinstance(checks[i], str) else "unreachable"
        results.append({"name": name, "display_name": display, "status": status})
    return {"services": results}


@router.post("/service-restart/{service_name}")
async def restart_service(
    service_name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Request a service restart. Gateway self-exits; others get a NATS message."""
    client_ip = request.client.host if request.client else None

    if service_name == "gateway":
        await log_audit(
            db, user,
            action="restart", resource_type="service",
            details={"service": "gateway", "description": "Gateway restart requested"},
            ip_address=client_ip,
        )
        await db.commit()

        async def _delayed_exit():
            await asyncio.sleep(1)
            sys.exit(0)

        asyncio.create_task(_delayed_exit())
        return {
            "status": "restarting",
            "service": "gateway",
            "message": "Gateway is restarting. Please wait a few seconds.",
        }

    return await service.request_service_restart(
        db, user, service_name=service_name, client_ip=client_ip,
    )


@router.get("")
async def get_all_settings(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Get all system settings grouped by category. Secrets masked."""
    return await service.get_all_settings_grouped(
        db, tenant_id=user.get("tenant_id"),
    )


@router.get("/{category}")
async def get_settings_by_category(
    category: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Get settings for a specific category."""
    return await service.get_settings_by_category(
        db, tenant_id=user.get("tenant_id"), category=category,
    )


@router.put("/{category}")
async def update_settings(
    category: str,
    body: SettingsUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Batch update settings for a category (admin only)."""
    return await service.update_settings_batch(
        db, user,
        category=category,
        settings_map=body.settings,
        client_ip=request.client.host if request.client else None,
    )
