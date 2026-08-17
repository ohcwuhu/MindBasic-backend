"""管理端：危机处理队列（值班接管 / 跟进 / 结案 / 留痕）。"""

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, require_role
from app.api.response import ok, paginated
from app.core.exceptions import AppError
from app.models.crisis import CrisisFlag
from app.models.user import User
from app.schemas.base import ApiModel
from app.services.audit_service import record_audit
from app.services.crisis_service import (
    add_crisis_follow_up,
    assign_crisis,
    crisis_to_out,
    list_crisis_flags,
    list_crisis_follow_ups,
    resolve_crisis,
)
from app.utils.format import mask_phone


class CrisisNoteIn(ApiModel):
    note: str = Field(min_length=1, max_length=500)


router = APIRouter(prefix="/admin/crisis-flags", tags=["admin-crisis"])


@router.get("")
async def admin_crisis_list(
    request: Request,
    status: str | None = Query(default=None, pattern="^(OPEN|FOLLOWING|RESOLVED)$"),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=10, ge=1, le=50, alias="pageSize"),
    admin: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    rows, total = await list_crisis_flags(db, status, page, pageSize)
    user_ids = {r.user_id for r in rows} | {r.assigned_admin_id for r in rows if r.assigned_admin_id}
    users = {
        u.id: u
        for u in await db.scalars(select(User).where(User.id.in_(user_ids)))
    } if user_ids else {}
    items = []
    for flag in rows:
        out = await crisis_to_out(db, flag, users)
        if out["user"]["phone"]:
            out["user"]["phone"] = mask_phone(out["user"]["phone"])
        items.append(out)
    return ok(paginated(items, total, page, pageSize), trace_id=request.state.trace_id)


@router.get("/{crisis_id}")
async def admin_crisis_detail(
    crisis_id: int,
    request: Request,
    admin: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    flag = await db.get(CrisisFlag, crisis_id)
    if flag is None:
        raise AppError(404, "CRISIS_NOT_FOUND", "危机记录不存在")
    users = {
        u.id: u
        for u in await db.scalars(select(User).where(User.id.in_([flag.user_id, flag.assigned_admin_id or 0])))
    }
    detail = await crisis_to_out(db, flag, users)
    follow_ups = await list_crisis_follow_ups(db, crisis_id, users)
    return ok({"flag": detail, "followUps": follow_ups}, trace_id=request.state.trace_id)


@router.post("/{crisis_id}/assign")
async def admin_crisis_assign(
    crisis_id: int,
    request: Request,
    admin: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    flag = await assign_crisis(db, admin, crisis_id)
    await record_audit(
        db,
        actor_user_id=admin.id,
        actor_role="ADMIN",
        action="ADMIN_CRISIS_ASSIGN",
        target_type="CRISIS",
        target_id=crisis_id,
        ip=request.client.host if request.client else None,
    )
    await db.commit()
    return ok({"id": flag.id, "status": flag.status}, trace_id=request.state.trace_id)


@router.post("/{crisis_id}/follow-up")
async def admin_crisis_follow_up(
    crisis_id: int,
    body: CrisisNoteIn,
    request: Request,
    admin: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    follow_up = await add_crisis_follow_up(db, admin, crisis_id, body.note)
    await record_audit(
        db,
        actor_user_id=admin.id,
        actor_role="ADMIN",
        action="ADMIN_CRISIS_FOLLOW_UP",
        target_type="CRISIS",
        target_id=crisis_id,
        detail={"note": body.note[:200]},
        ip=request.client.host if request.client else None,
    )
    await db.commit()
    return ok({"id": follow_up.id, "action": follow_up.action}, trace_id=request.state.trace_id)


@router.post("/{crisis_id}/resolve")
async def admin_crisis_resolve(
    crisis_id: int,
    body: CrisisNoteIn,
    request: Request,
    admin: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    flag = await resolve_crisis(db, admin, crisis_id, body.note)
    await record_audit(
        db,
        actor_user_id=admin.id,
        actor_role="ADMIN",
        action="ADMIN_CRISIS_RESOLVE",
        target_type="CRISIS",
        target_id=crisis_id,
        detail={"note": body.note[:200]},
        ip=request.client.host if request.client else None,
    )
    await db.commit()
    return ok({"id": flag.id, "status": flag.status}, trace_id=request.state.trace_id)
