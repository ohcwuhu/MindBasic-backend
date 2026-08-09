"""公开教练目录：列表、详情、可预约时段。"""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.response import ok, paginated
from app.core.exceptions import AppError
from app.models.coach import CoachProfile, CoachSlot, CoachTag, Service, Tag
from app.models.user import User
from app.utils.time import to_iso

router = APIRouter(prefix="/coaches", tags=["coaches"])


def get_approved_coach_or_404(db: Session, coach_id: int) -> CoachProfile:
    coach = db.scalar(
        select(CoachProfile).where(
            CoachProfile.id == coach_id,
            CoachProfile.audit_status == "APPROVED",
            CoachProfile.deleted_at.is_(None),
        )
    )
    if coach is None:
        raise AppError(404, "COACH_NOT_FOUND", "教练不存在或暂未上架")
    return coach


def coach_tags(db: Session, coach_id: int) -> list[str]:
    return list(
        db.scalars(
            select(Tag.name)
            .join(CoachTag, CoachTag.tag_id == Tag.id)
            .where(CoachTag.coach_id == coach_id)
            .order_by(Tag.sort_order)
        )
    )


def coach_brief(db: Session, coach: CoachProfile) -> dict:
    user = db.get(User, coach.user_id)
    return {
        "id": coach.id,
        "nickname": user.nickname if user else "",
        "avatarUrl": user.avatar_url if user else None,
        "tagNames": coach_tags(db, coach.id),
        "yearsOfExperience": coach.years_of_experience,
        "rating": float(coach.rating),
        "reviewCount": coach.review_count,
        "serviceConcept": coach.service_concept,
    }


@router.get("")
def list_coaches(
    request: Request,
    keyword: str | None = Query(default=None, max_length=32),
    tagId: int | None = Query(default=None, alias="tagId"),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=10, ge=1, le=50, alias="pageSize"),
    db: Session = Depends(get_db),
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
    total = len(list(db.scalars(stmt)))
    coaches = list(
        db.scalars(
            stmt.order_by(CoachProfile.rating.desc(), CoachProfile.id.desc())
            .offset((page - 1) * pageSize)
            .limit(pageSize)
        )
    )
    items = [coach_brief(db, c) for c in coaches]
    return ok(paginated(items, total, page, pageSize), trace_id=request.state.trace_id)


@router.get("/{coach_id}")
def coach_detail(coach_id: int, request: Request, db: Session = Depends(get_db)) -> dict:
    coach = get_approved_coach_or_404(db, coach_id)
    data = coach_brief(db, coach)
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
        for s in db.scalars(
            select(Service).where(Service.coach_id == coach.id, Service.is_enabled.is_(True))
        )
    ]
    return ok(data, trace_id=request.state.trace_id)


@router.get("/{coach_id}/slots")
def coach_slots(coach_id: int, request: Request, db: Session = Depends(get_db)) -> dict:
    get_approved_coach_or_404(db, coach_id)
    start = date.today()
    end = start + timedelta(days=14)
    slots = list(
        db.scalars(
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
