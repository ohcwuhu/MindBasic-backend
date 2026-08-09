"""认证业务逻辑：用户校验、令牌签发与刷新。"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import AppError
from app.core.security import (
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.models.user import RefreshToken, User
from app.schemas.auth import UserOut
from app.utils.time import utcnow_naive

REFRESH_COOKIE = "refresh_token"


def mask_phone(phone: str) -> str:
    if len(phone) == 11:
        return phone[:3] + "****" + phone[-4:]
    return phone


def to_user_out(user: User) -> UserOut:
    created_at = user.created_at.isoformat()
    if not created_at.endswith("Z") and "+" not in created_at:
        created_at += "Z"
    return UserOut(
        id=user.id,
        phone=mask_phone(user.phone),
        nickname=user.nickname,
        avatarUrl=user.avatar_url,
        role=user.role,  # type: ignore[arg-type]
        isDisabled=user.status != "ENABLED",
        createdAt=created_at,
    )


def get_user_by_phone(db: Session, phone: str) -> User | None:
    stmt = select(User).where(User.phone == phone, User.deleted_at.is_(None))
    return db.scalar(stmt)


def register_user(db: Session, phone: str, password: str, nickname: str, privacy_agreed: bool) -> User:
    if not privacy_agreed:
        raise AppError(400, "VALIDATION_ERROR", "请先阅读并同意隐私政策")
    if get_user_by_phone(db, phone) is not None:
        raise AppError(409, "PHONE_EXISTS", "该手机号已注册")
    user = User(
        phone=phone,
        password_hash=hash_password(password),
        nickname=nickname,
        role="USER",
        status="ENABLED",
        privacy_agreed=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, phone: str, password: str) -> User:
    user = get_user_by_phone(db, phone)
    if user is None or not verify_password(password, user.password_hash):
        raise AppError(401, "CREDENTIAL_INVALID", "手机号或密码错误")
    if user.status != "ENABLED":
        raise AppError(403, "ACCOUNT_DISABLED", "账号已被禁用")
    return user


def issue_refresh_token(db: Session, user: User) -> str:
    token = generate_refresh_token()
    db.add(RefreshToken(
        user_id=user.id,
        token_hash=hash_refresh_token(token),
        expires_at=utcnow_naive() + timedelta(days=settings.refresh_token_expire_days),
    ))
    db.commit()
    return token


def rotate_refresh_token(db: Session, raw_token: str) -> tuple[str, User]:
    """校验并轮换 Refresh Token，返回 (新 token, 用户)；失败抛 401。"""
    token_hash = hash_refresh_token(raw_token)
    record = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    now = utcnow_naive()
    if record is None or record.revoked_at is not None or record.expires_at < now:
        raise AppError(401, "UNAUTHORIZED", "登录状态已失效，请重新登录")
    user = db.get(User, record.user_id)
    if user is None or user.deleted_at is not None or user.status != "ENABLED":
        raise AppError(401, "UNAUTHORIZED", "账号不可用，请重新登录")

    new_token = generate_refresh_token()
    record.revoked_at = now
    db.add(RefreshToken(
        user_id=user.id,
        token_hash=hash_refresh_token(new_token),
        expires_at=utcnow_naive() + timedelta(days=settings.refresh_token_expire_days),
    ))
    db.commit()
    return new_token, user


def revoke_refresh_token(db: Session, raw_token: str) -> None:
    record = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(raw_token)))
    if record is not None and record.revoked_at is None:
        record.revoked_at = utcnow_naive()
        db.commit()
