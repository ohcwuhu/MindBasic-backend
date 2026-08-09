"""轻量进程内频率限制。

单机部署够用；多实例部署时应迁移到 Redis 等共享存储。
"""

import time
from collections import defaultdict, deque

from fastapi import Request

from app.core.exceptions import AppError

_buckets: dict[str, deque] = defaultdict(deque)


def rate_limit(scope: str, limit: int, window_seconds: int):
    """按客户端 IP 做滑动窗口限流，超限返回 429 RATE_LIMITED。"""

    def checker(request: Request) -> None:
        client = request.client.host if request.client else "unknown"
        key = f"{scope}:{client}"
        now = time.monotonic()
        bucket = _buckets[key]
        while bucket and now - bucket[0] > window_seconds:
            bucket.popleft()
        if len(bucket) >= limit:
            raise AppError(429, "RATE_LIMITED", "请求过于频繁，请稍后再试")
        bucket.append(now)

    return checker


def reset_rate_limits() -> None:
    _buckets.clear()
