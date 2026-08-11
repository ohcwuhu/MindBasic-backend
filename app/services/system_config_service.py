"""平台系统配置业务逻辑（system_configs 键值表，含默认值与审计）。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.models.content import SystemConfig
from app.models.user import AdminActionLog, User


DEFAULT_CONFIGS: dict[str, dict[str, str]] = {
    "platform_name": {
        "value": "MindBasic",
        "description": "平台名称（前台展示）",
    },
    "hotline": {
        "value": "12356",
        "description": "心理援助热线号码",
    },
    "emergency_hint": {
        "value": "如你正处于心理危机或紧急状态，请立即拨打全国心理援助热线 12356，或前往就近医疗机构寻求帮助。",
        "description": "紧急求助说明（危机时展示）",
    },
    "disclaimer": {
        "value": "本平台提供成长陪伴与自助工具，不提供心理疾病诊断或治疗服务；如有医疗需求，请前往正规医疗机构就诊。",
        "description": "免责声明",
    },
}

ALLOWED_KEYS = frozenset(DEFAULT_CONFIGS)


def _value_of(row: SystemConfig | None, key: str) -> str:
    if row is not None and isinstance(row.config_value, str) and row.config_value.strip():
        return row.config_value
    return DEFAULT_CONFIGS[key]["value"]


async def get_all_configs(db: AsyncSession) -> list[dict]:
    """返回全量配置（缺失的键兜底默认值，保证前端始终可渲染）。"""
    rows = {
        row.config_key: row
        for row in await db.scalars(select(SystemConfig).order_by(SystemConfig.config_key))
    }
    return [
        {
            "key": key,
            "value": _value_of(rows.get(key), key),
            "description": rows[key].description if key in rows else meta["description"],
        }
        for key, meta in DEFAULT_CONFIGS.items()
    ]


async def get_public_config(db: AsyncSession) -> dict:
    """公开合规信息：平台名称 / 援助热线 / 紧急求助说明 / 免责声明。"""
    rows = {
        row.config_key: row.config_value
        for row in await db.scalars(
            select(SystemConfig).where(SystemConfig.config_key.in_(DEFAULT_CONFIGS.keys()))
        )
    }
    return {
        "platformName": rows.get("platform_name", DEFAULT_CONFIGS["platform_name"]["value"]),
        "hotline": rows.get("hotline", DEFAULT_CONFIGS["hotline"]["value"]),
        "emergencyHint": rows.get("emergency_hint", DEFAULT_CONFIGS["emergency_hint"]["value"]),
        "disclaimer": rows.get("disclaimer", DEFAULT_CONFIGS["disclaimer"]["value"]),
    }


async def update_configs(db: AsyncSession, admin: User, items: list[dict]) -> list[dict]:
    """批量更新配置：键白名单校验 + 单事务 + 管理审计日志。"""
    updates = {item["key"]: item["value"] for item in items}
    unknown = [key for key in updates if key not in ALLOWED_KEYS]
    if unknown:
        raise AppError(400, "VALIDATION_ERROR", f"无效的配置键：{', '.join(sorted(unknown))}")
    for key, value in updates.items():
        value = value.strip()
        if not value:
            raise AppError(400, "VALIDATION_ERROR", f"配置「{key}」不能为空")
        updates[key] = value

    existing = {
        row.config_key: row
        for row in await db.scalars(
            select(SystemConfig).where(SystemConfig.config_key.in_(updates.keys()))
        )
    }
    for key, value in updates.items():
        row = existing.get(key)
        if row is None:
            db.add(SystemConfig(
                config_key=key,
                config_value=value,
                description=DEFAULT_CONFIGS[key]["description"],
                updated_by=admin.id,
            ))
        else:
            row.config_value = value
            row.updated_by = admin.id
    db.add(AdminActionLog(
        admin_id=admin.id,
        action="SYSTEM_CONFIG_UPDATE",
        target_type="SYSTEM_CONFIG",
        target_id=0,
        detail={"keys": sorted(updates)},
    ))
    await db.commit()
    return await get_all_configs(db)
