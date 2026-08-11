"""主题社群：社群、帖子、评论、点赞。"""

from typing import Literal

from pydantic import Field

from app.schemas.base import ApiModel


class CommunityCreateIn(ApiModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=500)
    cover_url: str | None = Field(default=None, max_length=512)


class CommunityPatchIn(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, min_length=1, max_length=500)
    cover_url: str | None = Field(default=None, max_length=512)


class CommunityBriefOut(ApiModel):
    id: int
    name: str
    description: str
    cover_url: str | None = None
    coach_nickname: str | None = None
    member_count: int
    joined: bool = False


class CommunityDetailOut(CommunityBriefOut):
    can_manage: bool = False
    max_members: int
    created_at: str


class CommunityPostIn(ApiModel):
    content: str = Field(min_length=1, max_length=4000)
    image_url: str | None = Field(default=None, max_length=512)


class CommunityPostOut(ApiModel):
    id: int
    community_id: int
    user_id: int
    nickname: str
    content: str
    image_url: str | None = None
    is_pinned: bool
    like_count: int
    liked: bool = False
    comment_count: int = 0
    created_at: str


class CommunityCommentIn(ApiModel):
    content: str = Field(min_length=1, max_length=500)


class CommunityCommentOut(ApiModel):
    id: int
    post_id: int
    user_id: int
    nickname: str
    content: str
    created_at: str


class CommunityStatusIn(ApiModel):
    status: Literal["ACTIVE", "DISABLED"]
