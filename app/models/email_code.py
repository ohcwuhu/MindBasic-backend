"""邮箱验证码（登录 / 找回密码 / 绑定邮箱）。"""

from sqlalchemy import Column, Index, Integer, String, desc, text
from sqlalchemy.dialects.mysql import BIGINT, DATETIME

from app.db.base import Base


class EmailVerificationCode(Base):
    """邮箱验证码记录（哈希存储、一次性消费、防爆破）。"""

    __tablename__ = "email_verification_codes"
    __table_args__ = (
        Index("idx_email_codes_lookup", "email", "purpose", desc("created_at")),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    email = Column(String(255), nullable=False)
    purpose = Column(String(16), nullable=False, comment="LOGIN/RESET/BIND")
    code_hash = Column(String(64), nullable=False, comment="SHA-256")
    attempts = Column(Integer, nullable=False, server_default=text("0"), comment="错误尝试次数")
    expires_at = Column(DATETIME(fsp=3), nullable=False)
    consumed_at = Column(DATETIME(fsp=3), nullable=True)
    created_at = Column(DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)"))
