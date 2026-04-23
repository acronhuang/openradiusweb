"""Policy data models."""

from datetime import datetime
from uuid import UUID
from typing import Optional, Any

from pydantic import BaseModel, Field


class PolicyCondition(BaseModel):
    field: str
    operator: str  # equals, not_equals, in, not_in, contains, gt, lt, regex
    value: Any


class PolicyAction(BaseModel):
    type: str  # vlan_assign, acl_apply, notify, quarantine, coa
    params: dict[str, Any] = {}


class PolicyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    priority: int = Field(default=100, ge=1, le=10000)
    conditions: list[PolicyCondition]
    match_actions: list[PolicyAction]
    no_match_actions: list[PolicyAction] = []
    enabled: bool = True


class PolicyUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    priority: Optional[int] = Field(None, ge=1, le=10000)
    conditions: Optional[list[PolicyCondition]] = None
    match_actions: Optional[list[PolicyAction]] = None
    no_match_actions: Optional[list[PolicyAction]] = None
    enabled: Optional[bool] = None


class PolicyTemplateOverrides(BaseModel):
    """Optional overrides when applying a policy template."""
    name: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[int] = Field(None, ge=1, le=10000)
    conditions: Optional[list[dict[str, Any]]] = None
    match_actions: Optional[list[dict[str, Any]]] = None
    no_match_actions: Optional[list[dict[str, Any]]] = None


class DeviceContext(BaseModel):
    """Device context for policy simulation."""
    mac_address: Optional[str] = None
    ip_address: Optional[str] = None
    hostname: Optional[str] = None
    device_type: Optional[str] = None
    os_family: Optional[str] = None
    vendor: Optional[str] = None
    vlan: Optional[int] = None
    port: Optional[str] = None
    switch_ip: Optional[str] = None
    auth_type: Optional[str] = None
    username: Optional[str] = None
    realm: Optional[str] = None


class PolicyResponse(BaseModel):
    id: UUID
    name: str
    description: Optional[str] = None
    priority: int
    conditions: list[dict]
    match_actions: list[dict]
    no_match_actions: list[dict]
    enabled: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
