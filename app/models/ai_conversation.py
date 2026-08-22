"""AI 自我教练对话持久化：会话 + 消息（记录可回看/继续）。"""

from sqlalchemy import (
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, JSON

from app.db.base import Base


class AiConversation(Base):
    """AI 自我教练会话（一轮完整视频通话）。"""

    __tablename__ = "ai_conversations"
    __table_args__ = (
        Index("idx_ai_conv_user", "user_id", "status", "created_at"),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    user_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_ai_conv_user"),
        nullable=False,
    )
    title = Column(String(100), nullable=False, server_default="自我教练对话")
    status = Column(String(16), nullable=False, server_default="ACTIVE", comment="ACTIVE/ENDED")
    message_count = Column(Integer, nullable=False, server_default=text("0"))
    ended_at = Column(DATETIME(fsp=3), nullable=True)
    journal_id = Column(BIGINT(unsigned=True), nullable=True, comment="由本对话生成的情绪日记 ID")
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


class AiMessage(Base):
    """AI 自我教练会话消息。"""

    __tablename__ = "ai_messages"
    __table_args__ = (
        Index("idx_ai_msg_conv", "conversation_id", "created_at"),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    conversation_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("ai_conversations.id", ondelete="CASCADE", name="fk_ai_msg_conv"),
        nullable=False,
    )
    role = Column(String(16), nullable=False, comment="USER/ASSISTANT")
    content = Column(Text, nullable=False)
    emotion = Column(JSON, nullable=True, comment="该轮情绪上下文快照")
    created_at = Column(
        DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
    )
