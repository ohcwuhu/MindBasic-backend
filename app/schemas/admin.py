from typing import Literal

from pydantic import Field

from app.schemas.base import ApiModel


# ---------- 用户管理 ----------


class AdminUserOut(ApiModel):
    id: int
    phone: str
    nickname: str
    role: Literal["USER", "COACH", "ADMIN"]
    is_disabled: bool
    created_at: str
    last_login_at: str | None = None


class UserStatusIn(ApiModel):
    status: Literal["ENABLED", "DISABLED"]


# ---------- 文章管理 ----------


class ArticleAdminIn(ApiModel):
    title: str = Field(min_length=1, max_length=128)
    summary: str | None = Field(default=None, max_length=255)
    content: str = Field(min_length=1)
    cover_url: str | None = Field(default=None, max_length=512)
    category_id: int | None = None
    is_pinned: bool = False
    status: Literal["PUBLISHED", "DRAFT", "OFFLINE"] = "DRAFT"


class ArticleAdminPatchIn(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=128)
    summary: str | None = Field(default=None, max_length=255)
    content: str | None = None
    cover_url: str | None = Field(default=None, max_length=512)
    category_id: int | None = None
    is_pinned: bool | None = None
    status: Literal["PUBLISHED", "DRAFT", "OFFLINE"] | None = None


class ArticleAdminOut(ApiModel):
    id: int
    title: str
    summary: str | None = None
    content: str
    cover_url: str | None = None
    category_id: int | None = None
    is_pinned: bool
    status: Literal["PUBLISHED", "DRAFT", "OFFLINE"]
    view_count: int
    published_at: str | None = None
    created_at: str
    updated_at: str


# ---------- 文章分类 ----------


class CategoryIn(ApiModel):
    name: str = Field(min_length=1, max_length=32)
    sort_order: int = 0
    is_enabled: bool = True


class CategoryPatchIn(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=32)
    sort_order: int | None = None
    is_enabled: bool | None = None


class CategoryOut(ApiModel):
    id: int
    name: str
    sort_order: int
    is_enabled: bool


# ---------- 轮播图 ----------


class BannerIn(ApiModel):
    title: str = Field(min_length=1, max_length=64)
    image_url: str = Field(min_length=1, max_length=512)
    link_type: Literal["NONE", "ARTICLE", "ACTIVITY", "URL"] = "NONE"
    link_value: str | None = Field(default=None, max_length=512)
    sort_order: int = 0
    is_enabled: bool = True
    start_at: str | None = None
    end_at: str | None = None


class BannerPatchIn(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=64)
    image_url: str | None = Field(default=None, min_length=1, max_length=512)
    link_type: Literal["NONE", "ARTICLE", "ACTIVITY", "URL"] | None = None
    link_value: str | None = Field(default=None, max_length=512)
    sort_order: int | None = None
    is_enabled: bool | None = None
    start_at: str | None = None
    end_at: str | None = None


class BannerAdminOut(ApiModel):
    id: int
    title: str
    image_url: str
    link_type: str
    link_value: str | None = None
    sort_order: int
    is_enabled: bool
    start_at: str | None = None
    end_at: str | None = None
    created_at: str


# ---------- 标签 ----------


class TagIn(ApiModel):
    name: str = Field(min_length=1, max_length=32)
    type: Literal["FIELD", "AUDIENCE"]
    sort_order: int = 0
    is_enabled: bool = True


class TagPatchIn(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=32)
    sort_order: int | None = None
    is_enabled: bool | None = None


class TagAdminOut(ApiModel):
    id: int
    name: str
    type: str
    sort_order: int
    is_enabled: bool


# ---------- 情绪话术库 ----------


class FeedbackIn(ApiModel):
    mood_type: Literal["CALM", "HAPPY", "ANXIOUS", "DOWN", "IRRITATED", "OTHER"]
    content: str = Field(min_length=1, max_length=500)
    sort_order: int = 0
    is_enabled: bool = True


class FeedbackPatchIn(ApiModel):
    content: str | None = Field(default=None, min_length=1, max_length=500)
    sort_order: int | None = None
    is_enabled: bool | None = None


class FeedbackAdminOut(ApiModel):
    id: int
    mood_type: str
    content: str
    sort_order: int
    is_enabled: bool


# ---------- 统计 ----------


class StatsOut(ApiModel):
    user_count: int
    coach_count: int
    approved_coach_count: int
    appointment_count: int
    pending_appointment_count: int
    article_count: int
    today_user_count: int
    today_appointment_count: int
