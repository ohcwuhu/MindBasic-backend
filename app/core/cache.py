"""进程内 TTL 缓存（单实例；多实例需 Redis）。"""

import time
from threading import Lock

_cache: dict[str, tuple[float, object]] = {}
_lock = Lock()
DEFAULT_TTL = 60


def get(key: str) -> object | None:
    with _lock:
        item = _cache.get(key)
        if item is None:
            return None
        expires_at, value = item
        if expires_at < time.monotonic():
            _cache.pop(key, None)
            return None
        return value


def set(key: str, value: object, ttl: int = DEFAULT_TTL) -> None:
    with _lock:
        _cache[key] = (time.monotonic() + ttl, value)


def invalidate(key: str) -> None:
    with _lock:
        _cache.pop(key, None)


def invalidate_home() -> None:
    invalidate("home:anon")


def reset_cache() -> None:
    with _lock:
        _cache.clear()
