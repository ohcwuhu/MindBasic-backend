"""时间序列化工具。"""

from datetime import datetime


def to_iso(dt: datetime | None) -> str | None:
    """MySQL DATETIME 为 naive UTC，统一输出 ISO 8601 并补 Z。"""
    if dt is None:
        return None
    text = dt.isoformat()
    if not text.endswith("Z") and "+" not in text:
        text += "Z"
    return text
