"""Fixed-window rate limiter: Redis when configured (shared across replicas), memory otherwise."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import Protocol

from app.core.config import Settings


class RateLimiter(Protocol):
    async def hit(self, key: str, limit: int, window_seconds: int) -> bool:
        """Record a hit; return False when the key exceeded `limit` within the window."""

    async def reset(self, key: str) -> None: ...


class MemoryRateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def hit(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        async with self._lock:
            hits = [t for t in self._hits[key] if now - t < window_seconds]
            hits.append(now)
            self._hits[key] = hits
            return len(hits) <= limit

    async def reset(self, key: str) -> None:
        async with self._lock:
            self._hits.pop(key, None)


class RedisRateLimiter:
    def __init__(self, url: str) -> None:
        import redis.asyncio as redis  # optional dependency

        self._redis = redis.from_url(url)

    async def hit(self, key: str, limit: int, window_seconds: int) -> bool:
        bucket = f"rl:{key}:{int(time.time() // window_seconds)}"
        pipe = self._redis.pipeline()
        pipe.incr(bucket)
        pipe.expire(bucket, window_seconds)
        count, _ = await pipe.execute()
        return int(count) <= limit

    async def reset(self, key: str) -> None:
        async for k in self._redis.scan_iter(f"rl:{key}:*"):
            await self._redis.delete(k)


def build_rate_limiter(settings: Settings) -> RateLimiter:
    if settings.redis_url:
        return RedisRateLimiter(settings.redis_url)
    return MemoryRateLimiter()
