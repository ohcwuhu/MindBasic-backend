"""add chat free limit

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-08-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "chat_conversations",
        sa.Column("free_limit", sa.Integer, nullable=False, server_default=sa.text("3")),
    )
    op.add_column(
        "chat_conversations",
        sa.Column("coach_reply_count", sa.Integer, nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "chat_conversations",
        sa.Column("unlocked", sa.Boolean, nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("chat_conversations", "unlocked")
    op.drop_column("chat_conversations", "coach_reply_count")
    op.drop_column("chat_conversations", "free_limit")
