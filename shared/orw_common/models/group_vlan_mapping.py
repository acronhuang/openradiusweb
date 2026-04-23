"""Pydantic models for Group-to-VLAN mappings (Dynamic VLAN Assignment)."""

from typing import Optional
from pydantic import BaseModel, Field


class GroupVlanMappingCreate(BaseModel):
    group_name: str = Field(..., min_length=1, max_length=255)
    vlan_id: int = Field(..., ge=1, le=4094)
    priority: int = Field(default=100, ge=1, le=9999)
    description: Optional[str] = None
    ldap_server_id: Optional[str] = None
    enabled: bool = True


class GroupVlanMappingUpdate(BaseModel):
    group_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    vlan_id: Optional[int] = Field(default=None, ge=1, le=4094)
    priority: Optional[int] = Field(default=None, ge=1, le=9999)
    description: Optional[str] = None
    ldap_server_id: Optional[str] = None
    enabled: Optional[bool] = None
