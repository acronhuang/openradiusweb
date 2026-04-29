"""RADIUS Authentication Log feature (Access Tracker).

Public contract:
- ``radius_auth_log_router``: APIRouter for ``/radius/auth-log/*`` endpoints.

Internal modules (``service``, ``repository``) are not part of the public
API; consume only the router.
"""
from .routes import router as radius_auth_log_router

__all__ = ["radius_auth_log_router"]
