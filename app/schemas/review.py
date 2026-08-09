from pydantic import Field

from app.schemas.base import ApiModel


class ReviewIn(ApiModel):
    rating: int = Field(ge=1, le=5)
    content: str | None = Field(default=None, max_length=500)


class ReviewOut(ApiModel):
    id: int
    appointment_id: int
    coach_id: int
    nickname: str
    rating: int
    content: str | None = None
    created_at: str
