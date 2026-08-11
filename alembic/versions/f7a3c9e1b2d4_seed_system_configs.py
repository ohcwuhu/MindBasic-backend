"""seed system configs (compliance: hotline / disclaimer)

Revision ID: f7a3c9e1b2d4
Revises: 16af94cc6ea0
Create Date: 2026-08-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f7a3c9e1b2d4"
down_revision: Union[str, Sequence[str], None] = "16af94cc6ea0"
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
        {"config_key": "platform_name", "config_value": "MindBasic", "description": "平台名称（前台展示）"},
        {"config_key": "hotline", "config_value": "12356", "description": "心理援助热线号码"},
        {
            "config_key": "emergency_hint",
            "config_value": "如你正处于心理危机或紧急状态，请立即拨打全国心理援助热线 12356，或前往就近医疗机构寻求帮助。",
            "description": "紧急求助说明（危机时展示）",
        },
        {
            "config_key": "disclaimer",
            "config_value": "本平台提供成长陪伴与自助工具，不提供心理疾病诊断或治疗服务；如有医疗需求，请前往正规医疗机构就诊。",
            "description": "免责声明",
        },
    ])


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        sa.text(
            "DELETE FROM system_configs "
            "WHERE config_key IN ('platform_name', 'hotline', 'emergency_hint', 'disclaimer')"
        )
    )
