"""统一响应包络助手。"""


def ok(data=None, message: str = "success", trace_id: str | None = None) -> dict:
    return {"code": "OK", "message": message, "data": data, "traceId": trace_id}
