"""add fallback_risk_level to multimodal_analysis_records

Revision ID: e9c1d3f5a7b9
Revises: d7e8f9a0b1c2
Create Date: 2026-10-03

背景：``dify_risk_level`` 只有 Dify 工作流会产出，Dify 抖动触发兜底时该轮只能留空，
一致性统计的样本会随"主路径挂了多久"而缩水。新增列专门存放兜底模型的同口径自判，
与 Dify 的判定分列存放，避免把两个来源混成一列导致口径失真。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e9c1d3f5a7b9"
down_revision: Union[str, Sequence[str], None] = "d7e8f9a0b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "multimodal_analysis_records",
        sa.Column(
            "fallback_risk_level",
            sa.String(length=8),
            nullable=True,
            comment=(
                "兜底模型自判的风险等级（low/medium/high）：Dify 不可用时的第二意见，"
                "与 dify_risk_level 分列存放以免口径混用"
            ),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("multimodal_analysis_records", "fallback_risk_level")
