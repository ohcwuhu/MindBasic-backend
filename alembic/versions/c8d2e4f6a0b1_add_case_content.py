"""add case_records.content (markdown template)

Revision ID: c8d2e4f6a0b1
Revises: f7a3c9e1b2d4
Create Date: 2026-08-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c8d2e4f6a0b1"
down_revision: Union[str, Sequence[str], None] = "f7a3c9e1b2d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("case_records", sa.Column("content", sa.Text(), nullable=True))
    # 回填：把原三个字段合并为 Markdown 模板
    op.execute(
        sa.text(
            "UPDATE case_records SET content = CONCAT("
            "'## 对话核心要点\n', COALESCE(key_points, ''), "
            "'\n\n## 用户收获\n', COALESCE(user_gains, ''), "
            "'\n\n## 后续跟进建议\n', COALESCE(followup_advice, '')"
            ") WHERE content IS NULL"
        )
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("case_records", "content")
