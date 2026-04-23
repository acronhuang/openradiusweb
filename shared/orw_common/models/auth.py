"""Authentication and user models."""

from datetime import datetime
from uuid import UUID
from typing import Optional

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: Optional[str] = None
    password: str = Field(..., min_length=8)
    role: str = Field(default="viewer", pattern=r"^(admin|operator|viewer)$")


class UserUpdate(BaseModel):
    email: Optional[str] = None
    role: Optional[str] = Field(None, pattern=r"^(admin|operator|viewer)$")
    enabled: Optional[bool] = None


class PasswordReset(BaseModel):
    new_password: str = Field(..., min_length=8)


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8)


class EmailUpdate(BaseModel):
    email: str = Field(..., pattern=r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


class UserPreferences(BaseModel):
    timezone: str = "UTC"
    language: str = "en"
    theme: str = Field(default="light", pattern=r"^(light|dark)$")
    notifications_enabled: bool = True


class UserResponse(BaseModel):
    id: UUID
    username: str
    email: Optional[str] = None
    role: str
    enabled: bool
    last_login: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}
