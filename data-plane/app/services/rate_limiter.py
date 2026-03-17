import asyncio
import time
import uuid
from urllib.parse import urlparse

import redis.asyncio as aioredis

from app.config import ext
from app.services.metrics import mark_rate_limit_wait
from app.utils.logger import get_logger

log = get_logger(__name__)

KEY_PREFIX = "dp:ratelimit:"


class DomainRateLimiter:
    """Per-domain rate limiter using Redis sorted sets (sliding window)."""

    def __init__(
        self,
        redis_client: aioredis.Redis | None = None,
        max_requests: int | None = None,
        window_seconds: int | None = None,
    ) -> None:
        self._redis: aioredis.Redis | None = redis_client
        self._max_requests = max_requests if max_requests is not None else ext.rate_limit_per_domain
        self._window = window_seconds if window_seconds is not None else ext.rate_limit_window

    async def start(self) -> None:
        if self._redis is None:
            self._redis = aioredis.from_url(ext.redis_url, decode_responses=True)
        log.info("rate_limiter_started")

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()
            self._redis = None

    @staticmethod
    def _extract_domain(url: str) -> str:
        return urlparse(url).netloc.lower()

    def _make_key(self, domain: str) -> str:
        return f"{KEY_PREFIX}{domain}"

    async def acquire(self, url: str) -> None:
        if not self._redis:
            return

        domain = self._extract_domain(url)
        key = self._make_key(domain)

        try:
            while True:
                now = time.time()
                window_start = now - self._window

                pipe = self._redis.pipeline()
                pipe.zremrangebyscore(key, 0, window_start)
                pipe.zcard(key)
                results = await pipe.execute()
                current_count = int(results[1])

                if current_count < self._max_requests:
                    member = str(uuid.uuid4())
                    await self._redis.zadd(key, {member: now})
                    await self._redis.expire(key, self._window + 10)
                    log.debug("rate_limit_acquired", domain=domain, usage=current_count + 1)
                    return

                oldest_entries = await self._redis.zrange(key, 0, 0, withscores=True)
                if oldest_entries:
                    oldest_score = float(oldest_entries[0][1])
                    wait_time = self._window - (now - oldest_score) + 0.1
                else:
                    wait_time = 1.0

                log.info("rate_limit_waiting", domain=domain, wait_seconds=round(wait_time, 1))
                mark_rate_limit_wait(domain)
                await asyncio.sleep(max(wait_time, 0.1))
        except Exception as exc:
            log.warning("rate_limiter_failed_allowing", domain=domain, error=str(exc))

    async def current_usage(self, url: str) -> int:
        if not self._redis:
            return 0
        domain = self._extract_domain(url)
        key = self._make_key(domain)
        now = time.time()
        window_start = now - self._window
        await self._redis.zremrangebyscore(key, 0, window_start)
        return int(await self._redis.zcard(key))
