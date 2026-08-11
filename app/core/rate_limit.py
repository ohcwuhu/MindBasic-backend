"""频率限制：可插拔后端（内存 / Redis）。"""

import time
from collections import defaultdict, deque
from typing import Protocol

from fastapi import Request

from app.core.config import settings
from app.core.exceptions import AppError


class RateLimitBackend(Protocol):
    def allowed(self, scope: str, key: str, limit: int, window_seconds: int) -> bool: ...

    def reset(self) -> None: ...


class InMemoryRateLimiter:
    """进程内滑动窗口（单实例适用）。"""

    def __init__(self) -> None:
        self._buckets: dict[str, deque] = defaultdict(deque)

    def allowed(self, scope: str, key: str, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        bucket = self._buckets[f"{scope}:{key}"]
        while bucket and now - bucket[0] > window_seconds:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True

    def reset(self) -> None:
        self._buckets.clear()


class RedisRateLimiter:
    """基于 Redis INCR + EXPIRE 的固定窗口（多实例适用）。"""

    def __init__(self, url: str) -> None:
        import redis

        self.client = redis.Redis.from_url(url, decode_responses=True)
        self.prefix = "rl"

    def allowed(self, scope: str, key: str, limit: int, window_seconds: int) -> bool:
        rkey = f"{self.prefix}:{scope}:{key}"
        pipe = self.client.pipeline()
        pipe.incr(rkey)
        pipe.expire(rkey, window_seconds, nx=True)
        count = pipe.execute()[0]
        return int(count) <= limit

    def reset(self) -> None:
        pass


_limiter: RateLimitBackend | None = None


def get_rate_limiter() -> RateLimitBackend:
    global _limiter
    if _limiter is None:
        if settings.rate_limit_backend == "redis":
            if not settings.redis_url:
                raise RuntimeError("RATE_LIMIT_BACKEND=redis 但未配置 REDIS_URL")
            _limiter = RedisRateLimiter(settings.redis_url)
        else:
            _limiter = InMemoryRateLimiter()
    return _limiter


def rate_limit(scope: str, limit: int, window_seconds: int):
    """按客户端 IP 限流，超限返回 429 RATE_LIMITED。"""

    def checker(request: Request) -> None:
        client = request.client.host if request.client else "unknown"
        if not get_rate_limiter().allowed(scope, client, limit, window_seconds):
            raise AppError(429, "RATE_LIMITED", "请求过于频繁，请稍后再试")

    return checker


def reset_rate_limits() -> None:
    """测试隔离用；Redis 后端下为 no-op。"""
    get_rate_limiter().reset()
