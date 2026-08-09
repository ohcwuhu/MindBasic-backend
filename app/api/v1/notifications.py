"""站内通知。"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.response import ok, paginated
from app.models.user import User
from app.services.notification_service import (
    list_notifications,
    mark_all_read,
    mark_read,
    notification_to_out,
    unread_count,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
def my_notifications(
    request: Request,
    unreadOnly: bool = Query(default=False, alias="unreadOnly"),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=20, ge=1, le=50, alias="pageSize"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    rows, total = list_notifications(db, user.id, unreadOnly, page, pageSize)
    items = [notification_to_out(n) for n in rows]
    return ok(paginated(items, total, page, pageSize), trace_id=request.state.trace_id)


@router.get("/unread-count")
def unread(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return ok({"count": unread_count(db, user.id)}, trace_id=request.state.trace_id)


@router.post("/{notification_id}/read")
def read_one(
    notification_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    mark_read(db, user.id, notification_id)
    return ok({"message": "已读"}, trace_id=request.state.trace_id)


@router.post("/read-all")
def read_all(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    count = mark_all_read(db, user.id)
    return ok({"marked": count}, trace_id=request.state.trace_id)
