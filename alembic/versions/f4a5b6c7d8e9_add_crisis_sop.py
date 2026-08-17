"""add crisis SOP (flags / follow-ups)

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-08-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = "f4a5b6c7d8e9"
down_revision: Union[str, Sequence[str], None] = "e3f4a5b6c7d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "crisis_flags",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column(
            "level",
            sa.String(length=8),
            nullable=False,
            server_default=sa.text("'HIGH'"),
        ),
        sa.Column("content", sa.String(length=500), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'OPEN'"),
        ),
        sa.Column("assigned_admin_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("resolved_at", mysql.DATETIME(fsp=3), nullable=True),
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
            name="fk_crisis_flags_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("idx_crisis_user", "crisis_flags", ["user_id", "status", sa.text("created_at DESC")])
    op.create_index("idx_crisis_status", "crisis_flags", ["status", sa.text("created_at DESC")])

    op.create_table(
        "crisis_follow_ups",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("crisis_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("actor_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("actor_role", sa.String(length=16), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=3),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
        ),
        sa.ForeignKeyConstraint(
            ["crisis_id"],
            ["crisis_flags.id"],
            name="fk_crisis_followups_flag",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("idx_crisis_followups_flag", "crisis_follow_ups", ["crisis_id", sa.text("created_at ASC")])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_crisis_followups_flag", table_name="crisis_follow_ups")
    op.drop_table("crisis_follow_ups")
    op.drop_index("idx_crisis_status", table_name="crisis_flags")
    op.drop_index("idx_crisis_user", table_name="crisis_flags")
    op.drop_table("crisis_flags")
