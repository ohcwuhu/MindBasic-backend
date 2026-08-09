from pydantic import Field

from app.schemas.base import ApiModel


class CheckInIn(ApiModel):
    content: str | None = Field(default=None, max_length=200)


class CheckInOut(ApiModel):
    id: int
    check_date: str
    content: str | None = None
    created_at: str


class BadgeOut(ApiModel):
    id: int
    key: str
    name: str
    description: str
    icon: str | None = None
    earned_at: str


class LeaderboardItemOut(ApiModel):
    rank: int
    nickname: str
    count: int


class CheckInStatsOut(ApiModel):
    streak_days: int
    total_count: int
    month_count: int
