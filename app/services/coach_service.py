"""教练入驻与审核业务逻辑。"""

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.models.coach import (
    CoachAudit,
    CoachPhrase,
    CoachProfile,
    CoachTag,
    PlatformPhrase,
    Service,
    Tag,
)
from app.models.user import AdminActionLog, User
from app.models.v1_1 import ClientRelation
from app.services.notification_service import notify
from app.schemas.coach import CoachProfileIn, CoachProfilePatchIn, ServiceIn
from app.schemas.coach import ServicePatchIn, SlotBatchIn, SlotIn
from app.utils.time import to_iso, utcnow_naive
from datetime import date as date_type
from datetime import datetime


async def get_profile_by_user(db: AsyncSession, user_id: int) -> CoachProfile | None:
    return await db.scalar(select(CoachProfile).where(CoachProfile.user_id == user_id))


async def get_profile_or_404(db: AsyncSession, user_id: int) -> CoachProfile:
    profile = await get_profile_by_user(db, user_id)
    if profile is None:
        raise AppError(404, "NOT_FOUND", "请先提交教练入驻资料")
    return profile


async def get_coach_tags(db: AsyncSession, coach_id: int) -> list[Tag]:
    return list(
        await db.scalars(
            select(Tag)
            .join(CoachTag, CoachTag.tag_id == Tag.id)
            .where(CoachTag.coach_id == coach_id)
            .order_by(Tag.sort_order)
        )
    )


async def get_coach_services(db: AsyncSession, coach_id: int) -> list[Service]:
    return list(
        await db.scalars(
            select(Service)
            .where(Service.coach_id == coach_id)
            .order_by(Service.id.asc())
        )
    )


def service_to_out(service: Service) -> dict:
    """服务项目统一序列化。"""
    return {
        "id": service.id,
        "name": service.name,
        "service_type": service.service_type,
        "duration_min": service.duration_min,
        "price_in_cents": service.price_in_cents,
        "description": service.description,
        "is_enabled": bool(service.is_enabled),
    }


async def validate_tags(db: AsyncSession, tag_ids: list[int]) -> list[Tag]:
    if not tag_ids:
        return []
    tags = list(await db.scalars(select(Tag).where(Tag.id.in_(tag_ids), Tag.is_enabled.is_(True))))
    if len(tags) != len(set(tag_ids)):
        raise AppError(400, "VALIDATION_ERROR", "包含无效的标签")
    return tags


async def create_services(db: AsyncSession, coach_id: int, services: list[ServiceIn]) -> None:
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


async def get_own_service_or_404(db: AsyncSession, coach_profile_id: int, service_id: int) -> Service:
    service = await db.scalar(
        select(Service).where(Service.id == service_id, Service.coach_id == coach_profile_id)
    )
    if service is None:
        raise AppError(404, "SERVICE_INVALID", "服务项目不存在")
    return service


async def create_service(db: AsyncSession, coach_profile_id: int, data: ServiceIn) -> Service:
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
    await db.commit()
    await db.refresh(service)
    return service


async def update_service(db: AsyncSession, coach_profile_id: int, service_id: int, data: ServicePatchIn) -> Service:
    service = await get_own_service_or_404(db, coach_profile_id, service_id)
    changes = data.model_dump(exclude_unset=True, exclude_none=True)
    for field in ("name", "service_type", "duration_min", "price_in_cents", "description", "is_enabled"):
        if field in changes:
            setattr(service, field, changes[field])
    await db.commit()
    await db.refresh(service)
    return service


def parse_slot_time(value: str):
    return datetime.strptime(value, "%H:%M").time()


async def list_coach_slots(db: AsyncSession, coach_profile_id: int, start_date: str, end_date: str) -> list:
    try:
        start = date_type.fromisoformat(start_date)
        end = date_type.fromisoformat(end_date)
    except ValueError:
        raise AppError(400, "VALIDATION_ERROR", "日期格式应为 YYYY-MM-DD")
    if end < start:
        raise AppError(400, "VALIDATION_ERROR", "结束日期不能早于开始日期")
    from app.models.coach import CoachSlot

    return list(
        await db.scalars(
            select(CoachSlot)
            .where(
                CoachSlot.coach_id == coach_profile_id,
                CoachSlot.date >= start,
                CoachSlot.date <= end,
            )
            .order_by(CoachSlot.date, CoachSlot.start_time)
        )
    )


async def replace_coach_slots(db: AsyncSession, coach_profile_id: int, data: SlotBatchIn) -> list:
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
    await db.execute(
        CoachSlot.__table__.delete().where(
            CoachSlot.coach_id == coach_profile_id,
            CoachSlot.date.in_(dates),
            CoachSlot.status != "BOOKED",
        )
    )
    booked_keys = {
        (row.date, row.start_time)
        for row in await db.scalars(
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
    await db.commit()
    return await list_coach_slots(
        db, coach_profile_id, min(dates).isoformat(), max(dates).isoformat()
    )


# ---------- 客户管理 ----------


async def list_clients(
    db: AsyncSession, coach_profile_id: int, keyword: str | None, page: int, page_size: int
) -> tuple[list[tuple[ClientRelation, User]], int]:
    stmt = (
        select(ClientRelation, User)
        .join(User, User.id == ClientRelation.user_id)
        .where(ClientRelation.coach_id == coach_profile_id)
    )
    if keyword:
        stmt = stmt.where(User.nickname.like(f"%{keyword}%"))
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (await db.execute(
        stmt.order_by(ClientRelation.last_appointment_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )).all()
    return rows, total


async def update_client_remark(
    db: AsyncSession, coach_profile_id: int, relation_id: int, remark: str | None
) -> ClientRelation:
    relation = await db.scalar(
        select(ClientRelation).where(
            ClientRelation.id == relation_id,
            ClientRelation.coach_id == coach_profile_id,
        )
    )
    if relation is None:
        raise AppError(404, "NOT_FOUND", "客户记录不存在")
    relation.remark = remark
    await db.commit()
    await db.refresh(relation)
    return relation


# ---------- 话术库 ----------


async def list_platform_phrases(db: AsyncSession, category: str | None) -> list[PlatformPhrase]:
    stmt = (
        select(PlatformPhrase)
        .where(PlatformPhrase.is_enabled.is_(True))
        .order_by(PlatformPhrase.category, PlatformPhrase.sort_order)
    )
    if category:
        stmt = stmt.where(PlatformPhrase.category == category)
    return list(await db.scalars(stmt))


async def my_phrases(db: AsyncSession, coach_profile_id: int) -> list[CoachPhrase]:
    return list(
        await db.scalars(
            select(CoachPhrase)
            .where(CoachPhrase.coach_id == coach_profile_id)
            .order_by(CoachPhrase.updated_at.desc())
        )
    )


async def get_own_phrase_or_404(db: AsyncSession, coach_profile_id: int, phrase_id: int) -> CoachPhrase:
    phrase = await db.scalar(
        select(CoachPhrase).where(
            CoachPhrase.id == phrase_id,
            CoachPhrase.coach_id == coach_profile_id,
        )
    )
    if phrase is None:
        raise AppError(404, "NOT_FOUND", "话术不存在")
    return phrase


async def create_phrase(db: AsyncSession, coach_profile_id: int, category: str, content: str) -> CoachPhrase:
    phrase = CoachPhrase(
        coach_id=coach_profile_id,
        category=category,
        content=content.strip(),
        source="custom",
    )
    db.add(phrase)
    await db.commit()
    await db.refresh(phrase)
    return phrase


async def update_phrase(
    db: AsyncSession, coach_profile_id: int, phrase_id: int, category: str | None, content: str | None
) -> CoachPhrase:
    phrase = await get_own_phrase_or_404(db, coach_profile_id, phrase_id)
    if category:
        phrase.category = category
    if content:
        phrase.content = content.strip()
    await db.commit()
    await db.refresh(phrase)
    return phrase


async def delete_phrase(db: AsyncSession, coach_profile_id: int, phrase_id: int) -> None:
    phrase = await get_own_phrase_or_404(db, coach_profile_id, phrase_id)
    await db.delete(phrase)
    await db.commit()


async def save_platform_phrase(db: AsyncSession, coach_profile_id: int, phrase_id: int) -> CoachPhrase:
    platform = await db.get(PlatformPhrase, phrase_id)
    if platform is None or not platform.is_enabled:
        raise AppError(404, "NOT_FOUND", "平台话术不存在")
    duplicate = await db.scalar(
        select(CoachPhrase.id).where(
            CoachPhrase.coach_id == coach_profile_id,
            CoachPhrase.content == platform.content,
            CoachPhrase.source == "saved",
        )
    )
    if duplicate is not None:
        raise AppError(409, "CONFLICT", "已收藏该话术")
    phrase = CoachPhrase(
        coach_id=coach_profile_id,
        category=platform.category,
        content=platform.content,
        source="saved",
    )
    db.add(phrase)
    await db.commit()
    await db.refresh(phrase)
    return phrase


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


async def create_audit_record(db: AsyncSession, profile: CoachProfile, version: int) -> CoachAudit:
    tags = await get_coach_tags(db, profile.id)
    services = await get_coach_services(db, profile.id)
    audit = CoachAudit(
        coach_id=profile.id,
        submit_version=version,
        profile_snapshot=build_snapshot(profile, tags, services),
        status="PENDING",
        remark=None,
    )
    db.add(audit)
    return audit


async def create_profile(db: AsyncSession, user: User, data: CoachProfileIn) -> CoachProfile:
    if await get_profile_by_user(db, user.id) is not None:
        raise AppError(409, "CONFLICT", "已存在教练资料，请勿重复提交")
    tags = await validate_tags(db, data.tag_ids)
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
    await db.flush()  # 取得 profile.id
    db.add_all(CoachTag(coach_id=profile.id, tag_id=t.id) for t in tags)
    await create_services(db, profile.id, data.services)
    await create_audit_record(db, profile, version=1)
    await db.commit()
    await db.refresh(profile)
    return profile


async def update_profile(db: AsyncSession, user: User, data: CoachProfilePatchIn) -> CoachProfile:
    profile = await get_profile_or_404(db, user.id)
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
        tags = await validate_tags(db, changes["tag_ids"])
        await db.execute(CoachTag.__table__.delete().where(CoachTag.coach_id == profile.id))
        db.add_all(CoachTag(coach_id=profile.id, tag_id=t.id) for t in tags)

    # 审核通过后的资料修改需要重新审核
    if profile.audit_status == "APPROVED":
        latest = await latest_audit_version(db, profile.id)
        profile.audit_status = "PENDING"
        await create_audit_record(db, profile, version=latest + 1)

    await db.commit()
    await db.refresh(profile)
    return profile


async def latest_audit_version(db: AsyncSession, coach_id: int) -> int:
    return await db.scalar(
        select(func.max(CoachAudit.submit_version)).where(CoachAudit.coach_id == coach_id)
    ) or 0


async def submit_audit(db: AsyncSession, user: User) -> CoachProfile:
    profile = await get_profile_or_404(db, user.id)
    if profile.audit_status == "APPROVED":
        raise AppError(409, "INVALID_STATE_TRANSITION", "已审核通过，无需重新提交")
    if profile.audit_status == "PENDING":
        raise AppError(409, "INVALID_STATE_TRANSITION", "资料审核中，请勿重复提交")
    # REJECTED → 重新提交
    profile.audit_status = "PENDING"
    version = await latest_audit_version(db, profile.id) + 1
    await create_audit_record(db, profile, version=version)
    await db.commit()
    await db.refresh(profile)
    return profile


async def profile_to_out(db: AsyncSession, profile: CoachProfile) -> dict:
    tags = await get_coach_tags(db, profile.id)
    services = await get_coach_services(db, profile.id)
    latest = await db.scalar(
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
        "services": [service_to_out(s) for s in services],
        "credential_urls": profile.credential_urls or [],
        "id_card_url": profile.id_card_url,
        "audit_status": profile.audit_status,
        "audit_remark": latest.remark if latest else None,
        "rating": float(profile.rating),
        "review_count": profile.review_count,
        "created_at": to_iso(profile.created_at),
    }


async def list_audits(db: AsyncSession, status: str | None, page: int, page_size: int) -> tuple[list[tuple[CoachAudit, User]], int]:
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
    total = await db.scalar(total_stmt) or 0
    rows = (await db.execute(
        stmt.order_by(CoachAudit.submitted_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )).all()
    return rows, total


async def get_audit_or_404(db: AsyncSession, audit_id: int) -> tuple[CoachAudit, User]:
    row = (await db.execute(
        select(CoachAudit, User)
        .join(CoachProfile, CoachProfile.id == CoachAudit.coach_id)
        .join(User, User.id == CoachProfile.user_id)
        .where(CoachAudit.id == audit_id)
    )).first()
    if row is None:
        raise AppError(404, "NOT_FOUND", "审核记录不存在")
    return row[0], row[1]


async def approve_audit(db: AsyncSession, admin: User, audit_id: int) -> CoachAudit:
    now = utcnow_naive()
    result = await db.execute(
        update(CoachAudit)
        .where(CoachAudit.id == audit_id, CoachAudit.status == "PENDING")
        .values(status="APPROVED", remark=None, reviewed_by=admin.id, reviewed_at=now)
    )
    if result.rowcount == 0:
        raise AppError(409, "AUDIT_ALREADY_PROCESSED", "该申请已处理")
    audit = await db.get(CoachAudit, audit_id)
    await db.execute(
        update(CoachProfile)
        .where(CoachProfile.id == audit.coach_id)
        .values(audit_status="APPROVED")
    )
    profile = await db.get(CoachProfile, audit.coach_id)
    if profile is not None:
        notify(
            db,
            profile.user_id,
            "AUDIT",
            "入驻审核通过",
            "你的教练入驻申请已通过，现在可以使用教练工作台了。",
        )
    db.add(AdminActionLog(
        admin_id=admin.id,
        action="COACH_AUDIT_APPROVE",
        target_type="COACH_AUDIT",
        target_id=audit.id,
        detail={"coachId": audit.coach_id, "submitVersion": audit.submit_version},
    ))
    await db.commit()
    await db.refresh(audit)
    return audit


async def reject_audit(db: AsyncSession, admin: User, audit_id: int, reason: str) -> CoachAudit:
    now = utcnow_naive()
    result = await db.execute(
        update(CoachAudit)
        .where(CoachAudit.id == audit_id, CoachAudit.status == "PENDING")
        .values(status="REJECTED", remark=reason, reviewed_by=admin.id, reviewed_at=now)
    )
    if result.rowcount == 0:
        raise AppError(409, "AUDIT_ALREADY_PROCESSED", "该申请已处理")
    audit = await db.get(CoachAudit, audit_id)
    await db.execute(
        update(CoachProfile)
        .where(CoachProfile.id == audit.coach_id)
        .values(audit_status="REJECTED")
    )
    profile = await db.get(CoachProfile, audit.coach_id)
    if profile is not None:
        notify(
            db,
            profile.user_id,
            "AUDIT",
            "入驻审核被驳回",
            f"驳回原因：{reason}",
        )
    db.add(AdminActionLog(
        admin_id=admin.id,
        action="COACH_AUDIT_REJECT",
        target_type="COACH_AUDIT",
        target_id=audit.id,
        detail={"coachId": audit.coach_id, "submitVersion": audit.submit_version, "reason": reason},
    ))
    await db.commit()
    await db.refresh(audit)
    return audit
