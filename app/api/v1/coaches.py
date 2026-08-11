"""公开教练目录：列表、详情、可预约时段。"""

from collections import defaultdict
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db
from app.api.response import ok, paginated
from app.core.exceptions import AppError
from app.models.coach import CoachProfile, CoachSlot, CoachTag, Service, Tag
from app.models.user import User
from app.schemas.review import ReviewOut
from app.services.review_service import coach_reviews, review_to_out
from app.utils.time import to_iso

router = APIRouter(prefix="/coaches", tags=["coaches"])


async def get_approved_coach_or_404(db: AsyncSession, coach_id: int) -> CoachProfile:
    coach = await db.scalar(
        select(CoachProfile).where(
            CoachProfile.id == coach_id,
            CoachProfile.audit_status == "APPROVED",
            CoachProfile.deleted_at.is_(None),
        )
    )
    if coach is None:
        raise AppError(404, "COACH_NOT_FOUND", "教练不存在或暂未上架")
    return coach


async def coach_tags(db: AsyncSession, coach_id: int) -> list[str]:
    return list(
        await db.scalars(
            select(Tag.name)
            .join(CoachTag, CoachTag.tag_id == Tag.id)
            .where(CoachTag.coach_id == coach_id)
            .order_by(Tag.sort_order)
        )
    )


async def coach_brief(db: AsyncSession, coach: CoachProfile) -> dict:
    user = await db.get(User, coach.user_id)
    return {
        "id": coach.id,
        "nickname": user.nickname if user else "",
        "avatarUrl": user.avatar_url if user else None,
        "tagNames": await coach_tags(db, coach.id),
        "yearsOfExperience": coach.years_of_experience,
        "rating": float(coach.rating),
        "reviewCount": coach.review_count,
        "serviceConcept": coach.service_concept,
    }


async def coach_briefs(db: AsyncSession, coaches: list[CoachProfile]) -> list[dict]:
    """批量构造教练摘要（避免列表接口 N+1）。"""
    if not coaches:
        return []
    coach_ids = [c.id for c in coaches]
    user_ids = [c.user_id for c in coaches]

    tag_rows = (await db.execute(
        select(CoachTag.coach_id, Tag.name)
        .join(Tag, Tag.id == CoachTag.tag_id)
        .where(CoachTag.coach_id.in_(coach_ids))
        .order_by(Tag.sort_order)
    )).all()
    tags_map: dict[int, list[str]] = defaultdict(list)
    for coach_id, name in tag_rows:
        tags_map[coach_id].append(name)

    users = {u.id: u for u in await db.scalars(select(User).where(User.id.in_(user_ids)))}
    return [
        {
            "id": c.id,
            "nickname": users[c.user_id].nickname if c.user_id in users else "",
            "avatarUrl": users[c.user_id].avatar_url if c.user_id in users else None,
            "tagNames": tags_map.get(c.id, []),
            "yearsOfExperience": c.years_of_experience,
            "rating": float(c.rating),
            "reviewCount": c.review_count,
            "serviceConcept": c.service_concept,
        }
        for c in coaches
    ]


@router.get("")
async def list_coaches(
    request: Request,
    keyword: str | None = Query(default=None, max_length=32),
    tagId: int | None = Query(default=None, alias="tagId"),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=10, ge=1, le=50, alias="pageSize"),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    stmt = (
        select(CoachProfile)
        .where(
            CoachProfile.audit_status == "APPROVED",
            CoachProfile.deleted_at.is_(None),
        )
    )
    if keyword:
        stmt = stmt.join(User, User.id == CoachProfile.user_id).where(User.nickname.like(f"%{keyword}%"))
    if tagId:
        stmt = stmt.where(
            CoachProfile.id.in_(
                select(CoachTag.coach_id).where(CoachTag.tag_id == tagId)
            )
        )
    total = len(list(await db.scalars(stmt)))
    coaches = list(
        await db.scalars(
            stmt.order_by(CoachProfile.rating.desc(), CoachProfile.id.desc())
            .offset((page - 1) * pageSize)
            .limit(pageSize)
        )
    )
    items = await coach_briefs(db, coaches)
    return ok(paginated(items, total, page, pageSize), trace_id=request.state.trace_id)


@router.get("/{coach_id}")
async def coach_detail(coach_id: int, request: Request, db: AsyncSession = Depends(get_async_db)) -> dict:
    coach = await get_approved_coach_or_404(db, coach_id)
    data = await coach_brief(db, coach)
    data["bio"] = coach.bio
    data["trainingExp"] = coach.training_exp
    data["services"] = [
        {
            "id": s.id,
            "name": s.name,
            "serviceType": s.service_type,
            "durationMin": s.duration_min,
            "priceInCents": s.price_in_cents,
            "description": s.description,
        }
        for s in await db.scalars(
            select(Service).where(Service.coach_id == coach.id, Service.is_enabled.is_(True))
        )
    ]
    return ok(data, trace_id=request.state.trace_id)


@router.get("/{coach_id}/slots")
async def coach_slots(coach_id: int, request: Request, db: AsyncSession = Depends(get_async_db)) -> dict:
    await get_approved_coach_or_404(db, coach_id)
    start = date.today()
    end = start + timedelta(days=14)
    slots = list(
        await db.scalars(
            select(CoachSlot)
            .where(
                CoachSlot.coach_id == coach_id,
                CoachSlot.status == "AVAILABLE",
                CoachSlot.date >= start,
                CoachSlot.date <= end,
            )
            .order_by(CoachSlot.date, CoachSlot.start_time)
        )
    )
    items = [
        {
            "id": s.id,
            "coachId": s.coach_id,
            "date": s.date.isoformat(),
            "startTime": s.start_time.strftime("%H:%M"),
            "endTime": s.end_time.strftime("%H:%M"),
            "status": s.status,
        }
        for s in slots
    ]
    return ok({"items": items}, trace_id=request.state.trace_id)


@router.get("/{coach_id}/reviews")
async def coach_reviews_endpoint(
    coach_id: int,
    request: Request,
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=10, ge=1, le=50, alias="pageSize"),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    await get_approved_coach_or_404(db, coach_id)
    rows, total = await coach_reviews(db, coach_id, page, pageSize)
    items = [ReviewOut(**review_to_out(review, user.nickname)).model_dump(by_alias=True) for review, user in rows]
    return ok(paginated(items, total, page, pageSize), trace_id=request.state.trace_id)
