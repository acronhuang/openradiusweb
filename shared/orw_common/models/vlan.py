"""VLAN management models."""

from typing import Optional

from pydantic import BaseModel, Field


class VlanCreate(BaseModel):
    vlan_id: int = Field(..., ge=1, le=4094)
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    purpose: Optional[str] = Field(
        None,
        pattern=r"^(corporate|guest|quarantine|iot|voip|printer|remediation|management)$",
    )
    subnet: Optional[str] = None
    enabled: bool = True


class VlanUpdate(BaseModel):
    vlan_id: Optional[int] = Field(None, ge=1, le=4094)
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    purpose: Optional[str] = None
    subnet: Optional[str] = None
    enabled: Optional[bool] = None
