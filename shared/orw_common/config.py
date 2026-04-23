"""Centralized configuration management using pydantic-settings."""

from functools import lru_cache
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Service identity
    service_name: str = "openradiusweb"
    log_level: str = "INFO"
    debug: bool = False

    # Database (REQUIRED — set DATABASE_URL env var)
    database_url: str = Field(
        default="",
        description="PostgreSQL connection string (e.g. postgresql+asyncpg://orw:PASSWORD@localhost:5432/orw)"
    )
    db_pool_size: int = 20
    db_max_overflow: int = 10

    # Redis (REQUIRED — set REDIS_URL env var)
    redis_url: str = Field(
        default="",
        description="Redis connection string (e.g. redis://:PASSWORD@localhost:6379/0)"
    )

    # NATS
    nats_url: str = Field(
        default="nats://localhost:4222",
        description="NATS server URL"
    )

    # JWT (REQUIRED — set JWT_SECRET_KEY env var)
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_prefix: str = "/api/v1"

    model_config = {"env_prefix": "", "case_sensitive": False}


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
