"""add user gender

Revision ID: f1a2b3c4d5e6
Revises: a1b2c3d4e5f6
Create Date: 2026-08-16 22:56:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "users",
        sa.Column(
            "gender",
            sa.String(length=8),
            nullable=False,
            server_default=sa.text("'girl'"),
            comment="陪伴角色性别：boy=小男孩 / girl=小女孩",
        ),
    )
    op.create_check_constraint("chk_users_gender", "users", sa.text("gender IN ('boy','girl')"))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("chk_users_gender", "users", type_="check")
    op.drop_column("users", "gender")
