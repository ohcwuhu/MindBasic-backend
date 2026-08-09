from app.schemas.base import ApiModel


class ArticleListOut(ApiModel):
    """文章列表摘要（首页/列表通用）。"""

    id: int
    title: str
    summary: str | None = None
    cover_url: str | None = None
    category_id: int | None = None
    is_pinned: bool = False
    is_favorite: bool = False
    published_at: str | None = None
