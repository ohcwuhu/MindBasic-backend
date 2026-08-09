from typing import Literal

from app.schemas.article import ArticleListOut
from app.schemas.base import ApiModel


class BannerOut(ApiModel):
    id: int
    title: str
    image_url: str
    link_type: Literal["NONE", "ARTICLE", "ACTIVITY", "URL"]
    link_value: str | None = None
    sort_order: int


class QuickEntryOut(ApiModel):
    key: str
    title: str
    icon: str
    path: str


class CoachBriefOut(ApiModel):
    id: int
    nickname: str
    avatar_url: str | None = None
    tag_names: list[str] = []
    years_of_experience: int
    rating: float
    review_count: int
    service_concept: str | None = None


class HomeOut(ApiModel):
    banners: list[BannerOut] = []
    quick_entries: list[QuickEntryOut] = []
    featured_articles: list[ArticleListOut] = []
    recommended_coaches: list[CoachBriefOut] = []
