"""Common shared models."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class PaginationParams(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=500)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class PaginatedResponse(BaseModel):
    total: int
    page: int
    page_size: int
    pages: int


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str
    version: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    dependencies: dict[str, str] = {}


class SettingsUpdate(BaseModel):
    """Body for PUT /settings/{category}."""
    settings: dict[str, str] = Field(..., min_length=1)


class BaseResponse(BaseModel):
    id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
