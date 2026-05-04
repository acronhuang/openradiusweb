"""Network device (switch/router/AP) data models."""

from datetime import datetime
from uuid import UUID
from typing import Optional, Any

from pydantic import BaseModel, Field, field_validator


class NetworkDeviceCreate(BaseModel):
    ip_address: str
    hostname: Optional[str] = None
    vendor: str
    model: Optional[str] = None
    os_version: Optional[str] = None
    device_type: str = Field(..., pattern=r"^(switch|router|ap|firewall)$")
    management_protocol: str = "snmp"
    snmp_version: str = "v2c"
    snmp_community: Optional[str] = None  # AES-256-GCM at write (PR #74)
    ssh_username: Optional[str] = None  # Plaintext column on network_devices
    ssh_password: Optional[str] = None  # AES-256-GCM via orw_common.secrets (PR #100)
    poll_interval_seconds: int = 300


class NetworkDeviceUpdate(BaseModel):
    hostname: Optional[str] = None
    vendor: Optional[str] = None
    model: Optional[str] = None
    os_version: Optional[str] = None
    management_protocol: Optional[str] = None
    snmp_version: Optional[str] = None
    snmp_community: Optional[str] = None  # AES-256-GCM at write
    ssh_username: Optional[str] = None
    ssh_password: Optional[str] = None  # AES-256-GCM at write (PR #100)
    enabled: Optional[bool] = None
    poll_interval_seconds: Optional[int] = None


class NetworkDeviceResponse(BaseModel):
    id: UUID
    ip_address: str
    hostname: Optional[str] = None
    vendor: str
    model: Optional[str] = None
    os_version: Optional[str] = None
    device_type: str
    management_protocol: str
    snmp_version: Optional[str] = "v2c"
    enabled: bool = True
    last_polled: Optional[datetime] = None
    poll_interval_seconds: int = 300
    port_count: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @field_validator("ip_address", mode="before")
    @classmethod
    def coerce_ip(cls, v: Any) -> str:
        return str(v) if v is not None else ""

    model_config = {"from_attributes": True}


class SwitchPortResponse(BaseModel):
    id: UUID
    port_name: str
    port_index: Optional[int] = None
    port_type: str
    admin_status: str
    oper_status: str
    speed_mbps: Optional[int] = None
    current_vlan: Optional[int] = None
    assigned_vlan: Optional[int] = None
    poe_status: Optional[str] = None
    last_mac_seen: Optional[str] = None
    description: Optional[str] = None
    connected_device: Optional[dict] = None
    updated_at: datetime

    model_config = {"from_attributes": True}
