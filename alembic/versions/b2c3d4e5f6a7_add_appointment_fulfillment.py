"""add appointment fulfillment (cancel deadline / no-show / events)

Revision ID: b2c3d4e5f6a7
Revises: f1a2b3c4d5e6
Create Date: 2026-08-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import BIGINT, DATETIME


# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "appointments",
        sa.Column("cancel_deadline_at", DATETIME(fsp=3), nullable=True),
    )
    op.add_column(
        "appointments",
        sa.Column("no_show_at", DATETIME(fsp=3), nullable=True),
    )
    op.drop_constraint("chk_appointments_status", "appointments", type_="check")
    op.create_check_constraint(
        "chk_appointments_status",
        "appointments",
        "status IN ('PENDING','CONFIRMED','COMPLETED','CANCELLED','NO_SHOW','RESCHEDULED')",
    )
    op.create_table(
        "appointment_events",
        sa.Column("id", BIGINT(unsigned=True), primary_key=True, autoincrement=True),
        sa.Column(
            "appointment_id",
            BIGINT(unsigned=True),
            sa.ForeignKey("appointments.id", ondelete="CASCADE", name="fk_appointment_events_appt"),
            nullable=False,
        ),
        sa.Column("actor_id", BIGINT(unsigned=True), nullable=True),
        sa.Column("actor_role", sa.String(16), nullable=False),
        sa.Column("event", sa.String(32), nullable=False),
        sa.Column("note", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            DATETIME(fsp=3),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
        ),
        sa.Index("idx_appointment_events", "appointment_id", "created_at"),
        mysql_charset="utf8mb4",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("appointment_events")
    op.drop_constraint("chk_appointments_status", "appointments", type_="check")
    op.create_check_constraint(
        "chk_appointments_status",
        "appointments",
        "status IN ('PENDING','CONFIRMED','COMPLETED','CANCELLED')",
    )
    op.drop_column("appointments", "no_show_at")
    op.drop_column("appointments", "cancel_deadline_at")
