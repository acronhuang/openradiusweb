"""Public data surface for the auth feature.

Pydantic models are sourced from `orw_common.models.auth` so that other
services (e.g. event subscribers consuming login events) can share the
exact same shapes. Re-exporting here lets the rest of the feature import
from a single, feature-local module.

If a model becomes auth-specific (no other service consumes it), inline
its definition here and remove it from `orw_common.models.auth`.
"""
from orw_common.models.auth import (
    EmailUpdate,
    LoginRequest,
    PasswordChange,
    PasswordReset,
    TokenResponse,
    UserCreate,
    UserPreferences,
    UserResponse,
    UserUpdate,
)


ROLE_PERMISSIONS: dict[str, dict] = {
    "admin": {
        "description": "Full system access",
        "permissions": [
            "users.read", "users.create", "users.update", "users.delete",
            "devices.read", "devices.create", "devices.update", "devices.delete",
            "policies.read", "policies.create", "policies.update", "policies.delete",
            "network_devices.read", "network_devices.create",
            "network_devices.update", "network_devices.delete",
            "radius.read", "radius.update",
            "audit.read",
            "coa.send",
        ],
    },
    "operator": {
        "description": "Operational access - manage devices and policies",
        "permissions": [
            "devices.read", "devices.create", "devices.update",
            "policies.read", "policies.create", "policies.update",
            "network_devices.read", "network_devices.create",
            "network_devices.update",
            "radius.read",
            "audit.read",
            "coa.send",
        ],
    },
    "viewer": {
        "description": "Read-only access",
        "permissions": [
            "devices.read",
            "policies.read",
            "network_devices.read",
            "radius.read",
            "audit.read",
        ],
    },
}


__all__ = [
    "EmailUpdate",
    "LoginRequest",
    "PasswordChange",
    "PasswordReset",
    "ROLE_PERMISSIONS",
    "TokenResponse",
    "UserCreate",
    "UserPreferences",
    "UserResponse",
    "UserUpdate",
]
