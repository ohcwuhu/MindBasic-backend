"""add audit logs and data exports

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-08-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = "e3f4a5b6c7d8"
down_revision: Union[str, Sequence[str], None] = "d2e3f4a5b6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "audit_logs",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("actor_user_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("actor_role", sa.String(length=16), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("detail", mysql.JSON, nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=3),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
        ),
        sa.PrimaryKeyConstraint("id"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("idx_audit_actor", "audit_logs", ["actor_user_id", sa.text("created_at DESC")])
    op.create_index("idx_audit_target", "audit_logs", ["target_type", "target_id"])
    op.create_index("idx_audit_action", "audit_logs", ["action", sa.text("created_at DESC")])

    op.create_table(
        "data_exports",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'READY'"),
        ),
        sa.Column(
            "format",
            sa.String(length=8),
            nullable=False,
            server_default=sa.text("'JSON'"),
        ),
        sa.Column("file_id", sa.String(length=64), nullable=True),
        sa.Column("size", mysql.INTEGER, nullable=True),
        sa.Column("expires_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=3),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_data_exports_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("idx_data_exports_user", "data_exports", ["user_id", sa.text("created_at DESC")])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_data_exports_user", table_name="data_exports")
    op.drop_table("data_exports")
    op.drop_index("idx_audit_action", table_name="audit_logs")
    op.drop_index("idx_audit_target", table_name="audit_logs")
    op.drop_index("idx_audit_actor", table_name="audit_logs")
    op.drop_table("audit_logs")
