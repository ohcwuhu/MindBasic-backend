from typing import Literal

from pydantic import Field

from app.schemas.base import ApiModel

MOOD_TYPES = Literal["CALM", "HAPPY", "ANXIOUS", "DOWN", "IRRITATED", "OTHER"]


class EmotionJournalIn(ApiModel):
    mood_type: MOOD_TYPES
    content: str = Field(min_length=1, max_length=500)


class EmotionJournalOut(ApiModel):
    id: int
    mood_type: MOOD_TYPES
    content: str
    feedback: str | None = None
    created_at: str


class EmotionTrendDayOut(ApiModel):
    date: str
    moods: dict[str, int]


class EmotionTrendOut(ApiModel):
    days: int
    items: list[EmotionTrendDayOut]
    summary: dict[str, int]
