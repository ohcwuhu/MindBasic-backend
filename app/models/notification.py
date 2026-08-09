"""站内通知。"""

from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.mysql import BIGINT, DATETIME

from app.db.base import Base


class Notification(Base):
    """站内消息：预约状态、审核结果等。"""

    __tablename__ = "notifications"
    __table_args__ = (
        Index("idx_notifications_user", "user_id", "is_read"),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    user_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_notifications_user"),
        nullable=False,
    )
    type = Column(String(32), nullable=False, comment="APPOINTMENT/AUDIT/SYSTEM")
    title = Column(String(64), nullable=False)
    content = Column(String(255), nullable=False)
    is_read = Column(Boolean, nullable=False, server_default=text("0"))
    created_at = Column(DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)"))
