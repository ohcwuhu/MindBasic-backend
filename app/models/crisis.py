"""危机处理 SOP：危机标记 + 跟进留痕（检测→值班→回访→结案）。"""

from sqlalchemy import (
    Column,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.mysql import BIGINT, DATETIME

from app.db.base import Base


class CrisisFlag(Base):
    """危机标记：来源 / 等级 / 状态 / 指派。"""

    __tablename__ = "crisis_flags"
    __table_args__ = (
        Index("idx_crisis_user", "user_id", "status", "created_at"),
        Index("idx_crisis_status", "status", "created_at"),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    user_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_crisis_flags_user"),
        nullable=False,
    )
    source = Column(String(24), nullable=False, comment="CHAT/EMOTION_JOURNAL/COMMUNITY/AI_COACH/OTHER")
    level = Column(String(8), nullable=False, server_default="HIGH", comment="HIGH/MEDIUM")
    content = Column(String(500), nullable=False, comment="命中内容快照")
    status = Column(String(16), nullable=False, server_default="OPEN", comment="OPEN/FOLLOWING/RESOLVED")
    assigned_admin_id = Column(BIGINT(unsigned=True), nullable=True, comment="值班处理人")
    resolved_at = Column(DATETIME(fsp=3), nullable=True)
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


class CrisisFollowUp(Base):
    """危机跟进留痕：谁在何时做了什么。"""

    __tablename__ = "crisis_follow_ups"
    __table_args__ = (
        Index("idx_crisis_followups_flag", "crisis_id", "created_at"),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    crisis_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("crisis_flags.id", ondelete="CASCADE", name="fk_crisis_followups_flag"),
        nullable=False,
    )
    actor_id = Column(BIGINT(unsigned=True), nullable=True, comment="操作人（系统事件为空）")
    actor_role = Column(String(16), nullable=False, comment="SYSTEM/ADMIN")
    action = Column(String(16), nullable=False, comment="DETECT/ASSIGN/FOLLOW_UP/RESOLVE/REOPEN")
    note = Column(String(500), nullable=True)
    created_at = Column(
        DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
    )
