"""公开标签目录（教练入驻表单与筛选用）。"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.response import ok
from app.models.coach import Tag

router = APIRouter(prefix="/tags", tags=["tags"])


@router.get("")
def list_tags(
    request: Request,
    type: str | None = Query(default=None, pattern="^(FIELD|AUDIENCE)$"),
    db: Session = Depends(get_db),
) -> dict:
    stmt = (
        select(Tag)
        .where(Tag.is_enabled.is_(True))
        .order_by(Tag.type, Tag.sort_order, Tag.id)
    )
    if type:
        stmt = stmt.where(Tag.type == type)
    items = [
        {"id": t.id, "name": t.name, "type": t.type}
        for t in db.scalars(stmt)
    ]
    return ok({"items": items}, trace_id=request.state.trace_id)
