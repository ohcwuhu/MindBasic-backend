"""平台系统配置：管理端键值维护 + 公开合规信息。"""

from pydantic import Field

from app.schemas.base import ApiModel


class SystemConfigItemIn(ApiModel):
    key: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=2000)


class SystemConfigUpdateIn(ApiModel):
    items: list[SystemConfigItemIn] = Field(min_length=1, max_length=20)


class PublicPlatformConfigOut(ApiModel):
    platform_name: str
    hotline: str
    emergency_hint: str
    disclaimer: str
