from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.response import ok, paginated
from app.models.user import User
from app.schemas.auth import ChangePasswordIn, UserPatchIn
from app.schemas.article import ArticleListOut
from app.schemas.checkin import BadgeOut
from app.services.article_service import article_to_out, list_my_favorites
from app.services.auth_service import to_user_out
from app.services.checkin_service import my_badges
from app.services.auth_service import change_password, deactivate_account
from app.core.security import blacklist_access_token

router = APIRouter(prefix="/users", tags=["users"])


def _bearer_token(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:]
    return None


@router.get("/me")
def get_me(request: Request, user: User = Depends(get_current_user)) -> dict:
    return ok(to_user_out(user).model_dump(by_alias=True), trace_id=request.state.trace_id)


@router.patch("/me")
def patch_me(
    body: UserPatchIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    data = body.model_dump(exclude_unset=True)
    if "nickname" in data:
        user.nickname = data["nickname"]
    if "avatarUrl" in data:
        user.avatar_url = data["avatarUrl"]
    db.commit()
    db.refresh(user)
    return ok(to_user_out(user).model_dump(by_alias=True), trace_id=request.state.trace_id)


@router.get("/me/favorites")
def my_favorites(
    request: Request,
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=10, ge=1, le=50, alias="pageSize"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    rows, total = list_my_favorites(db, user.id, page, pageSize)
    items = [ArticleListOut(**article_to_out(a, set([a.id]))).model_dump(by_alias=True) for a in rows]
    return ok(paginated(items, total, page, pageSize), trace_id=request.state.trace_id)


@router.post("/me/password")
def change_my_password(
    body: ChangePasswordIn,
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    change_password(db, user, body.old_password, body.new_password)
    token = _bearer_token(authorization)
    if token:
        blacklist_access_token(token)
    return ok({"message": "密码已修改"}, trace_id=request.state.trace_id)


@router.post("/me/deactivate")
def deactivate_me(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    deactivate_account(db, user)
    token = _bearer_token(authorization)
    if token:
        blacklist_access_token(token)
    return ok({"message": "账号已注销"}, trace_id=request.state.trace_id)


@router.get("/me/badges")
def my_badges_endpoint(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    items = [BadgeOut(**b).model_dump(by_alias=True) for b in my_badges(db, user.id)]
    return ok({"items": items}, trace_id=request.state.trace_id)
