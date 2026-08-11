"""FastAPI 依赖注入：数据库会话、当前用户、角色校验。"""

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.core.security import decode_access_token
from app.core.token_blacklist import is_blacklisted
from app.db.session import AsyncSessionLocal, SessionLocal
from app.models.coach import CoachProfile
from app.models.user import User

bearer_scheme = HTTPBearer(auto_error=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_async_db():
    """异步会话依赖：给已迁移为 async 的路由使用。"""
    db = AsyncSessionLocal()
    try:
        yield db
    finally:
        await db.close()


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise AppError(401, "UNAUTHORIZED", "请先登录")
    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.ExpiredSignatureError:
        raise AppError(401, "TOKEN_EXPIRED", "登录已过期，请刷新")
    except jwt.InvalidTokenError:
        raise AppError(401, "UNAUTHORIZED", "登录凭证无效")
    if is_blacklisted(payload.get("jti", "")):
        raise AppError(401, "TOKEN_EXPIRED", "登录已失效，请重新登录")

    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        raise AppError(401, "UNAUTHORIZED", "登录凭证无效")
    user = db.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise AppError(401, "UNAUTHORIZED", "账号不存在")
    if user.status != "ENABLED":
        raise AppError(403, "ACCOUNT_DISABLED", "账号已被禁用")
    request.state.user_id = user.id
    return user


def get_optional_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User | None:
    """可选登录态：带有效 Token 返回用户，否则返回 None（不抛错）。"""
    if credentials is None:
        return None
    try:
        payload = decode_access_token(credentials.credentials)
        if is_blacklisted(payload.get("jti", "")):
            return None
        user_id = int(payload["sub"])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, KeyError, TypeError, ValueError):
        return None
    user = db.get(User, user_id)
    if user is None or user.deleted_at is not None or user.status != "ENABLED":
        return None
    request.state.user_id = user.id
    return user


def get_current_coach(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CoachProfile:
    """要求当前用户是已审核通过的教练，返回其教练资料。"""
    profile = db.scalar(select(CoachProfile).where(CoachProfile.user_id == user.id))
    if profile is None or profile.audit_status != "APPROVED":
        raise AppError(403, "COACH_NOT_APPROVED", "请先完成教练入驻并通过审核")
    return profile


def require_role(*roles: str):
    """返回一个依赖：要求当前用户属于指定角色。"""

    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise AppError(403, "FORBIDDEN", "无权访问该资源")
        return user

    return checker
