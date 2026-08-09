"""add checkins badges phrases

Revision ID: 9e50587faa5e
Revises: d5a92ec50895
Create Date: 2026-08-09 16:49:07.219300

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = '9e50587faa5e'
down_revision: Union[str, Sequence[str], None] = 'd5a92ec50895'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "check_ins",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("check_date", sa.Date(), nullable=False),
        sa.Column("content", sa.String(length=200), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=3), server_default=sa.text("CURRENT_TIMESTAMP(3)"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_check_ins_user", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "check_date", name="uq_check_ins_user_date"),
    )
    op.create_index("idx_check_ins_date", "check_ins", ["check_date"], unique=False)

    op.create_table(
        "badges",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("key", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("description", sa.String(length=200), nullable=False),
        sa.Column("icon", sa.String(length=64), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", name="uq_badges_key"),
    )

    op.create_table(
        "user_badges",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("badge_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("earned_at", mysql.DATETIME(fsp=3), server_default=sa.text("CURRENT_TIMESTAMP(3)"), nullable=False),
        sa.ForeignKeyConstraint(["badge_id"], ["badges.id"], name="fk_user_badges_badge", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user_badges_user", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "badge_id", name="uq_user_badges"),
    )

    op.create_table(
        "coach_phrases",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("coach_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("content", sa.String(length=500), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False, server_default=sa.text("'custom'")),
        sa.Column("created_at", mysql.DATETIME(fsp=3), server_default=sa.text("CURRENT_TIMESTAMP(3)"), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=3), server_default=sa.text("CURRENT_TIMESTAMP(3)"), nullable=False),
        sa.ForeignKeyConstraint(["coach_id"], ["coach_profiles.id"], name="fk_coach_phrases_coach", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_coach_phrases_coach", "coach_phrases", ["coach_id"], unique=False)

    op.create_table(
        "platform_phrases",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("content", sa.String(length=500), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", mysql.DATETIME(fsp=3), server_default=sa.text("CURRENT_TIMESTAMP(3)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    badges = sa.table(
        "badges",
        sa.column("id", sa.BigInteger()),
        sa.column("key", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.String()),
        sa.column("icon", sa.String()),
        sa.column("sort_order", sa.Integer()),
    )
    op.bulk_insert(badges, [
        {"id": 1, "key": "FIRST_CHECKIN", "name": "迈出第一步", "description": "完成第一次每日打卡", "icon": "seedling", "sort_order": 1},
        {"id": 2, "key": "STREAK_3", "name": "连续三天", "description": "连续打卡 3 天", "icon": "three", "sort_order": 2},
        {"id": 3, "key": "STREAK_7", "name": "坚持一周", "description": "连续打卡 7 天", "icon": "seven", "sort_order": 3},
        {"id": 4, "key": "TOTAL_10", "name": "积累十次", "description": "累计打卡 10 次", "icon": "ten", "sort_order": 4},
        {"id": 5, "key": "MONTH_15", "name": "月度满勤", "description": "单月打卡 15 天", "icon": "month", "sort_order": 5},
    ])

    phrases = sa.table(
        "platform_phrases",
        sa.column("id", sa.BigInteger()),
        sa.column("category", sa.String()),
        sa.column("content", sa.String()),
        sa.column("sort_order", sa.Integer()),
        sa.column("is_enabled", sa.Boolean()),
    )
    op.bulk_insert(phrases, [
        {"id": 1, "category": "OPENING", "content": "今天想从哪件事聊起？", "sort_order": 1, "is_enabled": True},
        {"id": 2, "category": "OPENING", "content": "谢谢你愿意和我分享此刻。", "sort_order": 2, "is_enabled": True},
        {"id": 3, "category": "RESOURCE", "content": "过去哪次你成功渡过类似的时刻？当时靠的是什么？", "sort_order": 1, "is_enabled": True},
        {"id": 4, "category": "RESOURCE", "content": "身边有哪些人可以支持你？", "sort_order": 2, "is_enabled": True},
        {"id": 5, "category": "FUTURE", "content": "如果三个月后回看今天，你希望那时的自己是什么状态？", "sort_order": 1, "is_enabled": True},
        {"id": 6, "category": "FUTURE", "content": "理想的状态里，你会有什么不同？", "sort_order": 2, "is_enabled": True},
        {"id": 7, "category": "ACTION", "content": "接下来 7 天里，哪件小事可以让你往前一步？", "sort_order": 1, "is_enabled": True},
        {"id": 8, "category": "ACTION", "content": "你怎么知道这件事真的做到了？", "sort_order": 2, "is_enabled": True},
        {"id": 9, "category": "OTHER", "content": "你此刻最在意的是什么？", "sort_order": 1, "is_enabled": True},
    ])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("platform_phrases")
    op.drop_index("idx_coach_phrases_coach", table_name="coach_phrases")
    op.drop_table("coach_phrases")
    op.drop_table("user_badges")
    op.drop_table("badges")
    op.drop_index("idx_check_ins_date", table_name="check_ins")
    op.drop_table("check_ins")
