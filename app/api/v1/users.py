from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.response import ok
from app.models.user import User
from app.schemas.auth import UserPatchIn
from app.services.auth_service import to_user_out

router = APIRouter(prefix="/users", tags=["users"])


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
