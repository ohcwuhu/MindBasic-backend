"""统一响应包络助手。"""


def ok(data=None, message: str = "success", trace_id: str | None = None) -> dict:
    return {"code": "OK", "message": message, "data": data, "traceId": trace_id}


def paginated(items: list, total: int, page: int, page_size: int) -> dict:
    """统一分页数据结构。"""
    total_pages = (total + page_size - 1) // page_size if total else 0
    return {
        "items": items,
        "pagination": {
            "page": page,
            "pageSize": page_size,
            "totalItems": total,
            "totalPages": total_pages,
            "hasMore": page < total_pages,
        },
    }
