"""add users.email + email_verification_codes

Revision ID: e5d6f7a8b9c0
Revises: c3a5d7e9f1b2
Create Date: 2026-08-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e5d6f7a8b9c0"
down_revision: Union[str, Sequence[str], None] = "c3a5d7e9f1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("users", sa.Column("email", sa.String(length=255), nullable=True))
    op.create_unique_constraint("uq_users_email", "users", ["email"])

    op.create_table(
        "email_verification_codes",
        sa.Column("id", sa.dialects.mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("expires_at", sa.dialects.mysql.DATETIME(fsp=3), nullable=False),
        sa.Column("consumed_at", sa.dialects.mysql.DATETIME(fsp=3), nullable=True),
        sa.Column("created_at", sa.dialects.mysql.DATETIME(fsp=3), server_default=sa.text("CURRENT_TIMESTAMP(3)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_email_codes_lookup", "email_verification_codes", ["email", "purpose", sa.text("created_at DESC")], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_email_codes_lookup", table_name="email_verification_codes")
    op.drop_table("email_verification_codes")
    op.drop_constraint("uq_users_email", "users", type_="unique")
    op.drop_column("users", "email")
