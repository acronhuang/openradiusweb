"""RADIUS realm models."""

from typing import Optional

from pydantic import BaseModel, Field


class RealmCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    realm_type: str = Field(..., pattern=r"^(local|proxy|reject)$")
    strip_username: bool = True
    proxy_host: Optional[str] = None
    proxy_port: int = 1812
    proxy_secret: Optional[str] = None
    proxy_nostrip: bool = False
    proxy_retry_count: int = Field(3, ge=0, le=10)
    proxy_retry_delay_seconds: int = Field(5, ge=1, le=30)
    proxy_dead_time_seconds: int = Field(120, ge=0, le=600)
    ldap_server_id: Optional[str] = None
    auth_types_allowed: list[str] = ["EAP-TLS", "PEAP", "EAP-TTLS", "MAB"]
    default_vlan: Optional[int] = Field(None, ge=1, le=4094)
    default_filter_id: Optional[str] = None
    fallback_realm_id: Optional[str] = None
    priority: int = Field(100, ge=0, le=9999)
    enabled: bool = True


class RealmUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    realm_type: Optional[str] = Field(None, pattern=r"^(local|proxy|reject)$")
    strip_username: Optional[bool] = None
    proxy_host: Optional[str] = None
    proxy_port: Optional[int] = Field(None, ge=1, le=65535)
    proxy_secret: Optional[str] = None
    proxy_nostrip: Optional[bool] = None
    proxy_retry_count: Optional[int] = Field(None, ge=0, le=10)
    proxy_retry_delay_seconds: Optional[int] = Field(None, ge=1, le=30)
    proxy_dead_time_seconds: Optional[int] = Field(None, ge=0, le=600)
    ldap_server_id: Optional[str] = None
    auth_types_allowed: Optional[list[str]] = None
    default_vlan: Optional[int] = Field(None, ge=1, le=4094)
    default_filter_id: Optional[str] = None
    fallback_realm_id: Optional[str] = None
    priority: Optional[int] = Field(None, ge=0, le=9999)
    enabled: Optional[bool] = None
