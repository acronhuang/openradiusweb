"""Public API for the ldap_servers feature."""
from .routes import router as ldap_servers_router

__all__ = ["ldap_servers_router"]
