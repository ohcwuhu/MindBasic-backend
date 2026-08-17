"""add payment locking (orders/wallet/refund)

Revision ID: b6d7e8f9a0b1
Revises: c3d4e5f6a7b8
Create Date: 2026-08-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = "b6d7e8f9a0b1"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # ---------- orders ----------
    op.add_column(
        "orders",
        sa.Column("type", sa.String(length=16), nullable=False, server_default=sa.text("'APPOINTMENT'")),
    )
    op.add_column("orders", sa.Column("appointment_id", mysql.BIGINT(unsigned=True), nullable=True))
    op.add_column("orders", sa.Column("expire_at", mysql.DATETIME(fsp=3), nullable=True))
    op.add_column("orders", sa.Column("refunded_at", mysql.DATETIME(fsp=3), nullable=True))
    op.alter_column("orders", "coach_id", existing_type=mysql.BIGINT(unsigned=True), nullable=True)
    op.alter_column("orders", "service_id", existing_type=mysql.BIGINT(unsigned=True), nullable=True)
    op.create_unique_constraint("uq_orders_appointment", "orders", ["appointment_id"])
    op.create_index("idx_orders_appointment", "orders", ["appointment_id"])

    # ---------- appointments ----------
    op.add_column("appointments", sa.Column("order_id", mysql.BIGINT(unsigned=True), nullable=True))
    op.create_index("idx_appointments_order", "appointments", ["order_id"])

    # ---------- user_wallets ----------
    op.create_table(
        "user_wallets",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column(
            "user_id",
            mysql.BIGINT(unsigned=True),
            nullable=False,
        ),
        sa.Column(
            "balance_in_cents",
            mysql.INTEGER(unsigned=True),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "version",
            mysql.INTEGER(unsigned=True),
            nullable=False,
            server_default=sa.text("0"),
        ),
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
            name="fk_user_wallets_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_user_wallets_user"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )

    # ---------- wallet_transactions ----------
    op.create_table(
        "wallet_transactions",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("wallet_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("change_in_cents", mysql.INTEGER, nullable=False),
        sa.Column("balance_after", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("biz_type", sa.String(length=16), nullable=False),
        sa.Column("order_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=3),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
        ),
        sa.ForeignKeyConstraint(
            ["wallet_id"],
            ["user_wallets.id"],
            name="fk_wallet_tx_wallet",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("idx_wallet_tx_wallet", "wallet_transactions", ["wallet_id", sa.text("created_at DESC")])

    # ---------- refunds ----------
    op.create_table(
        "refunds",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("order_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("appointment_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("amount_in_cents", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=3),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name="fk_refunds_order",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("idx_refunds_order", "refunds", ["order_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_refunds_order", table_name="refunds")
    op.drop_table("refunds")
    op.drop_index("idx_wallet_tx_wallet", table_name="wallet_transactions")
    op.drop_table("wallet_transactions")
    op.drop_table("user_wallets")
    op.drop_index("idx_appointments_order", table_name="appointments")
    op.drop_column("appointments", "order_id")
    op.drop_index("idx_orders_appointment", table_name="orders")
    op.drop_constraint("uq_orders_appointment", "orders", type_="unique")
    op.alter_column("orders", "service_id", existing_type=mysql.BIGINT(unsigned=True), nullable=False)
    op.alter_column("orders", "coach_id", existing_type=mysql.BIGINT(unsigned=True), nullable=False)
    op.drop_column("orders", "refunded_at")
    op.drop_column("orders", "expire_at")
    op.drop_column("orders", "appointment_id")
    op.drop_column("orders", "type")
