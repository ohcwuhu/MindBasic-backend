"""管理端：审计日志查询。"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, require_role
from app.api.response import ok, paginated
from app.models.user import User
from app.services.audit_service import list_audit_logs
from app.utils.time import to_iso

router = APIRouter(prefix="/admin/audit-logs", tags=["admin-audit-logs"])


@router.get("")
async def admin_audit_logs(
    request: Request,
    actorUserId: int | None = Query(default=None, alias="actorUserId"),
    action: str | None = Query(default=None),
    targetType: str | None = Query(default=None, alias="targetType"),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    admin: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    rows, total = await list_audit_logs(
        db,
        actor_user_id=actorUserId,
        action=action,
        target_type=targetType,
        page=page,
        page_size=pageSize,
    )
    actor_ids = {r.actor_user_id for r in rows if r.actor_user_id is not None}
    actors = {
        u.id: u
        for u in await db.scalars(select(User).where(User.id.in_(actor_ids)))
    } if actor_ids else {}
    items = [
        {
            "id": r.id,
            "actorUserId": r.actor_user_id,
            "actorRole": r.actor_role,
            "actorNickname": actors[r.actor_user_id].nickname if r.actor_user_id in actors else "",
            "action": r.action,
            "targetType": r.target_type,
            "targetId": r.target_id,
            "detail": r.detail,
            "ip": r.ip,
            "createdAt": to_iso(r.created_at),
        }
        for r in rows
    ]
    return ok(paginated(items, total, page, pageSize), trace_id=request.state.trace_id)
