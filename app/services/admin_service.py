"""管理后台业务逻辑。"""

from datetime import datetime, time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.models.coach import CoachProfile, CoachTag
from app.models.content import Article, ArticleCategory, ArticleFavorite, Banner
from app.models.growth import EmotionFeedbackLib
from app.models.user import AdminActionLog, User
from app.schemas.admin import (
    ArticleAdminIn,
    ArticleAdminPatchIn,
    BannerIn,
    BannerPatchIn,
    CategoryIn,
    CategoryPatchIn,
    FeedbackIn,
    FeedbackPatchIn,
    TagIn,
    TagPatchIn,
)
from app.services.content_guard import check_banned_words
from app.services.content_sanitizer import sanitize_html
from app.utils.time import to_iso, utcnow_naive


def write_admin_log(
    db: Session, admin: User, action: str, target_type: str, target_id: int, detail: dict | None = None
) -> None:
    db.add(AdminActionLog(
        admin_id=admin.id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        detail=detail,
    ))


# ---------- 用户管理 ----------


def list_users(
    db: Session, keyword: str | None, role: str | None, status: str | None, page: int, page_size: int
) -> tuple[list[User], int]:
    stmt = select(User).where(User.deleted_at.is_(None))
    if keyword:
        stmt = stmt.where(User.phone.like(f"%{keyword}%") | User.nickname.like(f"%{keyword}%"))
    if role:
        stmt = stmt.where(User.role == role)
    if status:
        stmt = stmt.where(User.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        db.scalars(
            stmt.order_by(User.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return rows, total


def set_user_status(db: Session, admin: User, user_id: int, status: str) -> User:
    if user_id == admin.id:
        raise AppError(409, "CONFLICT", "不能修改自己的账号状态")
    user = db.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise AppError(404, "NOT_FOUND", "用户不存在")
    user.status = status
    write_admin_log(
        db,
        admin,
        action="USER_ENABLE" if status == "ENABLED" else "USER_DISABLE",
        target_type="USER",
        target_id=user.id,
        detail={"from": "DISABLED" if status == "ENABLED" else "ENABLED", "to": status},
    )
    db.commit()
    db.refresh(user)
    return user


def user_to_admin_out(user: User) -> dict:
    return {
        "id": user.id,
        "phone": (user.phone[:3] + "****" + user.phone[-4:]) if len(user.phone) == 11 else user.phone,
        "nickname": user.nickname,
        "role": user.role,
        "is_disabled": user.status != "ENABLED",
        "created_at": to_iso(user.created_at),
        "last_login_at": to_iso(user.last_login_at),
    }


# ---------- 文章管理 ----------


def list_admin_articles(
    db: Session,
    status: str | None,
    category_id: int | None,
    keyword: str | None,
    page: int,
    page_size: int,
) -> tuple[list[Article], int]:
    stmt = select(Article).where(Article.deleted_at.is_(None))
    if status:
        stmt = stmt.where(Article.status == status)
    if category_id:
        stmt = stmt.where(Article.category_id == category_id)
    if keyword:
        stmt = stmt.where(Article.title.like(f"%{keyword}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        db.scalars(
            stmt.order_by(Article.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return rows, total


def get_admin_article_or_404(db: Session, article_id: int) -> Article:
    article = db.scalar(
        select(Article).where(Article.id == article_id, Article.deleted_at.is_(None))
    )
    if article is None:
        raise AppError(404, "ARTICLE_NOT_FOUND", "文章不存在")
    return article


def article_to_admin_out(article: Article) -> dict:
    return {
        "id": article.id,
        "title": article.title,
        "summary": article.summary,
        "content": article.content,
        "cover_url": article.cover_url,
        "category_id": article.category_id,
        "is_pinned": bool(article.is_pinned),
        "status": article.status,
        "view_count": article.view_count,
        "published_at": to_iso(article.published_at),
        "created_at": to_iso(article.created_at),
        "updated_at": to_iso(article.updated_at),
    }


def create_article(db: Session, data: ArticleAdminIn) -> Article:
    check_banned_words(data.title, data.summary, data.content)
    article = Article(
        title=data.title.strip(),
        summary=data.summary,
        content=sanitize_html(data.content),
        cover_url=data.cover_url,
        category_id=data.category_id,
        is_pinned=data.is_pinned,
        status=data.status,
        view_count=0,
        published_at=utcnow_naive() if data.status == "PUBLISHED" else None,
    )
    db.add(article)
    db.commit()
    db.refresh(article)
    return article


def update_article(db: Session, article_id: int, data: ArticleAdminPatchIn) -> Article:
    article = get_admin_article_or_404(db, article_id)
    changes = data.model_dump(exclude_unset=True)
    if not changes:
        return article
    check_banned_words(
        changes.get("title", article.title),
        changes.get("summary", article.summary),
        changes.get("content", article.content),
    )
    for field in ("title", "summary", "content", "cover_url", "category_id", "is_pinned", "status"):
        if field in changes:
            if field == "content":
                changes[field] = sanitize_html(changes[field])
            setattr(article, field, changes[field])
    if changes.get("status") == "PUBLISHED" and article.published_at is None:
        article.published_at = utcnow_naive()
    db.commit()
    db.refresh(article)
    return article


def delete_article(db: Session, article_id: int) -> None:
    article = get_admin_article_or_404(db, article_id)
    article.deleted_at = utcnow_naive()
    db.commit()


# ---------- 文章分类 ----------


def list_admin_categories(db: Session) -> list[ArticleCategory]:
    return list(
        db.scalars(
            select(ArticleCategory).order_by(ArticleCategory.sort_order, ArticleCategory.id)
        )
    )


def get_category_or_404(db: Session, category_id: int) -> ArticleCategory:
    category = db.get(ArticleCategory, category_id)
    if category is None:
        raise AppError(404, "CATEGORY_NOT_FOUND", "分类不存在")
    return category


def create_category(db: Session, data: CategoryIn) -> ArticleCategory:
    exists = db.scalar(select(ArticleCategory.id).where(ArticleCategory.name == data.name.strip()))
    if exists is not None:
        raise AppError(409, "CONFLICT", "分类名称已存在")
    category = ArticleCategory(
        name=data.name.strip(),
        sort_order=data.sort_order,
        is_enabled=data.is_enabled,
    )
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


def update_category(db: Session, category_id: int, data: CategoryPatchIn) -> ArticleCategory:
    category = get_category_or_404(db, category_id)
    changes = data.model_dump(exclude_unset=True)
    if "name" in changes:
        dup = db.scalar(
            select(ArticleCategory.id).where(
                ArticleCategory.name == changes["name"].strip(),
                ArticleCategory.id != category_id,
            )
        )
        if dup is not None:
            raise AppError(409, "CONFLICT", "分类名称已存在")
        category.name = changes["name"].strip()
    if "sort_order" in changes:
        category.sort_order = changes["sort_order"]
    if "is_enabled" in changes:
        category.is_enabled = changes["is_enabled"]
    db.commit()
    db.refresh(category)
    return category


def delete_category(db: Session, category_id: int) -> None:
    category = get_category_or_404(db, category_id)
    article_count = db.scalar(
        select(func.count()).select_from(Article).where(
            Article.category_id == category_id,
            Article.deleted_at.is_(None),
        )
    ) or 0
    if article_count:
        raise AppError(409, "CONFLICT", "分类下存在文章，无法删除")
    db.delete(category)
    db.commit()


def category_to_out(category: ArticleCategory) -> dict:
    return {
        "id": category.id,
        "name": category.name,
        "sort_order": category.sort_order,
        "is_enabled": bool(category.is_enabled),
    }


# ---------- 轮播图 ----------


def list_admin_banners(db: Session) -> list[Banner]:
    return list(
        db.scalars(select(Banner).order_by(Banner.sort_order, Banner.id))
    )


def get_banner_or_404(db: Session, banner_id: int) -> Banner:
    banner = db.get(Banner, banner_id)
    if banner is None:
        raise AppError(404, "NOT_FOUND", "轮播图不存在")
    return banner


def parse_dt(value: str | None):
    return datetime.fromisoformat(value) if value else None


def create_banner(db: Session, data: BannerIn) -> Banner:
    banner = Banner(
        title=data.title.strip(),
        image_url=data.image_url.strip(),
        link_type=data.link_type,
        link_value=data.link_value,
        sort_order=data.sort_order,
        is_enabled=data.is_enabled,
        start_at=parse_dt(data.start_at),
        end_at=parse_dt(data.end_at),
    )
    db.add(banner)
    db.commit()
    db.refresh(banner)
    return banner


def update_banner(db: Session, banner_id: int, data: BannerPatchIn) -> Banner:
    banner = get_banner_or_404(db, banner_id)
    changes = data.model_dump(exclude_unset=True)
    for field in ("title", "image_url", "link_type", "link_value", "sort_order", "is_enabled"):
        if field in changes:
            setattr(banner, field, changes[field])
    if "start_at" in changes:
        banner.start_at = parse_dt(changes["start_at"])
    if "end_at" in changes:
        banner.end_at = parse_dt(changes["end_at"])
    db.commit()
    db.refresh(banner)
    return banner


def delete_banner(db: Session, banner_id: int) -> None:
    banner = get_banner_or_404(db, banner_id)
    db.delete(banner)
    db.commit()


def banner_to_admin_out(banner: Banner) -> dict:
    return {
        "id": banner.id,
        "title": banner.title,
        "image_url": banner.image_url,
        "link_type": banner.link_type,
        "link_value": banner.link_value,
        "sort_order": banner.sort_order,
        "is_enabled": bool(banner.is_enabled),
        "start_at": to_iso(banner.start_at),
        "end_at": to_iso(banner.end_at),
        "created_at": to_iso(banner.created_at),
    }


# ---------- 标签 ----------


def list_admin_tags(db: Session, tag_type: str | None) -> list:
    from app.models.coach import Tag

    stmt = select(Tag).order_by(Tag.type, Tag.sort_order, Tag.id)
    if tag_type:
        stmt = stmt.where(Tag.type == tag_type)
    return list(db.scalars(stmt))


def get_tag_or_404(db: Session, tag_id: int):
    from app.models.coach import Tag

    tag = db.get(Tag, tag_id)
    if tag is None:
        raise AppError(404, "NOT_FOUND", "标签不存在")
    return tag


def create_tag(db: Session, data: TagIn):
    from app.models.coach import Tag

    dup = db.scalar(
        select(Tag.id).where(Tag.name == data.name.strip(), Tag.type == data.type)
    )
    if dup is not None:
        raise AppError(409, "CONFLICT", "同类标签名称已存在")
    tag = Tag(
        name=data.name.strip(),
        type=data.type,
        sort_order=data.sort_order,
        is_enabled=data.is_enabled,
    )
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return tag


def update_tag(db: Session, tag_id: int, data: TagPatchIn):
    from app.models.coach import Tag

    tag = get_tag_or_404(db, tag_id)
    changes = data.model_dump(exclude_unset=True)
    if "name" in changes:
        dup = db.scalar(
            select(Tag.id).where(
                Tag.name == changes["name"].strip(),
                Tag.type == tag.type,
                Tag.id != tag_id,
            )
        )
        if dup is not None:
            raise AppError(409, "CONFLICT", "同类标签名称已存在")
        tag.name = changes["name"].strip()
    if "sort_order" in changes:
        tag.sort_order = changes["sort_order"]
    if "is_enabled" in changes:
        tag.is_enabled = changes["is_enabled"]
    db.commit()
    db.refresh(tag)
    return tag


def delete_tag(db: Session, tag_id: int) -> None:
    tag = get_tag_or_404(db, tag_id)
    used = db.scalar(select(func.count()).select_from(CoachTag).where(CoachTag.tag_id == tag_id)) or 0
    if used:
        raise AppError(409, "CONFLICT", "标签已被教练使用，无法删除")
    db.delete(tag)
    db.commit()


def tag_to_admin_out(tag) -> dict:
    return {
        "id": tag.id,
        "name": tag.name,
        "type": tag.type,
        "sort_order": tag.sort_order,
        "is_enabled": bool(tag.is_enabled),
    }


# ---------- 情绪话术库 ----------


def list_admin_feedback(db: Session, mood_type: str | None) -> list[EmotionFeedbackLib]:
    stmt = select(EmotionFeedbackLib).order_by(EmotionFeedbackLib.mood_type, EmotionFeedbackLib.sort_order)
    if mood_type:
        stmt = stmt.where(EmotionFeedbackLib.mood_type == mood_type)
    return list(db.scalars(stmt))


def get_feedback_or_404(db: Session, item_id: int) -> EmotionFeedbackLib:
    item = db.get(EmotionFeedbackLib, item_id)
    if item is None:
        raise AppError(404, "NOT_FOUND", "话术不存在")
    return item


def create_feedback(db: Session, data: FeedbackIn) -> EmotionFeedbackLib:
    item = EmotionFeedbackLib(
        mood_type=data.mood_type,
        content=data.content.strip(),
        sort_order=data.sort_order,
        is_enabled=data.is_enabled,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def update_feedback(db: Session, item_id: int, data: FeedbackPatchIn) -> EmotionFeedbackLib:
    item = get_feedback_or_404(db, item_id)
    changes = data.model_dump(exclude_unset=True)
    if "content" in changes:
        item.content = changes["content"].strip()
    if "sort_order" in changes:
        item.sort_order = changes["sort_order"]
    if "is_enabled" in changes:
        item.is_enabled = changes["is_enabled"]
    db.commit()
    db.refresh(item)
    return item


def delete_feedback(db: Session, item_id: int) -> None:
    item = get_feedback_or_404(db, item_id)
    db.delete(item)
    db.commit()


def feedback_to_out(item: EmotionFeedbackLib) -> dict:
    return {
        "id": item.id,
        "mood_type": item.mood_type,
        "content": item.content,
        "sort_order": item.sort_order,
        "is_enabled": bool(item.is_enabled),
    }


# ---------- 统计 ----------


def admin_stats(db: Session) -> dict:
    today_start = datetime.combine(datetime.now().date(), time.min)
    user_count = db.scalar(select(func.count()).select_from(User).where(User.deleted_at.is_(None))) or 0
    coach_count = db.scalar(select(func.count()).select_from(CoachProfile).where(CoachProfile.deleted_at.is_(None))) or 0
    approved_coach_count = db.scalar(
        select(func.count()).select_from(CoachProfile).where(
            CoachProfile.audit_status == "APPROVED",
            CoachProfile.deleted_at.is_(None),
        )
    ) or 0
    from app.models.coach import Appointment

    appointment_count = db.scalar(select(func.count()).select_from(Appointment)) or 0
    pending_appointment_count = db.scalar(
        select(func.count()).select_from(Appointment).where(Appointment.status == "PENDING")
    ) or 0
    article_count = db.scalar(
        select(func.count()).select_from(Article).where(
            Article.status == "PUBLISHED",
            Article.deleted_at.is_(None),
        )
    ) or 0
    today_user_count = db.scalar(
        select(func.count()).select_from(User).where(User.created_at >= today_start)
    ) or 0
    today_appointment_count = db.scalar(
        select(func.count()).select_from(Appointment).where(Appointment.created_at >= today_start)
    ) or 0
    return {
        "user_count": user_count,
        "coach_count": coach_count,
        "approved_coach_count": approved_coach_count,
        "appointment_count": appointment_count,
        "pending_appointment_count": pending_appointment_count,
        "article_count": article_count,
        "today_user_count": today_user_count,
        "today_appointment_count": today_appointment_count,
    }
