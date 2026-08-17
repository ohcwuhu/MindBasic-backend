"""add ai disclaimer config

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-08-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d2e3f4a5b6c7"
down_revision: Union[str, Sequence[str], None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    system_configs = sa.table(
        "system_configs",
        sa.column("config_key", sa.String),
        sa.column("config_value", sa.JSON),
        sa.column("description", sa.String),
    )
    op.bulk_insert(system_configs, [
        {
            "config_key": "ai_disclaimer",
            "config_value": "AI 生成内容，非人工心理服务，仅供参考；不提供诊断或治疗，如处于危机请拨打 12356。",
            "description": "AI 生成内容标识与免责说明",
        },
    ])


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(sa.text("DELETE FROM system_configs WHERE config_key = 'ai_disclaimer'"))
