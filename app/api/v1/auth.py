from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.response import ok
from app.core.config import settings
from app.core.exceptions import AppError
from app.core.rate_limit import rate_limit
from app.core.security import create_access_token
from app.schemas.auth import AuthOut, LoginIn, RegisterIn, TokenOut
from app.services.auth_service import (
    REFRESH_COOKIE,
    authenticate_user,
    issue_refresh_token,
    register_user,
    revoke_refresh_token,
    rotate_refresh_token,
    to_user_out,
    utcnow_naive,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=token,
        max_age=settings.refresh_token_expire_days * 24 * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/api/v1/auth",
    )


def clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, path="/api/v1/auth")


def get_refresh_token(request: Request) -> str:
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise AppError(401, "UNAUTHORIZED", "登录状态已失效，请重新登录")
    return token


@router.post("/register", status_code=201)
def register(
    body: RegisterIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    _limiter: None = Depends(rate_limit("register", 5, 60)),
) -> dict:
    user = register_user(db, body.phone, body.password, body.nickname, body.privacy_agreed)
    access_token, expires_in = create_access_token(user.id, user.role)
    refresh_token = issue_refresh_token(db, user)
    set_refresh_cookie(response, refresh_token)
    payload = AuthOut(accessToken=access_token, expiresIn=expires_in, user=to_user_out(user))
    return ok(payload.model_dump(by_alias=True), trace_id=request.state.trace_id)


@router.post("/login")
def login(
    body: LoginIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    _limiter: None = Depends(rate_limit("login", 10, 60)),
) -> dict:
    user = authenticate_user(db, body.phone, body.password)
    user.last_login_at = utcnow_naive()
    db.commit()
    access_token, expires_in = create_access_token(user.id, user.role)
    refresh_token = issue_refresh_token(db, user)
    set_refresh_cookie(response, refresh_token)
    payload = AuthOut(accessToken=access_token, expiresIn=expires_in, user=to_user_out(user))
    return ok(payload.model_dump(by_alias=True), trace_id=request.state.trace_id)


@router.post("/refresh")
def refresh(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    _limiter: None = Depends(rate_limit("refresh", 20, 60)),
) -> dict:
    raw_token = get_refresh_token(request)
    new_token, user = rotate_refresh_token(db, raw_token)
    access_token, expires_in = create_access_token(user.id, user.role)
    set_refresh_cookie(response, new_token)
    payload = TokenOut(accessToken=access_token, expiresIn=expires_in)
    return ok(payload.model_dump(by_alias=True), trace_id=request.state.trace_id)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> Response:
    raw_token = request.cookies.get(REFRESH_COOKIE)
    if raw_token:
        revoke_refresh_token(db, raw_token)
    clear_refresh_cookie(response)
    return Response(status_code=204)
