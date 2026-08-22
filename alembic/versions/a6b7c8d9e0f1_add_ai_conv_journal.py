"""add ai conversation journal link

Revision ID: a6b7c8d9e0f1
Revises: a5b6c7d8e9f0
Create Date: 2026-08-22
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = "a6b7c8d9e0f1"
down_revision: Union[str, Sequence[str], None] = "a5b6c7d8e9f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "ai_conversations",
        sa.Column("journal_id", mysql.BIGINT(unsigned=True), nullable=True),
    )
    op.create_index("idx_ai_conv_journal", "ai_conversations", ["journal_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_ai_conv_journal", table_name="ai_conversations")
    op.drop_column("ai_conversations", "journal_id")
