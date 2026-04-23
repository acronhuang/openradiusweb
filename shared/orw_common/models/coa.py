"""CoA (Change of Authorization) models."""

from pydantic import BaseModel, Field


class CoARequest(BaseModel):
    """CoA request payload."""
    action: str = Field(
        ...,
        description="CoA action type",
        pattern="^(disconnect|reauthenticate|vlan_change|apply_acl|bounce_port)$",
    )
    vlan_id: int | None = Field(None, ge=1, le=4094, description="Target VLAN for vlan_change")
    acl_name: str | None = Field(None, description="ACL name for apply_acl")
    reason: str | None = Field(None, description="Reason for CoA (for audit log)")


class CoAByMacRequest(CoARequest):
    mac_address: str = Field(..., description="Device MAC address")


class CoAByUsernameRequest(CoARequest):
    username: str = Field(..., description="Username to disconnect/reauthenticate")


class CoABySessionRequest(CoARequest):
    session_id: str = Field(..., description="RADIUS session ID")


class CoABulkRequest(BaseModel):
    """Bulk CoA for multiple devices."""
    action: str = Field(..., pattern="^(disconnect|reauthenticate|vlan_change)$")
    targets: list[str] = Field(..., description="List of MAC addresses or session IDs")
    target_type: str = Field("mac", pattern="^(mac|session_id|username)$")
    vlan_id: int | None = None
    reason: str | None = None
