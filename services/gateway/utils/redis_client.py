"""Async Redis client helper (lazy singleton)."""

import redis.asyncio as redis
from orw_common.config import get_settings

_redis: redis.Redis | None = None


async def get_redis_client() -> redis.Redis:
    """Get or create the Redis client singleton."""
    global _redis
    if _redis is None:
        settings = get_settings()
        _redis = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis
