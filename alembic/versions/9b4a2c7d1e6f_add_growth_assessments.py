"""add growth assessment tables + seed scale

Revision ID: 9b4a2c7d1e6f
Revises: c8d2e4f6a0b1
Create Date: 2026-08-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9b4a2c7d1e6f"
down_revision: Union[str, Sequence[str], None] = "c8d2e4f6a0b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "growth_assessment_templates",
        sa.Column("id", sa.dialects.mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.dialects.mysql.DATETIME(fsp=3), server_default=sa.text("CURRENT_TIMESTAMP(3)"), nullable=False),
        sa.Column("updated_at", sa.dialects.mysql.DATETIME(fsp=3), server_default=sa.text("CURRENT_TIMESTAMP(3)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "version", name="uq_growth_template_version"),
    )
    op.create_index("idx_growth_templates_enabled", "growth_assessment_templates", ["is_enabled"], unique=False)

    op.create_table(
        "growth_assessment_questions",
        sa.Column("id", sa.dialects.mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("template_id", sa.dialects.mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("dimension_key", sa.String(length=32), nullable=False),
        sa.Column("dimension_name", sa.String(length=32), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.ForeignKeyConstraint(["template_id"], ["growth_assessment_templates.id"], name="fk_growth_questions_template", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_growth_questions_template", "growth_assessment_questions", ["template_id", "sort_order"], unique=False)

    op.create_table(
        "growth_assessment_results",
        sa.Column("id", sa.dialects.mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.dialects.mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("template_id", sa.dialects.mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("answers", sa.JSON(), nullable=False),
        sa.Column("scores", sa.JSON(), nullable=False),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.dialects.mysql.DATETIME(fsp=3), server_default=sa.text("CURRENT_TIMESTAMP(3)"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_growth_results_user", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["template_id"], ["growth_assessment_templates.id"], name="fk_growth_results_template", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_growth_results_user", "growth_assessment_results", ["user_id", sa.text("created_at DESC")], unique=False)

    # ---------- 种子量表（5 维度 × 3 题） ----------
    templates = sa.table(
        "growth_assessment_templates",
        sa.column("id", sa.BigInteger),
        sa.column("name", sa.String),
        sa.column("description", sa.String),
        sa.column("version", sa.Integer),
        sa.column("is_enabled", sa.Boolean),
    )
    op.bulk_insert(templates, [{
        "id": 1,
        "name": "成长状态自评",
        "description": "从觉察、资源、目标、行动与情绪五个方向，看见自己当下的成长状态（资源导向，非诊断）。",
        "version": 1,
        "is_enabled": True,
    }])

    questions = sa.table(
        "growth_assessment_questions",
        sa.column("id", sa.BigInteger),
        sa.column("template_id", sa.BigInteger),
        sa.column("dimension_key", sa.String),
        sa.column("dimension_name", sa.String),
        sa.column("question", sa.Text),
        sa.column("options", sa.JSON),
        sa.column("sort_order", sa.Integer),
    )
    options = [
        {"value": 1, "label": "几乎从不"},
        {"value": 2, "label": "偶尔"},
        {"value": 3, "label": "有时"},
        {"value": 4, "label": "经常"},
        {"value": 5, "label": "几乎总是"},
    ]
    op.bulk_insert(questions, [
        {"id": 1, "template_id": 1, "dimension_key": "SELF_AWARENESS", "dimension_name": "自我觉察", "question": "我能察觉到自己的情绪变化，并知道它从哪来。", "options": options, "sort_order": 1},
        {"id": 2, "template_id": 1, "dimension_key": "SELF_AWARENESS", "dimension_name": "自我觉察", "question": "我清楚自己在压力下的反应模式。", "options": options, "sort_order": 2},
        {"id": 3, "template_id": 1, "dimension_key": "SELF_AWARENESS", "dimension_name": "自我觉察", "question": "我会留意自己的状态变化，并主动调整节奏。", "options": options, "sort_order": 3},
        {"id": 4, "template_id": 1, "dimension_key": "RESOURCE_USE", "dimension_name": "资源运用", "question": "遇到困难时，我能想起过去成功应对的经验。", "options": options, "sort_order": 4},
        {"id": 5, "template_id": 1, "dimension_key": "RESOURCE_USE", "dimension_name": "资源运用", "question": "我知道身边有哪些人可以支持我，并愿意开口求助。", "options": options, "sort_order": 5},
        {"id": 6, "template_id": 1, "dimension_key": "RESOURCE_USE", "dimension_name": "资源运用", "question": "我会把已有的优势用在新挑战上。", "options": options, "sort_order": 6},
        {"id": 7, "template_id": 1, "dimension_key": "GOAL_CLARITY", "dimension_name": "目标清晰", "question": "我清楚自己现阶段最想推进的目标。", "options": options, "sort_order": 7},
        {"id": 8, "template_id": 1, "dimension_key": "GOAL_CLARITY", "dimension_name": "目标清晰", "question": "我能说出目标为什么对我重要。", "options": options, "sort_order": 8},
        {"id": 9, "template_id": 1, "dimension_key": "GOAL_CLARITY", "dimension_name": "目标清晰", "question": "我知道目标达成后会是怎样的状态。", "options": options, "sort_order": 9},
        {"id": 10, "template_id": 1, "dimension_key": "ACTION", "dimension_name": "行动力", "question": "我会把大目标拆成可以执行的小步骤。", "options": options, "sort_order": 10},
        {"id": 11, "template_id": 1, "dimension_key": "ACTION", "dimension_name": "行动力", "question": "即使不完美，我也会先迈出第一步。", "options": options, "sort_order": 11},
        {"id": 12, "template_id": 1, "dimension_key": "ACTION", "dimension_name": "行动力", "question": "我会定期回顾自己的进展并做出调整。", "options": options, "sort_order": 12},
        {"id": 13, "template_id": 1, "dimension_key": "EMOTION_REGULATION", "dimension_name": "情绪调节", "question": "情绪波动时，我能用适合自己的方式让自己平静下来。", "options": options, "sort_order": 13},
        {"id": 14, "template_id": 1, "dimension_key": "EMOTION_REGULATION", "dimension_name": "情绪调节", "question": "我能接纳暂时的低落，不急着否定自己。", "options": options, "sort_order": 14},
        {"id": 15, "template_id": 1, "dimension_key": "EMOTION_REGULATION", "dimension_name": "情绪调节", "question": "我会给自己安排恢复能量的时间。", "options": options, "sort_order": 15},
    ])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_growth_results_user", table_name="growth_assessment_results")
    op.drop_table("growth_assessment_results")
    op.drop_index("idx_growth_questions_template", table_name="growth_assessment_questions")
    op.drop_table("growth_assessment_questions")
    op.drop_index("idx_growth_templates_enabled", table_name="growth_assessment_templates")
    op.drop_table("growth_assessment_templates")
