"""管理后台业务逻辑。"""

from datetime import datetime, time

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

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
    db: AsyncSession, admin: User, action: str, target_type: str, target_id: int, detail: dict | None = None
) -> None:
    db.add(AdminActionLog(
        admin_id=admin.id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        detail=detail,
    ))


# ---------- 用户管理 ----------


async def list_users(
    db: AsyncSession,
    keyword: str | None,
    role: str | None,
    status: str | None,
    created_from: str | None,
    created_to: str | None,
    page: int,
    page_size: int,
) -> tuple[list[User], int]:
    stmt = select(User).where(User.deleted_at.is_(None))
    if keyword:
        stmt = stmt.where(User.phone.like(f"%{keyword}%") | User.nickname.like(f"%{keyword}%"))
    if role:
        stmt = stmt.where(User.role == role)
    if status:
        stmt = stmt.where(User.status == status)
    if created_from:
        start = datetime.combine(datetime.strptime(created_from, "%Y-%m-%d").date(), time.min)
        stmt = stmt.where(User.created_at >= start)
    if created_to:
        end = datetime.combine(datetime.strptime(created_to, "%Y-%m-%d").date(), time.max)
        stmt = stmt.where(User.created_at <= end)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        await db.scalars(
            stmt.order_by(User.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return rows, total


async def set_user_status(db: AsyncSession, admin: User, user_id: int, status: str) -> User:
    if user_id == admin.id:
        raise AppError(409, "CONFLICT", "不能修改自己的账号状态")
    user = await db.get(User, user_id)
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
    await db.commit()
    await db.refresh(user)
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


async def list_admin_articles(
    db: AsyncSession,
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
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        await db.scalars(
            stmt.order_by(Article.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return rows, total


async def get_admin_article_or_404(db: AsyncSession, article_id: int) -> Article:
    article = await db.scalar(
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


async def create_article(db: AsyncSession, data: ArticleAdminIn) -> Article:
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
    await db.commit()
    await db.refresh(article)
    return article


async def update_article(db: AsyncSession, article_id: int, data: ArticleAdminPatchIn) -> Article:
    article = await get_admin_article_or_404(db, article_id)
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
    await db.commit()
    await db.refresh(article)
    return article


async def delete_article(db: AsyncSession, article_id: int) -> None:
    article = await get_admin_article_or_404(db, article_id)
    article.deleted_at = utcnow_naive()
    await db.commit()


# ---------- 文章分类 ----------


async def list_admin_categories(db: AsyncSession) -> list[ArticleCategory]:
    return list(
        await db.scalars(
            select(ArticleCategory).order_by(ArticleCategory.sort_order, ArticleCategory.id)
        )
    )


async def get_category_or_404(db: AsyncSession, category_id: int) -> ArticleCategory:
    category = await db.get(ArticleCategory, category_id)
    if category is None:
        raise AppError(404, "CATEGORY_NOT_FOUND", "分类不存在")
    return category


async def create_category(db: AsyncSession, data: CategoryIn) -> ArticleCategory:
    exists = await db.scalar(select(ArticleCategory.id).where(ArticleCategory.name == data.name.strip()))
    if exists is not None:
        raise AppError(409, "CONFLICT", "分类名称已存在")
    category = ArticleCategory(
        name=data.name.strip(),
        sort_order=data.sort_order,
        is_enabled=data.is_enabled,
    )
    db.add(category)
    await db.commit()
    await db.refresh(category)
    return category


async def update_category(db: AsyncSession, category_id: int, data: CategoryPatchIn) -> ArticleCategory:
    category = await get_category_or_404(db, category_id)
    changes = data.model_dump(exclude_unset=True)
    if "name" in changes:
        dup = await db.scalar(
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
    await db.commit()
    await db.refresh(category)
    return category


async def delete_category(db: AsyncSession, category_id: int) -> None:
    category = await get_category_or_404(db, category_id)
    article_count = await db.scalar(
        select(func.count()).select_from(Article).where(
            Article.category_id == category_id,
            Article.deleted_at.is_(None),
        )
    ) or 0
    if article_count:
        raise AppError(409, "CONFLICT", "分类下存在文章，无法删除")
    await db.delete(category)
    await db.commit()


def category_to_out(category: ArticleCategory) -> dict:
    return {
        "id": category.id,
        "name": category.name,
        "sort_order": category.sort_order,
        "is_enabled": bool(category.is_enabled),
    }


# ---------- 轮播图 ----------


async def list_admin_banners(db: AsyncSession) -> list[Banner]:
    return list(
        await db.scalars(select(Banner).order_by(Banner.sort_order, Banner.id))
    )


async def get_banner_or_404(db: AsyncSession, banner_id: int) -> Banner:
    banner = await db.get(Banner, banner_id)
    if banner is None:
        raise AppError(404, "NOT_FOUND", "轮播图不存在")
    return banner


def parse_dt(value: str | None):
    return datetime.fromisoformat(value) if value else None


async def create_banner(db: AsyncSession, data: BannerIn) -> Banner:
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
    await db.commit()
    await db.refresh(banner)
    return banner


async def update_banner(db: AsyncSession, banner_id: int, data: BannerPatchIn) -> Banner:
    banner = await get_banner_or_404(db, banner_id)
    changes = data.model_dump(exclude_unset=True)
    for field in ("title", "image_url", "link_type", "link_value", "sort_order", "is_enabled"):
        if field in changes:
            setattr(banner, field, changes[field])
    if "start_at" in changes:
        banner.start_at = parse_dt(changes["start_at"])
    if "end_at" in changes:
        banner.end_at = parse_dt(changes["end_at"])
    await db.commit()
    await db.refresh(banner)
    return banner


async def delete_banner(db: AsyncSession, banner_id: int) -> None:
    banner = await get_banner_or_404(db, banner_id)
    await db.delete(banner)
    await db.commit()


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


async def list_admin_tags(db: AsyncSession, tag_type: str | None) -> list:
    from app.models.coach import Tag

    stmt = select(Tag).order_by(Tag.type, Tag.sort_order, Tag.id)
    if tag_type:
        stmt = stmt.where(Tag.type == tag_type)
    return list(await db.scalars(stmt))


async def get_tag_or_404(db: AsyncSession, tag_id: int):
    from app.models.coach import Tag

    tag = await db.get(Tag, tag_id)
    if tag is None:
        raise AppError(404, "NOT_FOUND", "标签不存在")
    return tag


async def create_tag(db: AsyncSession, data: TagIn):
    from app.models.coach import Tag

    dup = await db.scalar(
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
    await db.commit()
    await db.refresh(tag)
    return tag


async def update_tag(db: AsyncSession, tag_id: int, data: TagPatchIn):
    from app.models.coach import Tag

    tag = await get_tag_or_404(db, tag_id)
    changes = data.model_dump(exclude_unset=True)
    if "name" in changes:
        dup = await db.scalar(
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
    await db.commit()
    await db.refresh(tag)
    return tag


async def delete_tag(db: AsyncSession, tag_id: int) -> None:
    tag = await get_tag_or_404(db, tag_id)
    used = await db.scalar(select(func.count()).select_from(CoachTag).where(CoachTag.tag_id == tag_id)) or 0
    if used:
        raise AppError(409, "CONFLICT", "标签已被教练使用，无法删除")
    await db.delete(tag)
    await db.commit()


def tag_to_admin_out(tag) -> dict:
    return {
        "id": tag.id,
        "name": tag.name,
        "type": tag.type,
        "sort_order": tag.sort_order,
        "is_enabled": bool(tag.is_enabled),
    }


# ---------- 情绪话术库 ----------


async def list_admin_feedback(db: AsyncSession, mood_type: str | None) -> list[EmotionFeedbackLib]:
    stmt = select(EmotionFeedbackLib).order_by(EmotionFeedbackLib.mood_type, EmotionFeedbackLib.sort_order)
    if mood_type:
        stmt = stmt.where(EmotionFeedbackLib.mood_type == mood_type)
    return list(await db.scalars(stmt))


async def get_feedback_or_404(db: AsyncSession, item_id: int) -> EmotionFeedbackLib:
    item = await db.get(EmotionFeedbackLib, item_id)
    if item is None:
        raise AppError(404, "NOT_FOUND", "话术不存在")
    return item


async def create_feedback(db: AsyncSession, data: FeedbackIn) -> EmotionFeedbackLib:
    item = EmotionFeedbackLib(
        mood_type=data.mood_type,
        content=data.content.strip(),
        sort_order=data.sort_order,
        is_enabled=data.is_enabled,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


async def update_feedback(db: AsyncSession, item_id: int, data: FeedbackPatchIn) -> EmotionFeedbackLib:
    item = await get_feedback_or_404(db, item_id)
    changes = data.model_dump(exclude_unset=True)
    if "content" in changes:
        item.content = changes["content"].strip()
    if "sort_order" in changes:
        item.sort_order = changes["sort_order"]
    if "is_enabled" in changes:
        item.is_enabled = changes["is_enabled"]
    await db.commit()
    await db.refresh(item)
    return item


async def delete_feedback(db: AsyncSession, item_id: int) -> None:
    item = await get_feedback_or_404(db, item_id)
    await db.delete(item)
    await db.commit()


def feedback_to_out(item: EmotionFeedbackLib) -> dict:
    return {
        "id": item.id,
        "mood_type": item.mood_type,
        "content": item.content,
        "sort_order": item.sort_order,
        "is_enabled": bool(item.is_enabled),
    }


# ---------- 统计 ----------


async def admin_stats(db: AsyncSession) -> dict:
    today_start = datetime.combine(datetime.now().date(), time.min)
    user_count = await db.scalar(select(func.count()).select_from(User).where(User.deleted_at.is_(None))) or 0
    coach_count = await db.scalar(select(func.count()).select_from(CoachProfile).where(CoachProfile.deleted_at.is_(None))) or 0
    approved_coach_count = await db.scalar(
        select(func.count()).select_from(CoachProfile).where(
            CoachProfile.audit_status == "APPROVED",
            CoachProfile.deleted_at.is_(None),
        )
    ) or 0
    from app.models.coach import Appointment

    appointment_count = await db.scalar(select(func.count()).select_from(Appointment)) or 0
    pending_appointment_count = await db.scalar(
        select(func.count()).select_from(Appointment).where(Appointment.status == "PENDING")
    ) or 0
    article_count = await db.scalar(
        select(func.count()).select_from(Article).where(
            Article.status == "PUBLISHED",
            Article.deleted_at.is_(None),
        )
    ) or 0
    today_user_count = await db.scalar(
        select(func.count()).select_from(User).where(User.created_at >= today_start)
    ) or 0
    today_appointment_count = await db.scalar(
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
