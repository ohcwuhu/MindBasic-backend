"""认证业务逻辑：用户校验、令牌签发与刷新。"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppError
from app.core.security import (
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.models.user import RefreshToken, User
from app.models.email_code import EmailVerificationCode
from app.schemas.auth import UserOut
from app.services import email_service
from app.utils.time import utcnow_naive
from app.utils.format import mask_phone

REFRESH_COOKIE = "refresh_token"

EMAIL_SUBJECTS = {
    "LOGIN": "MindBasic 登录验证码",
    "RESET": "MindBasic 找回密码验证码",
    "BIND": "MindBasic 绑定邮箱验证码",
}
EMAIL_PURPOSES = frozenset(EMAIL_SUBJECTS)


def _normalize_email(email: str) -> str:
    return email.strip().lower()


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    stmt = select(User).where(User.email == email, User.deleted_at.is_(None))
    return await db.scalar(stmt)


async def send_verification_code(db: AsyncSession, email: str, purpose: str) -> str:
    """发送验证码并返回明文（仅供日志/测试；生产关闭邮件时会打印）。"""
    email = _normalize_email(email)
    now = utcnow_naive()
    latest = await db.scalar(
        select(EmailVerificationCode)
        .where(
            EmailVerificationCode.email == email,
            EmailVerificationCode.purpose == purpose,
        )
        .order_by(EmailVerificationCode.created_at.desc())
        .limit(1)
    )
    if latest is not None and latest.created_at > now - timedelta(seconds=60):
        raise AppError(429, "RATE_LIMITED", "发送过于频繁，请稍后再试")
    code = email_service.generate_code()
    record = EmailVerificationCode(
        email=email,
        purpose=purpose,
        code_hash=email_service.hash_code(code),
        expires_at=now + timedelta(minutes=settings.email_code_ttl_minutes),
        created_at=now,
    )
    db.add(record)
    await db.commit()
    email_service.send_email(
        email,
        EMAIL_SUBJECTS[purpose],
        f"你的验证码是 {code}，{settings.email_code_ttl_minutes} 分钟内有效。如非本人操作请忽略。",
    )
    return code


async def verify_code(db: AsyncSession, email: str, purpose: str, code: str) -> None:
    """校验验证码：错误尝试累计 5 次即作废，成功则一次性消费。"""
    email = _normalize_email(email)
    now = utcnow_naive()
    record = await db.scalar(
        select(EmailVerificationCode)
        .where(
            EmailVerificationCode.email == email,
            EmailVerificationCode.purpose == purpose,
            EmailVerificationCode.consumed_at.is_(None),
        )
        .order_by(EmailVerificationCode.created_at.desc())
        .limit(1)
    )
    if record is None or record.expires_at < now:
        raise AppError(401, "CODE_INVALID", "验证码错误或已过期")
    if record.attempts >= 5:
        record.consumed_at = now
        await db.commit()
        raise AppError(401, "CODE_INVALID", "验证码错误次数过多，请重新获取")
    if record.code_hash != email_service.hash_code(code):
        record.attempts += 1
        await db.commit()
        raise AppError(401, "CODE_INVALID", "验证码错误或已过期")
    record.consumed_at = now
    await db.commit()


def to_user_out(user: User) -> UserOut:
    created_at = user.created_at.isoformat()
    if not created_at.endswith("Z") and "+" not in created_at:
        created_at += "Z"
    return UserOut(
        id=user.id,
        phone=mask_phone(user.phone),
        email=user.email,
        nickname=user.nickname,
        avatarUrl=user.avatar_url,
        role=user.role,  # type: ignore[arg-type]
        isDisabled=user.status != "ENABLED",
        createdAt=created_at,
    )


async def get_user_by_phone(db: AsyncSession, phone: str) -> User | None:
    stmt = select(User).where(User.phone == phone, User.deleted_at.is_(None))
    return await db.scalar(stmt)


async def register_user(db: AsyncSession, phone: str, password: str, nickname: str, privacy_agreed: bool) -> User:
    if not privacy_agreed:
        raise AppError(400, "VALIDATION_ERROR", "请先阅读并同意隐私政策")
    if await get_user_by_phone(db, phone) is not None:
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
    await db.commit()
    await db.refresh(user)
    return user


async def authenticate_user(db: AsyncSession, phone: str, password: str) -> User:
    user = await get_user_by_phone(db, phone)
    if user is None or not verify_password(password, user.password_hash):
        raise AppError(401, "CREDENTIAL_INVALID", "手机号或密码错误")
    if user.status != "ENABLED":
        raise AppError(403, "ACCOUNT_DISABLED", "账号已被禁用")
    return user


async def issue_refresh_token(db: AsyncSession, user: User) -> str:
    token = generate_refresh_token()
    db.add(RefreshToken(
        user_id=user.id,
        token_hash=hash_refresh_token(token),
        expires_at=utcnow_naive() + timedelta(days=settings.refresh_token_expire_days),
    ))
    await db.commit()
    return token


async def rotate_refresh_token(db: AsyncSession, raw_token: str) -> tuple[str, User]:
    """校验并轮换 Refresh Token，返回 (新 token, 用户)；失败抛 401。"""
    token_hash = hash_refresh_token(raw_token)
    record = await db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    now = utcnow_naive()
    if record is None or record.revoked_at is not None or record.expires_at < now:
        raise AppError(401, "UNAUTHORIZED", "登录状态已失效，请重新登录")
    user = await db.get(User, record.user_id)
    if user is None or user.deleted_at is not None or user.status != "ENABLED":
        raise AppError(401, "UNAUTHORIZED", "账号不可用，请重新登录")

    new_token = generate_refresh_token()
    record.revoked_at = now
    db.add(RefreshToken(
        user_id=user.id,
        token_hash=hash_refresh_token(new_token),
        expires_at=utcnow_naive() + timedelta(days=settings.refresh_token_expire_days),
    ))
    await db.commit()
    return new_token, user


async def revoke_refresh_token(db: AsyncSession, raw_token: str) -> None:
    record = await db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(raw_token)))
    if record is not None and record.revoked_at is None:
        record.revoked_at = utcnow_naive()
        await db.commit()


async def change_password(db: AsyncSession, user: User, old_password: str, new_password: str) -> None:
    if not verify_password(old_password, user.password_hash):
        raise AppError(401, "CREDENTIAL_INVALID", "原密码错误")
    user.password_hash = hash_password(new_password)
    now = utcnow_naive()
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await db.commit()


async def deactivate_account(db: AsyncSession, user: User) -> None:
    """注销账号：软删除 + 释放手机号 + 吊销全部刷新令牌。"""
    user.deleted_at = utcnow_naive()
    user.phone = f"del_{user.id}"
    now = utcnow_naive()
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await db.commit()


async def email_login(db: AsyncSession, email: str, code: str) -> User:
    """邮箱验证码登录（仅限已绑定邮箱的账号）。"""
    await verify_code(db, email, "LOGIN", code)
    user = await get_user_by_email(db, _normalize_email(email))
    if user is None:
        raise AppError(404, "ACCOUNT_NOT_FOUND", "该邮箱未绑定账号")
    if user.status != "ENABLED":
        raise AppError(403, "ACCOUNT_DISABLED", "账号已被禁用")
    user.last_login_at = utcnow_naive()
    await db.commit()
    return user


async def reset_password(db: AsyncSession, email: str, code: str, new_password: str) -> None:
    """邮箱验证码找回密码：校验后重置并吊销旧刷新令牌。"""
    await verify_code(db, email, "RESET", code)
    user = await get_user_by_email(db, _normalize_email(email))
    if user is None:
        raise AppError(404, "ACCOUNT_NOT_FOUND", "该邮箱未绑定账号")
    user.password_hash = hash_password(new_password)
    now = utcnow_naive()
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await db.commit()


async def bind_email(db: AsyncSession, user: User, email: str, code: str) -> User:
    """登录用户绑定（或换绑）邮箱。"""
    await verify_code(db, email, "BIND", code)
    normalized = _normalize_email(email)
    existing = await get_user_by_email(db, normalized)
    if existing is not None and existing.id != user.id:
        raise AppError(409, "CONFLICT", "该邮箱已被其他账号绑定")
    user.email = normalized
    await db.commit()
    await db.refresh(user)
    return user
