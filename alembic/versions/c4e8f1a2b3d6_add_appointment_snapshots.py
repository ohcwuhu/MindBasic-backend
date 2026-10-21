"""add appointment service and slot snapshots

Revision ID: c4e8f1a2b3d6
Revises: e9c1d3f5a7b9
Create Date: 2026-10-21
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "c4e8f1a2b3d6"
down_revision: Union[str, Sequence[str], None] = "e9c1d3f5a7b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("appointments", sa.Column("service_name", sa.String(length=64), nullable=True))
    op.add_column("appointments", sa.Column("service_type", sa.String(length=16), nullable=True))
    op.add_column(
        "appointments",
        sa.Column("service_duration_min", mysql.INTEGER(unsigned=True), nullable=True),
    )
    op.add_column(
        "appointments",
        sa.Column("service_price_in_cents", mysql.INTEGER(unsigned=True), nullable=True),
    )
    op.add_column("appointments", sa.Column("slot_date", sa.Date(), nullable=True))
    op.add_column("appointments", sa.Column("slot_start_time", sa.Time(), nullable=True))
    op.add_column("appointments", sa.Column("slot_end_time", sa.Time(), nullable=True))

    # 历史排班仍由外键关联，可准确回填；服务成交价优先从订单取得。
    op.execute(
        """
        UPDATE appointments a
        JOIN coach_slots s ON s.id = a.slot_id
        SET a.slot_date = s.date,
            a.slot_start_time = s.start_time,
            a.slot_end_time = s.end_time
        """
    )
    op.execute(
        """
        UPDATE appointments a
        JOIN services s ON s.id = a.service_id
        SET a.service_name = s.name,
            a.service_type = s.service_type,
            a.service_duration_min = s.duration_min
        """
    )
    op.execute(
        """
        UPDATE appointments a
        LEFT JOIN orders o ON o.id = a.order_id
        LEFT JOIN services s ON s.id = a.service_id
        SET a.service_price_in_cents = COALESCE(o.amount_in_cents, s.price_in_cents)
        """
    )

    # 旧实现把取消截止按上海墙上时间写入；该字段来源单一，可安全统一为 UTC。
    op.execute(
        """
        UPDATE appointments
        SET cancel_deadline_at = CONVERT_TZ(cancel_deadline_at, '+08:00', '+00:00')
        WHERE cancel_deadline_at IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE appointments
        SET cancel_deadline_at = CONVERT_TZ(cancel_deadline_at, '+00:00', '+08:00')
        WHERE cancel_deadline_at IS NOT NULL
        """
    )
    op.drop_column("appointments", "slot_end_time")
    op.drop_column("appointments", "slot_start_time")
    op.drop_column("appointments", "slot_date")
    op.drop_column("appointments", "service_price_in_cents")
    op.drop_column("appointments", "service_duration_min")
    op.drop_column("appointments", "service_type")
    op.drop_column("appointments", "service_name")
