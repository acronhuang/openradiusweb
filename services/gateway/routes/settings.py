"""System settings management routes."""

import asyncio
import socket
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from orw_common.database import get_db
from orw_common.models.common import SettingsUpdate
from middleware.auth import get_current_user, require_admin
from utils.audit import log_audit

router = APIRouter(prefix="/settings")


async def _check_http(client: httpx.AsyncClient, url: str) -> str:
    """Check service via HTTP GET."""
    try:
        resp = await client.get(url)
        return "healthy" if resp.status_code < 400 else "unhealthy"
    except Exception:
        return "unreachable"


async def _check_tcp(host: str, port: int, timeout: float = 3.0) -> str:
    """Check service via TCP socket connection."""
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
    """Check if a hostname resolves (container is running on the network)."""
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, socket.gethostbyname, host)
        return "healthy"
    except Exception:
        return "unreachable"


async def _check_redis(host: str = "redis", port: int = 6379) -> str:
    """Check Redis with AUTH + PING using password from REDIS_URL env."""
    try:
        loop = asyncio.get_event_loop()
        fut = loop.run_in_executor(None, _redis_ping, host, port)
        result = await asyncio.wait_for(fut, timeout=4.0)
        return "healthy" if result else "unhealthy"
    except Exception:
        return "unreachable"


def _redis_ping(host: str, port: int) -> bool:
    import os
    sock = socket.create_connection((host, port), timeout=3.0)
    # Extract password from REDIS_URL (redis://:password@host:port/db)
    redis_url = os.environ.get("REDIS_URL", "")
    password = None
    if ":/" in redis_url:
        try:
            password = redis_url.split("://:", 1)[1].split("@", 1)[0]
        except (IndexError, ValueError):
            pass
    if password:
        sock.sendall(f"AUTH {password}\r\n".encode())
        sock.recv(64)  # +OK or -ERR
    sock.sendall(b"PING\r\n")
    data = sock.recv(64)
    sock.close()
    return b"+PONG" in data


def _mask_sensitive(row: dict) -> dict:
    """Mask value if the setting is marked as secret."""
    out = dict(row)
    if out.get("is_secret"):
        out["setting_value"] = "********"
    return out


# ============================================================
# Get Settings
# ============================================================

@router.get("/service-status")
async def service_status(
    user: dict = Depends(get_current_user),
):
    """
    Return service list with real health status.
    Uses HTTP for web services, TCP/protocol checks for infrastructure.
    """
    async with httpx.AsyncClient(timeout=5.0) as client:
        checks = await asyncio.gather(
            _check_http(client, "http://localhost:8000/health"),   # gateway
            _check_http(client, "http://frontend:80/"),           # frontend (nginx on port 80)
            _check_dns("freeradius"),                               # freeradius (UDP only, DNS = container alive)
            _check_tcp("postgres", 5432),                         # postgres TCP
            _check_redis("redis", 6379),                          # redis PING
            _check_http(client, "http://nats:8222/healthz"),      # nats monitoring HTTP
            return_exceptions=True,
        )

    service_defs = [
        ("gateway", "API Gateway"),
        ("frontend", "Web Frontend"),
        ("freeradius", "FreeRADIUS"),
        ("postgres", "PostgreSQL"),
        ("redis", "Redis"),
        ("nats", "NATS"),
    ]

    results = []
    for i, (name, display) in enumerate(service_defs):
        status = checks[i] if isinstance(checks[i], str) else "unreachable"
        results.append({"name": name, "display_name": display, "status": status})

    return {"services": results}


# Allowed services for restart via NATS
_RESTART_NATS_TOPICS = {
    "freeradius": "orw.service.freeradius.restart",
    "discovery": "orw.service.discovery.restart",
    "device_inventory": "orw.service.device_inventory.restart",
    "policy_engine": "orw.service.policy_engine.restart",
    "switch_mgmt": "orw.service.switch_mgmt.restart",
    "coa": "orw.service.coa.restart",
}


@router.post("/service-restart/{service_name}")
async def restart_service(
    service_name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """
    Request a service restart via NATS message.
    Only admin users can restart services.
    Gateway restarts itself; other services receive NATS notification.
    """
    from orw_common import nats_client

    if service_name == "gateway":
        # Gateway self-restart: log then exit (Docker will restart it)
        await log_audit(db, user, "restart", "service",
                        details={"service": "gateway", "description": "Gateway restart requested"})
        await db.commit()

        async def _delayed_exit():
            await asyncio.sleep(1)
            import sys
            sys.exit(0)

        asyncio.create_task(_delayed_exit())
        return {"status": "restarting", "service": "gateway",
                "message": "Gateway is restarting. Please wait a few seconds."}

    if service_name not in _RESTART_NATS_TOPICS and service_name not in ("postgres", "redis", "nats", "frontend"):
        raise HTTPException(status_code=400, detail=f"Unknown service: {service_name}")

    if service_name in ("postgres", "redis", "nats", "frontend"):
        raise HTTPException(status_code=400,
                            detail=f"Cannot restart {service_name} from Web UI. Use SSH to restart infrastructure services.")

    # Publish restart request via NATS
    topic = _RESTART_NATS_TOPICS[service_name]
    await nats_client.publish(topic, {
        "action": "restart",
        "requested_by": user.get("username", user.get("sub")),
    })

    client_ip = request.client.host if request.client else None
    await log_audit(db, user, "restart", "service",
                    details={"service": service_name},
                    ip_address=client_ip)
    await db.commit()

    return {"status": "restart_requested", "service": service_name,
            "message": f"Restart request sent to {service_name}"}


@router.get("")
async def get_all_settings(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """
    Get all system settings grouped by category.
    Secret values are masked. Filtered by tenant.
    """
    result = await db.execute(
        text(
            "SELECT setting_key, setting_value, value_type, category, description, is_secret "
            "FROM system_settings "
            "WHERE tenant_id = :tenant_id OR tenant_id IS NULL "
            "ORDER BY category, setting_key"
        ),
        {"tenant_id": user["tenant_id"]},
    )
    rows = result.mappings().all()

    categories: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        entry = _mask_sensitive(dict(row))
        cat = entry.pop("category", "general")
        categories.setdefault(cat, []).append(entry)

    return {"categories": categories}


@router.get("/{category}")
async def get_settings_by_category(
    category: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Get settings for a specific category, filtered by tenant."""
    result = await db.execute(
        text(
            "SELECT setting_key, setting_value, value_type, category, description, is_secret "
            "FROM system_settings "
            "WHERE category = :category AND (tenant_id = :tenant_id OR tenant_id IS NULL) "
            "ORDER BY setting_key"
        ),
        {"category": category, "tenant_id": user["tenant_id"]},
    )
    rows = result.mappings().all()

    if not rows:
        raise HTTPException(status_code=404, detail=f"No settings found for category '{category}'")

    items = [_mask_sensitive(dict(r)) for r in rows]
    return {"category": category, "settings": items}


# ============================================================
# Update Settings
# ============================================================

@router.put("/{category}")
async def update_settings(
    category: str,
    body: SettingsUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """
    Batch update settings for a category.
    Body: {"settings": {"setting_key": "new_value", ...}}
    Logs audit with old and new values.
    """
    settings_map = body.settings

    # Fetch current values for audit trail (tenant-scoped)
    result = await db.execute(
        text(
            "SELECT setting_key, setting_value, is_secret FROM system_settings "
            "WHERE category = :category AND setting_key = ANY(:keys) "
            "AND (tenant_id = :tenant_id OR tenant_id IS NULL)"
        ),
        {
            "category": category,
            "keys": list(settings_map.keys()),
            "tenant_id": user["tenant_id"],
        },
    )
    existing = {r["setting_key"]: r for r in result.mappings().all()}

    updated_keys = []
    audit_changes: list[dict[str, Any]] = []

    for key, new_value in settings_map.items():
        old_row = existing.get(key)
        if old_row is None:
            continue

        old_value = old_row["setting_value"]
        is_secret = old_row["is_secret"]

        await db.execute(
            text(
                "UPDATE system_settings SET setting_value = :value, updated_at = NOW() "
                "WHERE category = :category AND setting_key = :key "
                "AND (tenant_id = :tenant_id OR tenant_id IS NULL)"
            ),
            {
                "value": str(new_value),
                "category": category,
                "key": key,
                "tenant_id": user["tenant_id"],
            },
        )
        updated_keys.append(key)

        audit_changes.append({
            "key": key,
            "old_value": "********" if is_secret else old_value,
            "new_value": "********" if is_secret else str(new_value),
        })

    if not updated_keys:
        raise HTTPException(status_code=400, detail="No valid settings keys found for this category")

    await db.commit()

    client_ip = request.client.host if request.client else None
    await log_audit(
        db=db,
        user=user,
        action="update",
        resource_type="system_settings",
        resource_id=None,
        details={"category": category, "changes": audit_changes},
        ip_address=client_ip,
    )
    await db.commit()

    return {"updated": updated_keys, "category": category}
