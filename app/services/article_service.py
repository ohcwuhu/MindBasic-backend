"""科普文章业务逻辑。"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.models.content import Article, ArticleCategory, ArticleFavorite
from app.services.content_sanitizer import sanitize_html
from app.utils.time import to_iso


async def get_public_articles(
    db: AsyncSession,
    category_id: int | None,
    keyword: str | None,
    page: int,
    page_size: int,
) -> tuple[list[Article], int]:
    stmt = select(Article).where(
        Article.status == "PUBLISHED",
        Article.deleted_at.is_(None),
    )
    if category_id:
        stmt = stmt.where(Article.category_id == category_id)
    if keyword:
        stmt = stmt.where(Article.title.like(f"%{keyword}%"))
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        await db.scalars(
            stmt.order_by(Article.is_pinned.desc(), Article.published_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return rows, total


async def get_public_article_or_404(db: AsyncSession, article_id: int) -> Article:
    article = await db.scalar(
        select(Article).where(
            Article.id == article_id,
            Article.status == "PUBLISHED",
            Article.deleted_at.is_(None),
        )
    )
    if article is None:
        raise AppError(404, "ARTICLE_NOT_FOUND", "文章不存在或已下架")
    return article


async def increment_view_count(db: AsyncSession, article: Article) -> None:
    article.view_count += 1
    await db.commit()
    await db.refresh(article)


async def list_categories(db: AsyncSession) -> list[ArticleCategory]:
    stmt = (
        select(ArticleCategory)
        .where(ArticleCategory.is_enabled.is_(True))
        .order_by(ArticleCategory.sort_order)
    )
    return list(await db.scalars(stmt))


async def get_favorite_ids(db: AsyncSession, user_id: int, article_ids: list[int]) -> set[int]:
    if not article_ids:
        return set()
    rows = await db.scalars(
        select(ArticleFavorite.article_id).where(
            ArticleFavorite.user_id == user_id,
            ArticleFavorite.article_id.in_(article_ids),
        )
    )
    return set(rows)


async def toggle_favorite(db: AsyncSession, user_id: int, article_id: int) -> bool:
    await get_public_article_or_404(db, article_id)
    favorite = await db.scalar(
        select(ArticleFavorite).where(
            ArticleFavorite.user_id == user_id,
            ArticleFavorite.article_id == article_id,
        )
    )
    if favorite is not None:
        await db.delete(favorite)
        await db.commit()
        return False
    db.add(ArticleFavorite(user_id=user_id, article_id=article_id))
    await db.commit()
    return True


def list_my_favorites(
    db: Session, user_id: int, page: int, page_size: int
) -> tuple[list[Article], int]:
    base = (
        select(Article)
        .join(ArticleFavorite, ArticleFavorite.article_id == Article.id)
        .where(
            ArticleFavorite.user_id == user_id,
            Article.status == "PUBLISHED",
            Article.deleted_at.is_(None),
        )
    )
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = list(
        db.scalars(
            base.order_by(ArticleFavorite.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return rows, total


def article_to_out(article: Article, favorite_ids: set[int] | None = None) -> dict:
    return {
        "id": article.id,
        "title": article.title,
        "summary": article.summary,
        "cover_url": article.cover_url,
        "category_id": article.category_id,
        "is_pinned": bool(article.is_pinned),
        "is_favorite": article.id in (favorite_ids or set()),
        "published_at": to_iso(article.published_at),
    }


def article_detail_to_out(article: Article, favorite_ids: set[int] | None = None) -> dict:
    data = article_to_out(article, favorite_ids)
    data["content"] = sanitize_html(article.content)
    data["view_count"] = article.view_count
    return data
