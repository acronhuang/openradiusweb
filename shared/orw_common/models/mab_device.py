"""MAB (MAC Authentication Bypass) device whitelist models."""

import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class MabDeviceCreate(BaseModel):
    mac_address: str = Field(..., description="MAC address (any format)")
    name: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    device_type: Optional[str] = None
    assigned_vlan_id: Optional[int] = Field(None, ge=1, le=4094)
    enabled: bool = True
    expiry_date: Optional[datetime] = None

    @field_validator("mac_address")
    @classmethod
    def normalize_mac(cls, v: str) -> str:
        raw = re.sub(r"[^0-9a-fA-F]", "", v)
        if len(raw) != 12:
            raise ValueError("MAC address must be 12 hex characters")
        return ":".join(raw[i : i + 2].lower() for i in range(0, 12, 2))


class MabDeviceUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    device_type: Optional[str] = None
    assigned_vlan_id: Optional[int] = Field(None, ge=1, le=4094)
    enabled: Optional[bool] = None
    expiry_date: Optional[datetime] = None


class MabDeviceBulkItem(BaseModel):
    mac_address: str
    name: Optional[str] = None
    device_type: Optional[str] = None
    assigned_vlan_id: Optional[int] = Field(None, ge=1, le=4094)

    @field_validator("mac_address")
    @classmethod
    def normalize_mac(cls, v: str) -> str:
        raw = re.sub(r"[^0-9a-fA-F]", "", v)
        if len(raw) != 12:
            raise ValueError(f"Invalid MAC: {v}")
        return ":".join(raw[i : i + 2].lower() for i in range(0, 12, 2))
