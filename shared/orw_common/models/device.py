"""Device (endpoint) data models."""

from datetime import datetime
from uuid import UUID
from typing import Optional, Any

from pydantic import BaseModel, Field, field_validator

from .common import PaginatedResponse


class DeviceCreate(BaseModel):
    mac_address: str = Field(..., pattern=r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
    ip_address: Optional[str] = None
    hostname: Optional[str] = None
    device_type: Optional[str] = None
    os_family: Optional[str] = None
    os_version: Optional[str] = None
    vendor: Optional[str] = None
    model: Optional[str] = None


class DeviceUpdate(BaseModel):
    ip_address: Optional[str] = None
    hostname: Optional[str] = None
    device_type: Optional[str] = None
    os_family: Optional[str] = None
    os_version: Optional[str] = None
    vendor: Optional[str] = None
    model: Optional[str] = None
    status: Optional[str] = None
    risk_score: Optional[int] = Field(None, ge=0, le=100)


class DeviceResponse(BaseModel):
    id: UUID
    mac_address: str
    ip_address: Optional[str] = None
    hostname: Optional[str] = None
    device_type: Optional[str] = None
    os_family: Optional[str] = None
    os_version: Optional[str] = None
    vendor: Optional[str] = None
    model: Optional[str] = None
    first_seen: datetime
    last_seen: datetime
    status: str
    risk_score: int

    @field_validator("mac_address", "ip_address", mode="before")
    @classmethod
    def coerce_addr(cls, v: Any) -> Optional[str]:
        return str(v) if v is not None else None

    model_config = {"from_attributes": True}


class DeviceListResponse(PaginatedResponse):
    items: list[DeviceResponse]


class DevicePropertyCreate(BaseModel):
    category: str
    key: str
    value: str
    source: str = "manual"
    confidence: float = Field(default=1.0, ge=0, le=1)
