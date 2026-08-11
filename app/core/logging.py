"""统一结构化日志（JSON 行格式）。"""

import json
import logging
import sys
from datetime import datetime, timezone

from app.core.config import settings


class JsonFormatter(logging.Formatter):
    """输出单行 JSON：{ts, level, logger, message, ...extra, exc?}。"""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "extra", None)
        if isinstance(extra, dict):
            entry.update(extra)
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def setup_logging(level: str | None = None) -> None:
    """配置根日志为 JSON 输出，日志级别来自配置。"""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel((level or settings.log_level).upper())
    # 访问日志由应用中间件统一输出，关闭 uvicorn 自带访问日志
    logging.getLogger("uvicorn.access").disabled = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
