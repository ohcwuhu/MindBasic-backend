"""首页聚合数据。"""

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.coach import CoachProfile, CoachTag, Tag
from app.models.content import Article, ArticleFavorite, Banner
from app.models.user import User
from app.utils.time import to_iso

QUICK_ENTRIES = [
    {"key": "self_coaching", "title": "自我教练", "icon": "self-coaching", "path": "/self-coaching"},
    {"key": "emotion_journal", "title": "情绪日记", "icon": "emotion-journal", "path": "/emotion-journals"},
    {"key": "coaches", "title": "找教练", "icon": "coaches", "path": "/coaches"},
    {"key": "science", "title": "科普中心", "icon": "science", "path": "/articles"},
]


def get_banners(db: Session) -> list[Banner]:
    stmt = (
        select(Banner)
        .where(Banner.is_enabled.is_(True))
        .order_by(Banner.sort_order.asc(), Banner.id.asc())
    )
    return list(db.scalars(stmt))


def get_featured_articles(db: Session, limit: int = 5) -> list[Article]:
    stmt = (
        select(Article)
        .where(
            Article.status == "PUBLISHED",
            Article.deleted_at.is_(None),
        )
        .order_by(Article.is_pinned.desc(), Article.published_at.desc())
        .limit(limit)
    )
    return list(db.scalars(stmt))


def get_recommended_coaches(db: Session, limit: int = 3) -> list[dict]:
    coaches = list(
        db.scalars(
            select(CoachProfile)
            .where(
                CoachProfile.audit_status == "APPROVED",
                CoachProfile.deleted_at.is_(None),
            )
            .order_by(CoachProfile.rating.desc(), CoachProfile.review_count.desc())
            .limit(limit)
        )
    )
    if not coaches:
        return []

    coach_ids = [c.id for c in coaches]
    user_ids = [c.user_id for c in coaches]

    tag_rows = db.execute(
        select(CoachTag.coach_id, Tag.name)
        .join(Tag, Tag.id == CoachTag.tag_id)
        .where(CoachTag.coach_id.in_(coach_ids))
    ).all()
    tags_map: dict[int, list[str]] = defaultdict(list)
    for coach_id, name in tag_rows:
        tags_map[coach_id].append(name)

    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_(user_ids)))}

    return [
        {
            "id": c.id,
            "nickname": users[c.user_id].nickname if c.user_id in users else "",
            "avatar_url": users[c.user_id].avatar_url if c.user_id in users else None,
            "tag_names": tags_map.get(c.id, []),
            "years_of_experience": c.years_of_experience,
            "rating": float(c.rating),
            "review_count": c.review_count,
            "service_concept": c.service_concept,
        }
        for c in coaches
    ]


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
