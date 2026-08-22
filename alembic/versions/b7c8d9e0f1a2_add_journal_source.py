"""add emotion journal source (self-coaching link)

Revision ID: b7c8d9e0f1a2
Revises: a6b7c8d9e0f1
Create Date: 2026-08-22
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, Sequence[str], None] = "a6b7c8d9e0f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "emotion_journals",
        sa.Column(
            "source",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'MANUAL'"),
        ),
    )
    op.add_column(
        "emotion_journals",
        sa.Column("source_conversation_id", mysql.BIGINT(unsigned=True), nullable=True),
    )
    op.create_unique_constraint(
        "uq_emotion_journals_conv",
        "emotion_journals",
        ["source_conversation_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("uq_emotion_journals_conv", "emotion_journals", type_="unique")
    op.drop_column("emotion_journals", "source_conversation_id")
    op.drop_column("emotion_journals", "source")
