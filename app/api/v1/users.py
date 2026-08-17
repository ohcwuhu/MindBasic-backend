from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, get_current_user
from app.api.response import ok, paginated
from app.core.exceptions import AppError
from app.core.security import verify_password
from app.models.user import User
from app.schemas.auth import ChangePasswordIn, DeleteAccountIn, EmailBindIn, UserPatchIn
from app.schemas.article import ArticleListOut
from app.schemas.checkin import BadgeOut
from app.services.article_service import article_to_out, list_my_favorites
from app.services.auth_service import to_user_out
from app.services.audit_service import record_audit
from app.services.data_export_service import (
    create_data_export,
    export_path,
    get_export_or_404,
    list_data_exports,
)
from app.services.delete_account_service import delete_account_data
from app.utils.time import to_iso
from app.services.checkin_service import my_badges
from app.services.auth_service import bind_email, change_password, deactivate_account
from app.core.security import blacklist_access_token

router = APIRouter(prefix="/users", tags=["users"])


def _bearer_token(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:]
    return None


@router.get("/me")
async def get_me(request: Request, user: User = Depends(get_current_user)) -> dict:
    return ok(to_user_out(user).model_dump(by_alias=True), trace_id=request.state.trace_id)


@router.patch("/me")
async def patch_me(
    body: UserPatchIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    data = body.model_dump(exclude_unset=True)
    if "nickname" in data:
        user.nickname = data["nickname"]
    if "avatarUrl" in data:
        user.avatar_url = data["avatarUrl"]
    if "gender" in data:
        user.gender = data["gender"]
    await db.commit()
    await db.refresh(user)
    return ok(to_user_out(user).model_dump(by_alias=True), trace_id=request.state.trace_id)


@router.post("/me/email")
async def bind_my_email(
    body: EmailBindIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    updated = await bind_email(db, user, body.email, body.code)
    return ok(to_user_out(updated).model_dump(by_alias=True), trace_id=request.state.trace_id)


@router.get("/me/favorites")
async def my_favorites(
    request: Request,
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=10, ge=1, le=50, alias="pageSize"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    rows, total = await list_my_favorites(db, user.id, page, pageSize)
    items = [ArticleListOut(**article_to_out(a, set([a.id]))).model_dump(by_alias=True) for a in rows]
    return ok(paginated(items, total, page, pageSize), trace_id=request.state.trace_id)


@router.post("/me/password")
async def change_my_password(
    body: ChangePasswordIn,
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    await change_password(db, user, body.old_password, body.new_password)
    token = _bearer_token(authorization)
    if token:
        blacklist_access_token(token)
    return ok({"message": "密码已修改"}, trace_id=request.state.trace_id)


@router.post("/me/deactivate")
async def deactivate_me(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    await deactivate_account(db, user)
    token = _bearer_token(authorization)
    if token:
        blacklist_access_token(token)
    return ok({"message": "账号已注销"}, trace_id=request.state.trace_id)


@router.post("/me/delete")
async def delete_me(
    body: DeleteAccountIn,
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    """注销删除（被遗忘权）：删除/匿名化个人数据，财务与履约记录保留。"""
    if not verify_password(body.password, user.password_hash):
        raise AppError(401, "CREDENTIAL_INVALID", "密码错误")
    await delete_account_data(db, user, ip=request.client.host if request.client else None)
    token = _bearer_token(authorization)
    if token:
        blacklist_access_token(token)
    return ok({"message": "账号已删除，个人数据已清除"}, trace_id=request.state.trace_id)


@router.post("/me/data-export", status_code=201)
async def create_my_data_export(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    record = await create_data_export(db, user)
    await record_audit(
        db,
        actor_user_id=user.id,
        actor_role="USER",
        action="USER_DATA_EXPORT",
        target_type="USER",
        target_id=user.id,
        ip=request.client.host if request.client else None,
    )
    await db.commit()
    return ok(
        {
            "id": record.id,
            "status": record.status,
            "format": record.format,
            "expiresAt": to_iso(record.expires_at),
        },
        trace_id=request.state.trace_id,
    )


@router.get("/me/data-exports")
async def my_data_exports(
    request: Request,
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=10, ge=1, le=50, alias="pageSize"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    items, total = await list_data_exports(db, user.id, page, pageSize)
    return ok(paginated(items, total, page, pageSize), trace_id=request.state.trace_id)


@router.get("/me/data-exports/{export_id}/download")
async def download_my_data_export(
    export_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> FileResponse:
    record = await get_export_or_404(db, user.id, export_id)
    path = export_path(record)
    return FileResponse(
        path,
        media_type="application/json",
        filename=f"mindbasic-data-export-{record.id}.json",
    )


@router.get("/me/badges")
async def my_badges_endpoint(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    items = [BadgeOut(**b).model_dump(by_alias=True) for b in await my_badges(db, user.id)]
    return ok({"items": items}, trace_id=request.state.trace_id)
