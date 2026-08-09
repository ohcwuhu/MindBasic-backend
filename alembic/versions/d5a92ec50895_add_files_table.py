"""add files table

Revision ID: d5a92ec50895
Revises: 7d66bfa7894f
Create Date: 2026-08-09 16:07:41.425969

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = 'd5a92ec50895'
down_revision: Union[str, Sequence[str], None] = '7d66bfa7894f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "files",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("file_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("usage", sa.String(length=16), nullable=False),
        sa.Column("is_private", sa.Boolean(), nullable=False),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=3), server_default=sa.text("CURRENT_TIMESTAMP(3)"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_files_user", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("file_id", name="uq_files_file_id"),
    )
    op.create_index("idx_files_user", "files", ["user_id"], unique=False)
    op.create_index("idx_files_private", "files", ["is_private"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_files_private", table_name="files")
    op.drop_index("idx_files_user", table_name="files")
    op.drop_table("files")
