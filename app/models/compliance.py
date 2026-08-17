"""数据合规：审计日志 + 数据导出（被遗忘权 / 审计闭环）。"""

from sqlalchemy import (
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, JSON

from app.db.base import Base


class AuditLog(Base):
    """敏感操作审计日志（用户导出/删除、管理员退款/发放等）。"""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("idx_audit_actor", "actor_user_id", "created_at"),
        Index("idx_audit_target", "target_type", "target_id"),
        Index("idx_audit_action", "action", "created_at"),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    actor_user_id = Column(BIGINT(unsigned=True), nullable=True, comment="操作人用户ID（系统操作为空）")
    actor_role = Column(String(16), nullable=False, comment="USER/COACH/ADMIN/SYSTEM")
    action = Column(String(64), nullable=False, comment="USER_DATA_EXPORT/USER_DELETE/ADMIN_ORDER_REFUND/...")
    target_type = Column(String(32), nullable=False, comment="USER/ORDER/APPOINTMENT/CASE/...")
    target_id = Column(BIGINT(unsigned=True), nullable=True)
    detail = Column(JSON, nullable=True, comment="补充快照")
    ip = Column(String(64), nullable=True)
    created_at = Column(
        DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
    )


class DataExport(Base):
    """用户数据导出记录（保留 7 天，私有下载）。"""

    __tablename__ = "data_exports"
    __table_args__ = (
        Index("idx_data_exports_user", "user_id", "created_at"),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    user_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_data_exports_user"),
        nullable=False,
    )
    status = Column(String(16), nullable=False, server_default="READY", comment="READY/EXPIRED/FAILED")
    format = Column(String(8), nullable=False, server_default="JSON")
    file_id = Column(String(64), nullable=True, comment="导出文件名（uuid.json）")
    size = Column(Integer, nullable=True)
    expires_at = Column(DATETIME(fsp=3), nullable=True, comment="下载截止时间")
    created_at = Column(
        DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
    )
