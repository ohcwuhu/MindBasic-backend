"""聊天请求/响应模型。"""

from pydantic import BaseModel, Field


class StartConversationIn(BaseModel):
    coach_id: int = Field(..., alias="coachId", description="教练资料 ID")

    model_config = {"populate_by_name": True}


class SendMessageIn(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)
