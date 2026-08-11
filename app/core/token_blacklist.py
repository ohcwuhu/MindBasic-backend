"""Access Token 黑名单（进程内 TTL；多实例需 Redis）。"""

import time
from threading import Lock

_blacklist: dict[str, float] = {}
_lock = Lock()


def blacklist_token(jti: str, expires_at: float) -> None:
    with _lock:
        _blacklist[jti] = expires_at


def is_blacklisted(jti: str) -> bool:
    now = time.time()
    with _lock:
        expired = [key for key, value in _blacklist.items() if value < now]
        for key in expired:
            _blacklist.pop(key, None)
        return jti in _blacklist


def reset_blacklist() -> None:
    with _lock:
        _blacklist.clear()
