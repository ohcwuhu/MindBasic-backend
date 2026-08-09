"""seed article categories

Revision ID: 7d66bfa7894f
Revises: fcc962131e77
Create Date: 2026-08-09 15:18:09.952167

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7d66bfa7894f'
down_revision: Union[str, Sequence[str], None] = 'fcc962131e77'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    categories = sa.table(
        "article_categories",
        sa.column("id", sa.BigInteger),
        sa.column("name", sa.String),
        sa.column("sort_order", sa.Integer),
        sa.column("is_enabled", sa.Boolean),
    )
    op.bulk_insert(categories, [
        {"id": 1, "name": "成长技巧", "sort_order": 1, "is_enabled": True},
        {"id": 2, "name": "教练真实故事", "sort_order": 2, "is_enabled": True},
        {"id": 3, "name": "常见成长困惑", "sort_order": 3, "is_enabled": True},
    ])


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(sa.text("DELETE FROM article_categories WHERE id BETWEEN 1 AND 3"))
