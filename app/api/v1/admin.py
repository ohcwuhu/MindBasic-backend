from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, require_role
from app.api.response import ok, paginated
from app.core.cache import invalidate_home
from app.models.user import User
from app.schemas.coach import AuditListOut, AuditOut, AuditRejectIn
from app.utils.format import mask_phone
from app.services.coach_service import (
    approve_audit,
    get_audit_or_404,
    list_audits,
    reject_audit,
)
from app.utils.time import to_iso
from app.schemas.admin import (
    AdminUserOut,
    ArticleAdminIn,
    ArticleAdminOut,
    ArticleAdminPatchIn,
    BannerAdminOut,
    BannerIn,
    BannerPatchIn,
    CategoryIn,
    CategoryOut,
    CategoryPatchIn,
    FeedbackAdminOut,
    FeedbackIn,
    FeedbackPatchIn,
    StatsOut,
    TagAdminOut,
    TagIn,
    TagPatchIn,
    UserStatusIn,
)
from app.schemas.system_config import SystemConfigUpdateIn
from app.services import admin_service
from app.services.system_config_service import get_all_configs, update_configs

router = APIRouter(prefix="/admin/coach-audits", tags=["admin-coach-audits"])


def audit_list_to_out(audit, user) -> dict:
    return AuditListOut(
        id=audit.id,
        coach_id=audit.coach_id,
        coach_name=user.nickname,
        submit_version=audit.submit_version,
        status=audit.status,
        remark=audit.remark,
        submitted_at=to_iso(audit.submitted_at),
        reviewed_at=to_iso(audit.reviewed_at),
    ).model_dump(by_alias=True)


def audit_detail_to_out(audit, user) -> dict:
    return AuditOut(
        id=audit.id,
        coach_id=audit.coach_id,
        coach_name=user.nickname,
        phone=mask_phone(user.phone),
        submit_version=audit.submit_version,
        status=audit.status,
        remark=audit.remark,
        snapshot=audit.profile_snapshot,
        submitted_at=to_iso(audit.submitted_at),
        reviewed_at=to_iso(audit.reviewed_at),
    ).model_dump(by_alias=True)


@router.get("")
async def list_audits_endpoint(
    request: Request,
    status: str | None = Query(default=None, pattern="^(PENDING|APPROVED|REJECTED)$"),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=10, ge=1, le=50, alias="pageSize"),
    admin: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    rows, total = await list_audits(db, status, page, pageSize)
    items = [audit_list_to_out(audit, user) for audit, user in rows]
    return ok(paginated(items, total, page, pageSize), trace_id=request.state.trace_id)


@router.get("/{audit_id}")
async def audit_detail(
    audit_id: int,
    request: Request,
    admin: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    audit, user = await get_audit_or_404(db, audit_id)
    return ok(audit_detail_to_out(audit, user), trace_id=request.state.trace_id)


@router.post("/{audit_id}/approve")
async def approve(
    audit_id: int,
    request: Request,
    admin: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    audit = await approve_audit(db, admin, audit_id)
    invalidate_home()
    return ok({"id": audit.id, "status": audit.status}, trace_id=request.state.trace_id)


@router.post("/{audit_id}/reject")
async def reject(
    audit_id: int,
    body: AuditRejectIn,
    request: Request,
    admin: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    audit = await reject_audit(db, admin, audit_id, body.reason)
    invalidate_home()
    return ok({"id": audit.id, "status": audit.status, "remark": audit.remark}, trace_id=request.state.trace_id)


users_router = APIRouter(prefix="/admin/users", tags=["admin-users"])
articles_router = APIRouter(prefix="/admin/articles", tags=["admin-articles"])
categories_router = APIRouter(prefix="/admin/article-categories", tags=["admin-categories"])
banners_router = APIRouter(prefix="/admin/banners", tags=["admin-banners"])
tags_router = APIRouter(prefix="/admin/tags", tags=["admin-tags"])
feedback_router = APIRouter(prefix="/admin/feedback-lib", tags=["admin-feedback"])
stats_router = APIRouter(prefix="/admin/stats", tags=["admin-stats"])


@users_router.get("")
async def admin_users(
    request: Request,
    keyword: str | None = Query(default=None, max_length=50),
    role: str | None = Query(default=None, pattern="^(USER|COACH|ADMIN)$"),
    status: str | None = Query(default=None, pattern="^(ENABLED|DISABLED)$"),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=10, ge=1, le=50, alias="pageSize"),
    admin: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    rows, total = await admin_service.list_users(db, keyword, role, status, page, pageSize)
    items = [AdminUserOut(**admin_service.user_to_admin_out(u)).model_dump(by_alias=True) for u in rows]
    return ok(paginated(items, total, page, pageSize), trace_id=request.state.trace_id)


@users_router.patch("/{user_id}/status")
async def admin_user_status(
    user_id: int,
    body: UserStatusIn,
    request: Request,
    admin: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    user = await admin_service.set_user_status(db, admin, user_id, body.status)
    return ok(
        {"id": user.id, "isDisabled": user.status != "ENABLED"},
        trace_id=request.state.trace_id,
    )


@articles_router.get("")
async def admin_articles(
    request: Request,
    status: str | None = Query(default=None, pattern="^(PUBLISHED|DRAFT|OFFLINE)$"),
    categoryId: int | None = Query(default=None, alias="categoryId"),
    keyword: str | None = Query(default=None, max_length=50),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=10, ge=1, le=50, alias="pageSize"),
    admin: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    rows, total = await admin_service.list_admin_articles(db, status, categoryId, keyword, page, pageSize)
    items = [ArticleAdminOut(**admin_service.article_to_admin_out(a)).model_dump(by_alias=True) for a in rows]
    return ok(paginated(items, total, page, pageSize), trace_id=request.state.trace_id)


@articles_router.post("", status_code=201)
async def admin_create_article(
    body: ArticleAdminIn,
    request: Request,
    admin: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    article = await admin_service.create_article(db, body)
    invalidate_home()
    return ok(
        ArticleAdminOut(**admin_service.article_to_admin_out(article)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


@articles_router.patch("/{article_id}")
async def admin_update_article(
    article_id: int,
    body: ArticleAdminPatchIn,
    request: Request,
    admin: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    article = await admin_service.update_article(db, article_id, body)
    invalidate_home()
    return ok(
        ArticleAdminOut(**admin_service.article_to_admin_out(article)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


@articles_router.delete("/{article_id}", status_code=204)
async def admin_delete_article(
    article_id: int,
    request: Request,
    admin: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_async_db),
) -> None:
    await admin_service.delete_article(db, article_id)
    invalidate_home()


@categories_router.get("")
async def admin_categories(
    request: Request,
    admin: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    items = [CategoryOut(**admin_service.category_to_out(c)).model_dump(by_alias=True)
             for c in await admin_service.list_admin_categories(db)]
    return ok({"items": items}, trace_id=request.state.trace_id)


@categories_router.post("", status_code=201)
async def admin_create_category(
    body: CategoryIn,
    request: Request,
    admin: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    category = await admin_service.create_category(db, body)
    return ok(
        CategoryOut(**admin_service.category_to_out(category)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


@categories_router.patch("/{category_id}")
async def admin_update_category(
    category_id: int,
    body: CategoryPatchIn,
    request: Request,
    admin: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    category = await admin_service.update_category(db, category_id, body)
    return ok(
        CategoryOut(**admin_service.category_to_out(category)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


@categories_router.delete("/{category_id}", status_code=204)
async def admin_delete_category(
    category_id: int,
    request: Request,
    admin: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_async_db),
) -> None:
    await admin_service.delete_category(db, category_id)


@banners_router.get("")
async def admin_banners(
    request: Request,
    admin: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    items = [BannerAdminOut(**admin_service.banner_to_admin_out(b)).model_dump(by_alias=True)
             for b in await admin_service.list_admin_banners(db)]
    return ok({"items": items}, trace_id=request.state.trace_id)


@banners_router.post("", status_code=201)
async def admin_create_banner(
    body: BannerIn,
    request: Request,
    admin: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    banner = await admin_service.create_banner(db, body)
    invalidate_home()
    return ok(
        BannerAdminOut(**admin_service.banner_to_admin_out(banner)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


@banners_router.patch("/{banner_id}")
async def admin_update_banner(
    banner_id: int,
    body: BannerPatchIn,
    request: Request,
    admin: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    banner = await admin_service.update_banner(db, banner_id, body)
    invalidate_home()
    return ok(
        BannerAdminOut(**admin_service.banner_to_admin_out(banner)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


@banners_router.delete("/{banner_id}", status_code=204)
async def admin_delete_banner(
    banner_id: int,
    request: Request,
    admin: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_async_db),
) -> None:
    await admin_service.delete_banner(db, banner_id)
    invalidate_home()


@tags_router.get("")
async def admin_tags(
    request: Request,
    type: str | None = Query(default=None, pattern="^(FIELD|AUDIENCE)$"),
    admin: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    items = [TagAdminOut(**admin_service.tag_to_admin_out(t)).model_dump(by_alias=True)
             for t in await admin_service.list_admin_tags(db, type)]
    return ok({"items": items}, trace_id=request.state.trace_id)


@tags_router.post("", status_code=201)
async def admin_create_tag(
    body: TagIn,
    request: Request,
    admin: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    tag = await admin_service.create_tag(db, body)
    invalidate_home()
    return ok(
        TagAdminOut(**admin_service.tag_to_admin_out(tag)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


@tags_router.patch("/{tag_id}")
async def admin_update_tag(
    tag_id: int,
    body: TagPatchIn,
    request: Request,
    admin: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    tag = await admin_service.update_tag(db, tag_id, body)
    invalidate_home()
    return ok(
        TagAdminOut(**admin_service.tag_to_admin_out(tag)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


@tags_router.delete("/{tag_id}", status_code=204)
async def admin_delete_tag(
    tag_id: int,
    request: Request,
    admin: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_async_db),
) -> None:
    await admin_service.delete_tag(db, tag_id)
    invalidate_home()


@feedback_router.get("")
async def admin_feedback(
    request: Request,
    moodType: str | None = Query(default=None, alias="moodType"),
    admin: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    items = [FeedbackAdminOut(**admin_service.feedback_to_out(f)).model_dump(by_alias=True)
             for f in await admin_service.list_admin_feedback(db, moodType)]
    return ok({"items": items}, trace_id=request.state.trace_id)


@feedback_router.post("", status_code=201)
async def admin_create_feedback(
    body: FeedbackIn,
    request: Request,
    admin: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    item = await admin_service.create_feedback(db, body)
    return ok(
        FeedbackAdminOut(**admin_service.feedback_to_out(item)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


@feedback_router.patch("/{item_id}")
async def admin_update_feedback(
    item_id: int,
    body: FeedbackPatchIn,
    request: Request,
    admin: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    item = await admin_service.update_feedback(db, item_id, body)
    return ok(
        FeedbackAdminOut(**admin_service.feedback_to_out(item)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


@feedback_router.delete("/{item_id}", status_code=204)
async def admin_delete_feedback(
    item_id: int,
    request: Request,
    admin: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_async_db),
) -> None:
    await admin_service.delete_feedback(db, item_id)


@stats_router.get("")
async def admin_stats(
    request: Request,
    admin: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    return ok(
        StatsOut(**await admin_service.admin_stats(db)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


config_router = APIRouter(prefix="/admin/system-configs", tags=["admin-config"])


@config_router.get("")
async def admin_system_configs(
    request: Request,
    admin: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    return ok({"items": await get_all_configs(db)}, trace_id=request.state.trace_id)


@config_router.put("")
async def admin_update_system_configs(
    body: SystemConfigUpdateIn,
    request: Request,
    admin: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    items = [{"key": item.key, "value": item.value} for item in body.items]
    return ok(
        {"items": await update_configs(db, admin, items)},
        trace_id=request.state.trace_id,
    )
