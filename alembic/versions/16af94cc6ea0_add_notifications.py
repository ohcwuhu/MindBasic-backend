"""add notifications

Revision ID: 16af94cc6ea0
Revises: 9e50587faa5e
Create Date: 2026-08-09 17:09:26.318297

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = '16af94cc6ea0'
down_revision: Union[str, Sequence[str], None] = '9e50587faa5e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "notifications",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=64), nullable=False),
        sa.Column("content", sa.String(length=255), nullable=False),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", mysql.DATETIME(fsp=3), server_default=sa.text("CURRENT_TIMESTAMP(3)"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_notifications_user", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_notifications_user", "notifications", ["user_id", "is_read"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_notifications_user", table_name="notifications")
    op.drop_table("notifications")
