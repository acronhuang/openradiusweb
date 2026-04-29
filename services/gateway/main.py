"""OpenRadiusWeb API Gateway - Main application entry point."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from prometheus_fastapi_instrumentator import Instrumentator

from orw_common import __version__
from orw_common.config import get_settings
from orw_common.database import close_db
from orw_common.exceptions import (
    DomainError, NotFoundError, ConflictError, ValidationError,
    AuthenticationError, AuthorizationError, RateLimitError,
)
from orw_common.logging import setup_logging
from orw_common import nats_client

from routes import (
    network_devices, policies, radius_auth_log,
    certificates,
    dot1x_overview,
)
from features.auth import auth_router, profile_router
from features.health import health_router
from features.vlans import vlans_router
from features.nas_clients import nas_clients_router
from features.mab_devices import mab_devices_router
from features.group_vlan_mappings import group_vlan_mappings_router
from features.audit import audit_router
from features.settings import settings_router
from features.ldap_servers import ldap_servers_router
from features.radius_realms import radius_realms_router
from features.coa import coa_router
from features.freeradius_config import freeradius_config_router
from features.devices import devices_router

settings = get_settings()
log = setup_logging("gateway")


# ---------------------------------------------------------------------------
# Security headers middleware
# ---------------------------------------------------------------------------
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Cache-Control"] = "no-store"
        if os.environ.get("ENABLE_HSTS", "").lower() in ("1", "true", "yes"):
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response


# ---------------------------------------------------------------------------
# Domain exception handlers
# ---------------------------------------------------------------------------
_DOMAIN_STATUS_MAP = {
    NotFoundError: 404,
    ConflictError: 409,
    ValidationError: 400,
    AuthenticationError: 401,
    AuthorizationError: 403,
    RateLimitError: 429,
}


async def domain_exception_handler(request: Request, exc: DomainError):
    status_code = _DOMAIN_STATUS_MAP.get(type(exc), 400)
    return JSONResponse(status_code=status_code, content={"detail": exc.message})


# ---------------------------------------------------------------------------
# Global exception handler - prevent internal details leaking
# ---------------------------------------------------------------------------
async def generic_exception_handler(request: Request, exc: Exception):
    log.error("unhandled_exception", path=request.url.path, error=str(exc))
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup/shutdown lifecycle."""
    log.info("gateway_starting", version=__version__)

    # Connect to NATS
    await nats_client.connect()
    await nats_client.ensure_stream(
        "orw", ["orw.>"]
    )
    log.info("gateway_ready")

    yield

    # Cleanup
    await nats_client.close()
    await close_db()
    log.info("gateway_stopped")


app = FastAPI(
    title="OpenRadiusWeb API",
    description="Open-source RADIUS Authentication & Network Access Control",
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Domain exception handlers (404, 409, 400, 401, 403, 429)
app.add_exception_handler(DomainError, domain_exception_handler)

# Global exception handler (500 catch-all)
app.add_exception_handler(Exception, generic_exception_handler)

# Request ID tracing
from middleware.request_id import RequestIDMiddleware
app.add_middleware(RequestIDMiddleware)

# Security headers
app.add_middleware(SecurityHeadersMiddleware)

# CORS - use explicit origins; allow_credentials=True requires specific origins
_cors_origins = os.environ.get("CORS_ORIGINS", "").strip()
if _cors_origins:
    _origins = [o.strip() for o in _cors_origins.split(",") if o.strip()]
else:
    # Default: allow same-host on common dev/prod ports
    _origins = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8888",
    ]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# Routes
prefix = settings.api_prefix
app.include_router(health_router, tags=["Health"])
app.include_router(auth_router, prefix=prefix, tags=["Authentication"])
app.include_router(profile_router, prefix=prefix, tags=["Profile"])
app.include_router(devices_router, prefix=prefix, tags=["Devices"])
app.include_router(network_devices.router, prefix=prefix, tags=["Network Devices"])
app.include_router(policies.router, prefix=prefix, tags=["Policies"])
app.include_router(radius_auth_log.router, prefix=prefix, tags=["RADIUS Auth Log"])
app.include_router(coa_router, prefix=prefix, tags=["Change of Authorization"])
app.include_router(certificates.router, prefix=prefix, tags=["Certificates"])
app.include_router(ldap_servers_router, prefix=prefix, tags=["LDAP Servers"])
app.include_router(radius_realms_router, prefix=prefix, tags=["RADIUS Realms"])
app.include_router(nas_clients_router, prefix=prefix, tags=["NAS Clients"])
app.include_router(settings_router, prefix=prefix, tags=["Settings"])
app.include_router(freeradius_config_router, prefix=prefix, tags=["FreeRADIUS Config"])
app.include_router(audit_router, prefix=prefix, tags=["Audit Log"])
app.include_router(vlans_router, prefix=prefix, tags=["VLANs"])
app.include_router(mab_devices_router, prefix=prefix, tags=["MAB Devices"])
app.include_router(dot1x_overview.router, prefix=prefix, tags=["802.1X Overview"])
app.include_router(group_vlan_mappings_router, prefix=prefix, tags=["Group VLAN Mappings"])

# Prometheus metrics endpoint (/metrics)
Instrumentator().instrument(app).expose(app, include_in_schema=False)
