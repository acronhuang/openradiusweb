"""Certificate management feature.

Public contract:
- ``certificates_router``: APIRouter for ``/certificates/*`` endpoints
  (list, get, generate CA/server, import, activate, delete, download).

Internal modules (``service``, ``repository``, ``crypto``, ``events``)
are not part of the public API; consume only the router.
"""
from .routes import router as certificates_router

__all__ = ["certificates_router"]
