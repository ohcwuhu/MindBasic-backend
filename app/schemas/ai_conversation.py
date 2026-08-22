"""AI 自我教练对话记录。"""

from typing import Literal

from app.schemas.base import ApiModel


class AiConversationOut(ApiModel):
    id: int
    title: str
    status: Literal["ACTIVE", "ENDED"]
    message_count: int
    created_at: str
    updated_at: str


class AiMessageOut(ApiModel):
    id: int
    role: Literal["USER", "ASSISTANT"]
    content: str
    emotion: dict | None = None
    created_at: str
