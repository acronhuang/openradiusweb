"""Certificate models."""

from typing import Optional

from pydantic import BaseModel, Field


class GenerateCARequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    common_name: str = Field(..., min_length=1, max_length=255)
    validity_days: int = Field(3650, ge=1, le=36500)
    key_size: int = Field(4096, ge=2048, le=8192)
    organization: Optional[str] = None
    country: Optional[str] = Field(None, min_length=2, max_length=2)


class GenerateServerRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    common_name: str = Field(..., min_length=1, max_length=255)
    san_dns: list[str] = []
    san_ips: list[str] = []
    validity_days: int = Field(730, ge=1, le=3650)
    key_size: int = Field(2048, ge=2048, le=8192)


class ImportCertRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    cert_type: str = Field(..., pattern=r"^(ca|server|client|radius)$")
    cert_pem: str
    key_pem: Optional[str] = None
    chain_pem: Optional[str] = None
