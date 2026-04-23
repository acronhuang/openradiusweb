"""NAS client models."""

from typing import Optional

from pydantic import BaseModel, Field


class NASClientCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    ip_address: str
    shared_secret: str = Field(..., min_length=6)
    shortname: Optional[str] = None
    nas_type: str = "other"
    description: Optional[str] = None


class NASClientUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    ip_address: Optional[str] = None
    shared_secret: Optional[str] = Field(default=None, min_length=6)
    shortname: Optional[str] = None
    nas_type: Optional[str] = None
    description: Optional[str] = None
    enabled: Optional[bool] = None
