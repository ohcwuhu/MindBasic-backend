"""add service agreement (scope boundary)

Revision ID: c1d2e3f4a5b6
Revises: b6d7e8f9a0b1
Create Date: 2026-08-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, Sequence[str], None] = "b6d7e8f9a0b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


AGREEMENT_CONTENT = """## 一、平台定位与资质边界

MindBasic 是一个「心理教练 / 成长辅导」自助平台，提供在线教练对话、自我教练工具、情绪记录与成长陪伴服务。

本平台**不提供心理咨询、心理治疗或医疗诊断服务**，不替代执业医师、心理咨询师或精神科医生的专业意见。如你正在接受治疗或存在疑似心理疾病症状，请及时前往正规医疗机构就诊。

## 二、AI 生成内容说明

自我教练等场景由人工智能生成回复。AI 生成内容仅用于自助陪伴与自我探索，不构成专业意见；回复可能存在不准确或不完整的情况，请结合自身情况理性使用。

## 三、危机与紧急情况

如你正处于心理危机或紧急状态（如自伤、自杀念头），请立即拨打全国心理援助热线 **12356**，或前往就近医疗机构/急诊寻求帮助，不要依赖平台回复。

## 四、服务约定

- 正式服务前可免费沟通（额度有限），正式预约需先支付并遵守取消/爽约规则；
- 平台教练为成长辅导方向的心理教练，非医疗机构执业人员；
- 平台提供风险提示与转介指引，但不对用户个人决策的后果承担责任。

## 五、用户责任

- 如实描述需求，不利用平台从事违法违规活动；
- 未成年人应在监护人指导下使用平台服务。

## 六、协议版本与更新

本协议当前版本为 **v1**，自发布之日起生效。平台更新协议时，会提示你重新确认。"""


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "users",
        sa.Column("agreement_version", sa.String(length=16), nullable=True, comment="已同意服务协议版本"),
    )
    op.add_column(
        "users",
        sa.Column("agreement_accepted_at", mysql.DATETIME(fsp=3), nullable=True, comment="同意服务协议时间"),
    )

    system_configs = sa.table(
        "system_configs",
        sa.column("config_key", sa.String),
        sa.column("config_value", sa.JSON),
        sa.column("description", sa.String),
    )
    op.bulk_insert(system_configs, [
        {
            "config_key": "agreement_version",
            "config_value": "1",
            "description": "服务协议版本号",
        },
        {
            "config_key": "agreement_content",
            "config_value": AGREEMENT_CONTENT,
            "description": "服务协议与免责声明（Markdown）",
        },
    ])


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        sa.text(
            "DELETE FROM system_configs "
            "WHERE config_key IN ('agreement_version', 'agreement_content')"
        )
    )
    op.drop_column("users", "agreement_accepted_at")
    op.drop_column("users", "agreement_version")
