from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db
from app.api.response import ok
from app.core.config import settings
from app.core.exceptions import AppError
from app.core.rate_limit import rate_limit
from app.core.security import blacklist_access_token, create_access_token
from app.schemas.auth import (
    AuthOut,
    EmailCodeIn,
    EmailLoginIn,
    LoginIn,
    RegisterIn,
    ResetPasswordIn,
    TokenOut,
)
from app.services.auth_service import (
    REFRESH_COOKIE,
    authenticate_user,
    email_login,
    issue_refresh_token,
    register_user,
    reset_password,
    revoke_refresh_token,
    rotate_refresh_token,
    send_verification_code,
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
async def register(
    body: RegisterIn,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_async_db),
    _limiter: None = Depends(rate_limit("register", 5, 60)),
) -> dict:
    user = await register_user(db, body.phone, body.password, body.nickname, body.privacy_agreed, body.gender)
    access_token, expires_in = create_access_token(user.id, user.role)
    refresh_token = await issue_refresh_token(db, user)
    set_refresh_cookie(response, refresh_token)
    payload = AuthOut(accessToken=access_token, expiresIn=expires_in, user=to_user_out(user))
    return ok(payload.model_dump(by_alias=True), trace_id=request.state.trace_id)


@router.post("/login")
async def login(
    body: LoginIn,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_async_db),
    _limiter: None = Depends(rate_limit("login", 10, 60)),
) -> dict:
    user = await authenticate_user(db, body.phone, body.password)
    user.last_login_at = utcnow_naive()
    await db.commit()
    access_token, expires_in = create_access_token(user.id, user.role)
    refresh_token = await issue_refresh_token(db, user)
    set_refresh_cookie(response, refresh_token)
    payload = AuthOut(accessToken=access_token, expiresIn=expires_in, user=to_user_out(user))
    return ok(payload.model_dump(by_alias=True), trace_id=request.state.trace_id)


@router.post("/refresh")
async def refresh(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_async_db),
    _limiter: None = Depends(rate_limit("refresh", 20, 60)),
) -> dict:
    raw_token = get_refresh_token(request)
    new_token, user = await rotate_refresh_token(db, raw_token)
    access_token, expires_in = create_access_token(user.id, user.role)
    set_refresh_cookie(response, new_token)
    payload = TokenOut(accessToken=access_token, expiresIn=expires_in)
    return ok(payload.model_dump(by_alias=True), trace_id=request.state.trace_id)


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    authorization: str | None = Header(default=None, alias="Authorization"),
    db: AsyncSession = Depends(get_async_db),
) -> Response:
    raw_token = request.cookies.get(REFRESH_COOKIE)
    if raw_token:
        await revoke_refresh_token(db, raw_token)
    if authorization and authorization.lower().startswith("bearer "):
        blacklist_access_token(authorization[7:])
    clear_refresh_cookie(response)
    return Response(status_code=204)


@router.post("/email-code")
async def request_email_code(
    body: EmailCodeIn,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    _limiter: None = Depends(rate_limit("email_code", 10, 300)),
) -> dict:
    await send_verification_code(db, body.email, body.purpose)
    return ok({"message": "验证码已发送"}, trace_id=request.state.trace_id)


@router.post("/email-login")
async def login_by_email(
    body: EmailLoginIn,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_async_db),
    _limiter: None = Depends(rate_limit("email_login", 10, 300)),
) -> dict:
    user = await email_login(db, body.email, body.code)
    access_token, expires_in = create_access_token(user.id, user.role)
    refresh_token = await issue_refresh_token(db, user)
    set_refresh_cookie(response, refresh_token)
    payload = AuthOut(accessToken=access_token, expiresIn=expires_in, user=to_user_out(user))
    return ok(payload.model_dump(by_alias=True), trace_id=request.state.trace_id)


@router.post("/reset-password")
async def reset_password_endpoint(
    body: ResetPasswordIn,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    _limiter: None = Depends(rate_limit("reset_password", 10, 300)),
) -> dict:
    await reset_password(db, body.email, body.code, body.new_password)
    return ok({"message": "密码已重置，请重新登录"}, trace_id=request.state.trace_id)
