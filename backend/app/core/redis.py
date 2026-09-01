"""Redis connection management with optional in-process fallback for development.
Redis 连接管理，支持可选的进程内降级方案，用于开发环境。
"""



from __future__ import annotations

import time

from loguru import logger
from redis.asyncio import Redis as AsyncRedis

from app.core.config import Settings


class RedisClient:
    """Async Redis client wrapper with optional in-process dict fallback."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: AsyncRedis | None = None
        self._fallback: dict[str, tuple[str, float]] = {}

    async def connect(self) -> None:
        """Connect to Redis or fall back to in-process storage."""
        try:
            self._client = AsyncRedis.from_url(
                self._settings.redis_url,
                socket_connect_timeout=3,
                socket_timeout=5,
            )
            await self._client.ping()
            logger.info("Redis 连接成功")
        except Exception:
            logger.warning("Redis 不可用，使用进程内存储降级")
            self._client = None

    async def get(self, key: str) -> str | None:
        """Get a value by key."""
        if self._client:
            value = await self._client.get(key)
            return value.decode() if isinstance(value, bytes) else value
        item = self._fallback.get(key)
        if item is None:
            return None
        value, expires_at = item
        if time.time() > expires_at:
            self._fallback.pop(key, None)
            return None
        return value

    async def set(self, key: str, value: str, expire_seconds: int) -> None:
        """Set a key with TTL in seconds."""
        if self._client:
            await self._client.set(key, value, ex=expire_seconds)
        else:
            self._fallback[key] = (value, time.time() + expire_seconds)

    async def delete(self, key: str) -> None:
        """Delete a key."""
        if self._client:
            await self._client.delete(key)
        else:
            self._fallback.pop(key, None)

    async def exists(self, key: str) -> bool:
        """Check if a key exists."""
        if self._client:
            return bool(await self._client.exists(key))
        item = self._fallback.get(key)
        if item is None:
            return False
        _, expires_at = item
        if time.time() > expires_at:
            self._fallback.pop(key, None)
            return False
        return True

    async def close(self) -> None:
        """Close the Redis connection."""
        if self._client:
            await self._client.close()
            self._client = None


_redis_instance: RedisClient | None = None


async def get_redis(settings: Settings) -> RedisClient:
    """Return a singleton RedisClient, connecting on first call."""
    global _redis_instance
    if _redis_instance is None:
        _redis_instance = RedisClient(settings)
        await _redis_instance.connect()
    return _redis_instance
