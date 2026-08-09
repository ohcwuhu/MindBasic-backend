"""FastAPI 依赖注入：数据库会话、当前用户、角色校验。"""

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.core.security import decode_access_token
from app.db.session import SessionLocal
from app.models.user import User

bearer_scheme = HTTPBearer(auto_error=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
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

    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        raise AppError(401, "UNAUTHORIZED", "登录凭证无效")
    user = db.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise AppError(401, "UNAUTHORIZED", "账号不存在")
    if user.status != "ENABLED":
        raise AppError(403, "ACCOUNT_DISABLED", "账号已被禁用")
    return user


def require_role(*roles: str):
    """返回一个依赖：要求当前用户属于指定角色。"""

    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise AppError(403, "FORBIDDEN", "无权访问该资源")
        return user

    return checker
