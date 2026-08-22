"""add ai conversations persistence

Revision ID: a5b6c7d8e9f0
Revises: f4a5b6c7d8e9
Create Date: 2026-08-22
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = "a5b6c7d8e9f0"
down_revision: Union[str, Sequence[str], None] = "f4a5b6c7d8e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "ai_conversations",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column(
            "title",
            sa.String(length=100),
            nullable=False,
            server_default=sa.text("'自我教练对话'"),
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'ACTIVE'"),
        ),
        sa.Column(
            "message_count",
            mysql.INTEGER,
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("ended_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=3),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
        ),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=3),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
            onupdate=sa.text("CURRENT_TIMESTAMP(3)"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_ai_conv_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("idx_ai_conv_user", "ai_conversations", ["user_id", "status", sa.text("created_at DESC")])

    op.create_table(
        "ai_messages",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("conversation_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("emotion", mysql.JSON, nullable=True),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=3),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["ai_conversations.id"],
            name="fk_ai_msg_conv",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("idx_ai_msg_conv", "ai_messages", ["conversation_id", sa.text("created_at ASC")])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("fk_ai_msg_conv", "ai_messages", type_="foreignkey")
    op.drop_index("idx_ai_msg_conv", table_name="ai_messages")
    op.drop_table("ai_messages")
    op.drop_constraint("fk_ai_conv_user", "ai_conversations", type_="foreignkey")
    op.drop_index("idx_ai_conv_user", table_name="ai_conversations")
    op.drop_table("ai_conversations")
