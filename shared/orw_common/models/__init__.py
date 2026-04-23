"""Pydantic models for API request/response schemas."""

from .device import (
    DeviceCreate,
    DeviceUpdate,
    DeviceResponse,
    DeviceListResponse,
    DevicePropertyCreate,
)
from .network_device import (
    NetworkDeviceCreate,
    NetworkDeviceUpdate,
    NetworkDeviceResponse,
)
from .policy import PolicyCreate, PolicyUpdate, PolicyResponse
from .auth import TokenResponse, LoginRequest, UserCreate, UserResponse
from .common import PaginationParams, HealthResponse
from .vlan import VlanCreate, VlanUpdate
from .mab_device import MabDeviceCreate, MabDeviceUpdate, MabDeviceBulkItem
from .group_vlan_mapping import GroupVlanMappingCreate, GroupVlanMappingUpdate

__all__ = [
    "DeviceCreate", "DeviceUpdate", "DeviceResponse", "DeviceListResponse",
    "DevicePropertyCreate",
    "NetworkDeviceCreate", "NetworkDeviceUpdate", "NetworkDeviceResponse",
    "PolicyCreate", "PolicyUpdate", "PolicyResponse",
    "TokenResponse", "LoginRequest", "UserCreate", "UserResponse",
    "PaginationParams", "HealthResponse",
    "VlanCreate", "VlanUpdate",
    "MabDeviceCreate", "MabDeviceUpdate", "MabDeviceBulkItem",
    "GroupVlanMappingCreate", "GroupVlanMappingUpdate",
]
