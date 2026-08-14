"""线上聊天（用户—教练）数据模型。"""

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import BIGINT, DATETIME

from app.db.base import Base


class ChatConversation(Base):
    """用户与教练的一对一会话。"""

    __tablename__ = "chat_conversations"
    __table_args__ = (
        UniqueConstraint("user_id", "coach_id", name="uq_chat_conversations_pair"),
        Index("idx_chat_conversations_user", "user_id", "last_message_at"),
        Index("idx_chat_conversations_coach", "coach_id", "last_message_at"),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    user_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_chat_conversations_user"),
        nullable=False,
        comment="普通用户",
    )
    coach_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("coach_profiles.id", ondelete="CASCADE", name="fk_chat_conversations_coach"),
        nullable=False,
        comment="教练资料",
    )
    last_message_preview = Column(String(255), nullable=True, comment="最后一条消息预览")
    last_message_at = Column(DATETIME(fsp=3), nullable=True, comment="最后消息时间(UTC)")
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


class ChatMessage(Base):
    """会话消息。"""

    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("idx_chat_messages_conv", "conversation_id", "created_at"),
        Index("idx_chat_messages_unread", "conversation_id", "sender_id", "read_at"),
        CheckConstraint(
            "sender_role IN ('USER','COACH')",
            name="chk_chat_messages_role",
        ),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    conversation_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("chat_conversations.id", ondelete="CASCADE", name="fk_chat_messages_conv"),
        nullable=False,
    )
    sender_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_chat_messages_sender"),
        nullable=False,
    )
    sender_role = Column(String(16), nullable=False, comment="USER/COACH")
    content = Column(Text, nullable=False, comment="消息内容")
    read_at = Column(DATETIME(fsp=3), nullable=True, comment="接收方已读时间(UTC)")
    created_at = Column(
        DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
    )
