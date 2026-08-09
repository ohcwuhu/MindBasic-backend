"""科普文章业务逻辑。"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.models.content import Article, ArticleCategory, ArticleFavorite
from app.utils.time import to_iso


def get_public_articles(
    db: Session,
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
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        db.scalars(
            stmt.order_by(Article.is_pinned.desc(), Article.published_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return rows, total


def get_public_article_or_404(db: Session, article_id: int) -> Article:
    article = db.scalar(
        select(Article).where(
            Article.id == article_id,
            Article.status == "PUBLISHED",
            Article.deleted_at.is_(None),
        )
    )
    if article is None:
        raise AppError(404, "ARTICLE_NOT_FOUND", "文章不存在或已下架")
    return article


def increment_view_count(db: Session, article: Article) -> None:
    article.view_count += 1
    db.commit()
    db.refresh(article)


def list_categories(db: Session) -> list[ArticleCategory]:
    stmt = (
        select(ArticleCategory)
        .where(ArticleCategory.is_enabled.is_(True))
        .order_by(ArticleCategory.sort_order)
    )
    return list(db.scalars(stmt))


def get_favorite_ids(db: Session, user_id: int, article_ids: list[int]) -> set[int]:
    if not article_ids:
        return set()
    rows = db.scalars(
        select(ArticleFavorite.article_id).where(
            ArticleFavorite.user_id == user_id,
            ArticleFavorite.article_id.in_(article_ids),
        )
    )
    return set(rows)


def toggle_favorite(db: Session, user_id: int, article_id: int) -> bool:
    get_public_article_or_404(db, article_id)
    favorite = db.scalar(
        select(ArticleFavorite).where(
            ArticleFavorite.user_id == user_id,
            ArticleFavorite.article_id == article_id,
        )
    )
    if favorite is not None:
        db.delete(favorite)
        db.commit()
        return False
    db.add(ArticleFavorite(user_id=user_id, article_id=article_id))
    db.commit()
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
    data["content"] = article.content
    data["view_count"] = article.view_count
    return data
