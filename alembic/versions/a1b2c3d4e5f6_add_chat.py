"""add chat tables

Revision ID: a1b2c3d4e5f6
Revises: e5d6f7a8b9c0
Create Date: 2026-08-15
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import BIGINT, DATETIME


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "e5d6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create chat tables."""
    op.create_table(
        "chat_conversations",
        sa.Column("id", BIGINT(unsigned=True), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            BIGINT(unsigned=True),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_chat_conversations_user"),
            nullable=False,
        ),
        sa.Column(
            "coach_id",
            BIGINT(unsigned=True),
            sa.ForeignKey("coach_profiles.id", ondelete="CASCADE", name="fk_chat_conversations_coach"),
            nullable=False,
        ),
        sa.Column("last_message_preview", sa.String(255), nullable=True),
        sa.Column("last_message_at", DATETIME(fsp=3), nullable=True),
        sa.Column(
            "created_at",
            DATETIME(fsp=3),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
        ),
        sa.Column(
            "updated_at",
            DATETIME(fsp=3),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
            onupdate=sa.text("CURRENT_TIMESTAMP(3)"),
        ),
        sa.UniqueConstraint("user_id", "coach_id", name="uq_chat_conversations_pair"),
        sa.Index("idx_chat_conversations_user", "user_id", "last_message_at"),
        sa.Index("idx_chat_conversations_coach", "coach_id", "last_message_at"),
        mysql_charset="utf8mb4",
    )
    op.create_table(
        "chat_messages",
        sa.Column("id", BIGINT(unsigned=True), primary_key=True, autoincrement=True),
        sa.Column(
            "conversation_id",
            BIGINT(unsigned=True),
            sa.ForeignKey("chat_conversations.id", ondelete="CASCADE", name="fk_chat_messages_conv"),
            nullable=False,
        ),
        sa.Column(
            "sender_id",
            BIGINT(unsigned=True),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_chat_messages_sender"),
            nullable=False,
        ),
        sa.Column("sender_role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("read_at", DATETIME(fsp=3), nullable=True),
        sa.Column(
            "created_at",
            DATETIME(fsp=3),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
        ),
        sa.CheckConstraint(
            "sender_role IN ('USER','COACH')",
            name="chk_chat_messages_role",
        ),
        sa.Index("idx_chat_messages_conv", "conversation_id", "created_at"),
        sa.Index("idx_chat_messages_unread", "conversation_id", "sender_id", "read_at"),
        mysql_charset="utf8mb4",
    )


def downgrade() -> None:
    """Drop chat tables."""
    op.drop_table("chat_messages")
    op.drop_table("chat_conversations")
