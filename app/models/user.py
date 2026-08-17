from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, JSON

from app.db.base import Base


class User(Base):
    """平台账号表"""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("phone", name="uq_users_phone"),
        UniqueConstraint("email", name="uq_users_email"),
        Index("idx_users_role_status", "role", "status"),
        CheckConstraint(
            "role IN ('USER','COACH','ADMIN')",
            name="chk_users_role",
        ),
        CheckConstraint(
            "status IN ('ENABLED','DISABLED')",
            name="chk_users_status",
        ),
        CheckConstraint(
            "gender IN ('boy','girl')",
            name="chk_users_gender",
        ),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    phone = Column(String(20), nullable=False, comment="手机号，唯一")
    email = Column(String(255), nullable=True, comment="邮箱，唯一（可空）")
    password_hash = Column(String(255), nullable=False, comment="bcrypt 哈希")
    nickname = Column(String(32), nullable=False, comment="昵称")
    gender = Column(
        String(8),
        nullable=False,
        server_default="girl",
        comment="陪伴角色性别：boy=小男孩 / girl=小女孩",
    )
    avatar_url = Column(String(512), nullable=True, comment="头像")
    role = Column(String(16), nullable=False, server_default="USER", comment="USER/COACH/ADMIN")
    status = Column(String(16), nullable=False, server_default="ENABLED", comment="ENABLED/DISABLED")
    privacy_agreed = Column(Boolean, nullable=False, server_default=text("0"), comment="是否同意隐私政策")
    agreement_version = Column(String(16), nullable=True, comment="已同意服务协议版本")
    agreement_accepted_at = Column(DATETIME(fsp=3), nullable=True, comment="同意服务协议时间")
    last_login_at = Column(DATETIME(fsp=3), nullable=True, comment="最后登录(UTC)")
    deleted_at = Column(DATETIME(fsp=3), nullable=True, comment="软删除(UTC)")
    created_at = Column(
        DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
    )
    updated_at = Column(
        DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
        onupdate=text("CURRENT_TIMESTAMP(3)"),
    )


class RefreshToken(Base):
    """刷新令牌（存储哈希）"""

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_refresh_tokens_hash"),
        Index("idx_refresh_tokens_user", "user_id"),
        Index("idx_refresh_tokens_expires", "expires_at"),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    user_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_refresh_tokens_user"),
        nullable=False,
    )
    token_hash = Column(String(64), nullable=False, comment="SHA-256 hex")
    expires_at = Column(DATETIME(fsp=3), nullable=False, comment="过期时间(UTC)")
    revoked_at = Column(DATETIME(fsp=3), nullable=True, comment="吊销时间(UTC)")
    created_at = Column(
        DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
    )


class AdminActionLog(Base):
    """管理操作审计日志"""

    __tablename__ = "admin_action_logs"
    __table_args__ = (
        Index("idx_admin_logs_admin", "admin_id", "created_at"),
        Index("idx_admin_logs_target", "target_type", "target_id"),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    admin_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_admin_logs_admin"),
        nullable=False,
    )
    action = Column(String(64), nullable=False, comment="COACH_AUDIT_APPROVE/USER_DISABLE/...")
    target_type = Column(String(32), nullable=False, comment="USER/COACH/AUDIT/ARTICLE/...")
    target_id = Column(BIGINT(unsigned=True), nullable=False)
    detail = Column(JSON, nullable=True, comment="变更前后快照")
    created_at = Column(
        DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
    )
