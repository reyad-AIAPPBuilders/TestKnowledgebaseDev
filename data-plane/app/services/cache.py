import hashlib

import redis.asyncio as aioredis

from app.config import ext, settings
from app.utils.logger import get_logger

log = get_logger(__name__)

KEY_PREFIX = "dp:cache:"


class ContentCache:
    """Redis-backed content cache with TTL."""

    def __init__(
        self,
        redis_client: aioredis.Redis | None = None,
        default_ttl: int | None = None,
    ) -> None:
        self._redis: aioredis.Redis | None = redis_client
        self._default_ttl = default_ttl if default_ttl is not None else settings.cache_ttl

    async def start(self) -> None:
        if self._redis is None:
            self._redis = aioredis.from_url(ext.redis_url, decode_responses=True)
        log.info("cache_started", redis_url=ext.redis_url)

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()
            self._redis = None

    async def ping(self) -> bool:
        if not self._redis:
            return False
        try:
            return await self._redis.ping()
        except Exception:
            return False

    @staticmethod
    def _make_key(url: str) -> str:
        return f"{KEY_PREFIX}{hashlib.sha256(url.encode()).hexdigest()}"

    async def get(self, url: str) -> str | None:
        if not self._redis:
            return None
        try:
            key = self._make_key(url)
            value = await self._redis.get(key)
            if value is not None:
                log.debug("cache_hit", url=url)
            return value
        except Exception as exc:
            log.warning("cache_get_failed", url=url, error=str(exc))
            return None

    async def set(self, url: str, content: str, ttl: int | None = None) -> None:
        if not self._redis:
            return
        try:
            key = self._make_key(url)
            await self._redis.set(key, content, ex=ttl or self._default_ttl)
            log.debug("cache_set", url=url)
        except Exception as exc:
            log.warning("cache_set_failed", url=url, error=str(exc))

    async def invalidate(self, url: str) -> None:
        if not self._redis:
            return
        try:
            key = self._make_key(url)
            await self._redis.delete(key)
        except Exception as exc:
            log.warning("cache_invalidate_failed", url=url, error=str(exc))

    async def clear(self) -> None:
        if not self._redis:
            return
        try:
            if hasattr(self._redis, "scan_iter"):
                async for key in self._redis.scan_iter(match=f"{KEY_PREFIX}*"):
                    await self._redis.delete(key)
            else:
                await self._redis.flushdb()
        except Exception as exc:
            log.warning("cache_clear_failed", error=str(exc))
