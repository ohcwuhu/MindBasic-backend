"""密码哈希与 JWT 令牌工具。"""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.core.config import settings


def hash_password(password: str) -> str:
    """bcrypt 哈希。"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(user_id: int, role: str) -> tuple[str, int]:
    """签发 Access Token，返回 (token, 有效期秒数)。"""
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {
        "sub": str(user_id),
        "role": role,
        "type": "access",
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": expire,
    }
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return token, settings.access_token_expire_minutes * 60


def decode_access_token(token: str) -> dict:
    """解码并校验 Access Token，失败时抛出 jwt 异常。"""
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])


def generate_refresh_token() -> str:
    """生成随机 Refresh Token 明文。"""
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    """Refresh Token 只存 SHA-256 哈希。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
