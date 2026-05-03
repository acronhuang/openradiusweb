"""Certificate management feature.

Public contract:
- ``certificates_router``: APIRouter for ``/certificates/*`` endpoints
  (list, get, generate CA/server, import, activate, delete, download).
- ``run_auto_renewal_once``: invoked by the gateway lifespan loop to
  renew active server certs nearing expiry. Idempotent — safe to call
  on every wake-up. See ``auto_renewal.py`` for the loop driver.

Internal modules (``service``, ``repository``, ``crypto``, ``events``)
are not part of the public API; consume only the router or the
auto-renewal entrypoint.
"""
from .auto_renewal import run_auto_renewal_once
from .routes import router as certificates_router

__all__ = ["certificates_router", "run_auto_renewal_once"]
