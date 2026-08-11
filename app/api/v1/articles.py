from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, get_current_user, get_optional_user
from app.api.response import ok, paginated
from app.models.user import User
from app.schemas.article import ArticleDetailOut, ArticleListOut
from app.services.article_service import (
    article_detail_to_out,
    article_to_out,
    get_favorite_ids,
    get_public_article_or_404,
    get_public_articles,
    increment_view_count,
    list_categories,
    list_my_favorites,
    toggle_favorite,
)

articles_router = APIRouter(prefix="/articles", tags=["articles"])
categories_router = APIRouter(prefix="/article-categories", tags=["articles"])


@articles_router.get("")
async def list_articles(
    request: Request,
    categoryId: int | None = Query(default=None, alias="categoryId"),
    keyword: str | None = Query(default=None, max_length=50),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=10, ge=1, le=50, alias="pageSize"),
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    rows, total = await get_public_articles(db, categoryId, keyword, page, pageSize)
    favorite_ids = await get_favorite_ids(db, user.id, [a.id for a in rows]) if user else set()
    items = [ArticleListOut(**article_to_out(a, favorite_ids)).model_dump(by_alias=True) for a in rows]
    return ok(paginated(items, total, page, pageSize), trace_id=request.state.trace_id)


@articles_router.get("/{article_id}")
async def article_detail(
    article_id: int,
    request: Request,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    article = await get_public_article_or_404(db, article_id)
    favorite_ids = await get_favorite_ids(db, user.id, [article.id]) if user else set()
    await increment_view_count(db, article)
    return ok(
        ArticleDetailOut(**article_detail_to_out(article, favorite_ids)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


@articles_router.post("/{article_id}/favorite")
async def favorite_article(
    article_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    is_favorite = await toggle_favorite(db, user.id, article_id)
    return ok({"isFavorite": is_favorite}, trace_id=request.state.trace_id)


@categories_router.get("")
async def article_categories(request: Request, db: AsyncSession = Depends(get_async_db)) -> dict:
    items = [
        {"id": c.id, "name": c.name, "sortOrder": c.sort_order}
        for c in await list_categories(db)
    ]
    return ok({"items": items}, trace_id=request.state.trace_id)
