"""教练入驻与审核业务逻辑。"""

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.models.coach import CoachAudit, CoachProfile, CoachTag, Service, Tag
from app.models.user import AdminActionLog, User
from app.schemas.coach import CoachProfileIn, CoachProfilePatchIn, ServiceIn
from app.schemas.coach import ServicePatchIn, SlotBatchIn, SlotIn
from app.utils.time import to_iso, utcnow_naive
from datetime import date as date_type
from datetime import datetime


def get_profile_by_user(db: Session, user_id: int) -> CoachProfile | None:
    return db.scalar(select(CoachProfile).where(CoachProfile.user_id == user_id))


def get_profile_or_404(db: Session, user_id: int) -> CoachProfile:
    profile = get_profile_by_user(db, user_id)
    if profile is None:
        raise AppError(404, "NOT_FOUND", "请先提交教练入驻资料")
    return profile


def get_coach_tags(db: Session, coach_id: int) -> list[Tag]:
    return list(
        db.scalars(
            select(Tag)
            .join(CoachTag, CoachTag.tag_id == Tag.id)
            .where(CoachTag.coach_id == coach_id)
            .order_by(Tag.sort_order)
        )
    )


def get_coach_services(db: Session, coach_id: int) -> list[Service]:
    return list(
        db.scalars(
            select(Service)
            .where(Service.coach_id == coach_id)
            .order_by(Service.id.asc())
        )
    )


def validate_tags(db: Session, tag_ids: list[int]) -> list[Tag]:
    if not tag_ids:
        return []
    tags = list(db.scalars(select(Tag).where(Tag.id.in_(tag_ids), Tag.is_enabled.is_(True))))
    if len(tags) != len(set(tag_ids)):
        raise AppError(400, "VALIDATION_ERROR", "包含无效的标签")
    return tags


def create_services(db: Session, coach_id: int, services: list[ServiceIn]) -> None:
    for item in services:
        db.add(Service(
            coach_id=coach_id,
            name=item.name,
            service_type=item.service_type,
            duration_min=item.duration_min,
            price_in_cents=item.price_in_cents,
            description=item.description,
            is_enabled=True,
        ))


def get_own_service_or_404(db: Session, coach_profile_id: int, service_id: int) -> Service:
    service = db.scalar(
        select(Service).where(Service.id == service_id, Service.coach_id == coach_profile_id)
    )
    if service is None:
        raise AppError(404, "SERVICE_INVALID", "服务项目不存在")
    return service


def create_service(db: Session, coach_profile_id: int, data: ServiceIn) -> Service:
    service = Service(
        coach_id=coach_profile_id,
        name=data.name.strip(),
        service_type=data.service_type,
        duration_min=data.duration_min,
        price_in_cents=data.price_in_cents,
        description=data.description,
        is_enabled=True,
    )
    db.add(service)
    db.commit()
    db.refresh(service)
    return service


def update_service(db: Session, coach_profile_id: int, service_id: int, data: ServicePatchIn) -> Service:
    service = get_own_service_or_404(db, coach_profile_id, service_id)
    changes = data.model_dump(exclude_unset=True, exclude_none=True)
    for field in ("name", "service_type", "duration_min", "price_in_cents", "description", "is_enabled"):
        if field in changes:
            setattr(service, field, changes[field])
    db.commit()
    db.refresh(service)
    return service


def parse_slot_time(value: str):
    return datetime.strptime(value, "%H:%M").time()


def list_coach_slots(db: Session, coach_profile_id: int, start_date: str, end_date: str) -> list:
    try:
        start = date_type.fromisoformat(start_date)
        end = date_type.fromisoformat(end_date)
    except ValueError:
        raise AppError(400, "VALIDATION_ERROR", "日期格式应为 YYYY-MM-DD")
    if end < start:
        raise AppError(400, "VALIDATION_ERROR", "结束日期不能早于开始日期")
    from app.models.coach import CoachSlot

    return list(
        db.scalars(
            select(CoachSlot)
            .where(
                CoachSlot.coach_id == coach_profile_id,
                CoachSlot.date >= start,
                CoachSlot.date <= end,
            )
            .order_by(CoachSlot.date, CoachSlot.start_time)
        )
    )


def replace_coach_slots(db: Session, coach_profile_id: int, data: SlotBatchIn) -> list:
    """按日期范围整体替换可管理时段：删除 AVAILABLE/OFF，保留 BOOKED。"""
    from app.models.coach import CoachSlot

    today = date_type.today()
    parsed: list[tuple[date_type, object, object]] = []
    seen: set[tuple[date_type, object]] = set()
    for slot in data.slots:
        d = date_type.fromisoformat(slot.date)
        start = parse_slot_time(slot.start_time)
        end = parse_slot_time(slot.end_time)
        if d < today:
            raise AppError(400, "VALIDATION_ERROR", "不能设置过去的时段")
        if end <= start:
            raise AppError(400, "VALIDATION_ERROR", "结束时间必须晚于开始时间")
        if (d, start) in seen:
            raise AppError(400, "SLOT_CONFLICT", "存在重复时段")
        seen.add((d, start))
        parsed.append((d, start, end))

    dates = {d for d, _, _ in parsed}
    db.execute(
        CoachSlot.__table__.delete().where(
            CoachSlot.coach_id == coach_profile_id,
            CoachSlot.date.in_(dates),
            CoachSlot.status != "BOOKED",
        )
    )
    booked_keys = {
        (row.date, row.start_time)
        for row in db.scalars(
            select(CoachSlot).where(
                CoachSlot.coach_id == coach_profile_id,
                CoachSlot.date.in_(dates),
                CoachSlot.status == "BOOKED",
            )
        )
    }
    for d, start, end in parsed:
        if (d, start) in booked_keys:
            continue
        db.add(CoachSlot(
            coach_id=coach_profile_id,
            date=d,
            start_time=start,
            end_time=end,
            status="AVAILABLE",
        ))
    db.commit()
    return list_coach_slots(
        db, coach_profile_id, min(dates).isoformat(), max(dates).isoformat()
    )


def build_snapshot(profile: CoachProfile, tags: list[Tag], services: list[Service]) -> dict:
    return {
        "realName": profile.real_name,
        "bio": profile.bio,
        "trainingExp": profile.training_exp,
        "serviceConcept": profile.service_concept,
        "yearsOfExperience": profile.years_of_experience,
        "credentialUrls": profile.credential_urls,
        "idCardUrl": profile.id_card_url,
        "tagIds": [t.id for t in tags],
        "services": [
            {
                "name": s.name,
                "serviceType": s.service_type,
                "durationMin": s.duration_min,
                "priceInCents": s.price_in_cents,
                "description": s.description,
            }
            for s in services
        ],
    }


def create_audit_record(db: Session, profile: CoachProfile, version: int) -> CoachAudit:
    tags = get_coach_tags(db, profile.id)
    services = get_coach_services(db, profile.id)
    audit = CoachAudit(
        coach_id=profile.id,
        submit_version=version,
        profile_snapshot=build_snapshot(profile, tags, services),
        status="PENDING",
        remark=None,
    )
    db.add(audit)
    return audit


def create_profile(db: Session, user: User, data: CoachProfileIn) -> CoachProfile:
    if get_profile_by_user(db, user.id) is not None:
        raise AppError(409, "CONFLICT", "已存在教练资料，请勿重复提交")
    tags = validate_tags(db, data.tag_ids)
    profile = CoachProfile(
        user_id=user.id,
        real_name=data.real_name,
        bio=data.bio,
        training_exp=data.training_exp,
        service_concept=data.service_concept,
        years_of_experience=data.years_of_experience,
        credential_urls=data.credential_urls,
        id_card_url=data.id_card_url,
        audit_status="PENDING",
        rating=0.0,
        review_count=0,
    )
    db.add(profile)
    db.flush()  # 取得 profile.id
    db.add_all(CoachTag(coach_id=profile.id, tag_id=t.id) for t in tags)
    create_services(db, profile.id, data.services)
    create_audit_record(db, profile, version=1)
    db.commit()
    db.refresh(profile)
    return profile


def update_profile(db: Session, user: User, data: CoachProfilePatchIn) -> CoachProfile:
    profile = get_profile_or_404(db, user.id)
    changes = data.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        return profile

    if "real_name" in changes:
        profile.real_name = changes["real_name"]
    if "bio" in changes:
        profile.bio = changes["bio"]
    if "training_exp" in changes:
        profile.training_exp = changes["training_exp"]
    if "service_concept" in changes:
        profile.service_concept = changes["service_concept"]
    if "years_of_experience" in changes:
        profile.years_of_experience = changes["years_of_experience"]
    if "credential_urls" in changes:
        profile.credential_urls = changes["credential_urls"]
    if "id_card_url" in changes:
        profile.id_card_url = changes["id_card_url"]

    if "tag_ids" in changes:
        tags = validate_tags(db, changes["tag_ids"])
        db.execute(CoachTag.__table__.delete().where(CoachTag.coach_id == profile.id))
        db.add_all(CoachTag(coach_id=profile.id, tag_id=t.id) for t in tags)

    # 审核通过后的资料修改需要重新审核
    if profile.audit_status == "APPROVED":
        latest = latest_audit_version(db, profile.id)
        profile.audit_status = "PENDING"
        create_audit_record(db, profile, version=latest + 1)

    db.commit()
    db.refresh(profile)
    return profile


def latest_audit_version(db: Session, coach_id: int) -> int:
    return db.scalar(
        select(func.max(CoachAudit.submit_version)).where(CoachAudit.coach_id == coach_id)
    ) or 0


def submit_audit(db: Session, user: User) -> CoachProfile:
    profile = get_profile_or_404(db, user.id)
    if profile.audit_status == "APPROVED":
        raise AppError(409, "INVALID_STATE_TRANSITION", "已审核通过，无需重新提交")
    if profile.audit_status == "PENDING":
        raise AppError(409, "INVALID_STATE_TRANSITION", "资料审核中，请勿重复提交")
    # REJECTED → 重新提交
    profile.audit_status = "PENDING"
    version = latest_audit_version(db, profile.id) + 1
    create_audit_record(db, profile, version=version)
    db.commit()
    db.refresh(profile)
    return profile


def profile_to_out(db: Session, profile: CoachProfile) -> dict:
    tags = get_coach_tags(db, profile.id)
    services = get_coach_services(db, profile.id)
    latest = db.scalar(
        select(CoachAudit)
        .where(CoachAudit.coach_id == profile.id)
        .order_by(CoachAudit.submit_version.desc())
    )
    return {
        "id": profile.id,
        "user_id": profile.user_id,
        "real_name": profile.real_name,
        "bio": profile.bio,
        "training_exp": profile.training_exp,
        "service_concept": profile.service_concept,
        "years_of_experience": profile.years_of_experience,
        "tags": [{"id": t.id, "name": t.name, "type": t.type} for t in tags],
        "services": [
            {
                "id": s.id,
                "name": s.name,
                "service_type": s.service_type,
                "duration_min": s.duration_min,
                "price_in_cents": s.price_in_cents,
                "description": s.description,
                "is_enabled": bool(s.is_enabled),
            }
            for s in services
        ],
        "audit_status": profile.audit_status,
        "audit_remark": latest.remark if latest else None,
        "rating": float(profile.rating),
        "review_count": profile.review_count,
        "created_at": to_iso(profile.created_at),
    }


def list_audits(db: Session, status: str | None, page: int, page_size: int) -> tuple[list[tuple[CoachAudit, User]], int]:
    stmt = (
        select(CoachAudit, User)
        .join(CoachProfile, CoachProfile.id == CoachAudit.coach_id)
        .join(User, User.id == CoachProfile.user_id)
    )
    if status:
        stmt = stmt.where(CoachAudit.status == status)
    total_stmt = select(func.count()).select_from(CoachAudit)
    if status:
        total_stmt = total_stmt.where(CoachAudit.status == status)
    total = db.scalar(total_stmt) or 0
    rows = db.execute(
        stmt.order_by(CoachAudit.submitted_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return rows, total


def get_audit_or_404(db: Session, audit_id: int) -> tuple[CoachAudit, User]:
    row = db.execute(
        select(CoachAudit, User)
        .join(CoachProfile, CoachProfile.id == CoachAudit.coach_id)
        .join(User, User.id == CoachProfile.user_id)
        .where(CoachAudit.id == audit_id)
    ).first()
    if row is None:
        raise AppError(404, "NOT_FOUND", "审核记录不存在")
    return row[0], row[1]


def approve_audit(db: Session, admin: User, audit_id: int) -> CoachAudit:
    now = utcnow_naive()
    result = db.execute(
        update(CoachAudit)
        .where(CoachAudit.id == audit_id, CoachAudit.status == "PENDING")
        .values(status="APPROVED", remark=None, reviewed_by=admin.id, reviewed_at=now)
    )
    if result.rowcount == 0:
        raise AppError(409, "AUDIT_ALREADY_PROCESSED", "该申请已处理")
    audit = db.get(CoachAudit, audit_id)
    db.execute(
        update(CoachProfile)
        .where(CoachProfile.id == audit.coach_id)
        .values(audit_status="APPROVED")
    )
    db.add(AdminActionLog(
        admin_id=admin.id,
        action="COACH_AUDIT_APPROVE",
        target_type="COACH_AUDIT",
        target_id=audit.id,
        detail={"coachId": audit.coach_id, "submitVersion": audit.submit_version},
    ))
    db.commit()
    db.refresh(audit)
    return audit


def reject_audit(db: Session, admin: User, audit_id: int, reason: str) -> CoachAudit:
    now = utcnow_naive()
    result = db.execute(
        update(CoachAudit)
        .where(CoachAudit.id == audit_id, CoachAudit.status == "PENDING")
        .values(status="REJECTED", remark=reason, reviewed_by=admin.id, reviewed_at=now)
    )
    if result.rowcount == 0:
        raise AppError(409, "AUDIT_ALREADY_PROCESSED", "该申请已处理")
    audit = db.get(CoachAudit, audit_id)
    db.execute(
        update(CoachProfile)
        .where(CoachProfile.id == audit.coach_id)
        .values(audit_status="REJECTED")
    )
    db.add(AdminActionLog(
        admin_id=admin.id,
        action="COACH_AUDIT_REJECT",
        target_type="COACH_AUDIT",
        target_id=audit.id,
        detail={"coachId": audit.coach_id, "submitVersion": audit.submit_version, "reason": reason},
    ))
    db.commit()
    db.refresh(audit)
    return audit
