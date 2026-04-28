"""Public API for the auth feature.

External callers (i.e. `gateway.main`) must import only the symbols listed
in `__all__`. Other features must NOT import from `service.py`,
`repository.py`, or `routes.py` directly — extend `__all__` instead.
"""
from .routes import auth_router, profile_router

__all__ = ["auth_router", "profile_router"]
