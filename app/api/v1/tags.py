"""公开标签目录（教练入驻表单与筛选用）。"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db
from app.api.response import ok
from app.models.coach import Tag

router = APIRouter(prefix="/tags", tags=["tags"])


@router.get("")
async def list_tags(
    request: Request,
    type: str | None = Query(default=None, pattern="^(FIELD|AUDIENCE)$"),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    stmt = (
        select(Tag)
        .where(Tag.is_enabled.is_(True))
        .order_by(Tag.type, Tag.sort_order, Tag.id)
    )
    if type:
        stmt = stmt.where(Tag.type == type)
    tags = await db.scalars(stmt)
    items = [
        {"id": t.id, "name": t.name, "type": t.type}
        for t in tags
    ]
    return ok({"items": items}, trace_id=request.state.trace_id)
