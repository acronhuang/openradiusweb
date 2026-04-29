"""Policy management feature.

Public contract:
- ``policies_router``: APIRouter for ``/policies/*`` endpoints (CRUD,
  templates, simulation).

Internal modules (``service``, ``repository``, ``events``) are not part
of the public API; consume only the router.
"""
from .routes import router as policies_router

__all__ = ["policies_router"]
