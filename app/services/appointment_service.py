"""预约业务逻辑（用户端下单 + 教练端处理）。"""

import uuid
from datetime import date as date_type
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.models.coach import Appointment, CoachProfile, CoachSlot, Service
from app.models.user import User
from app.models.v1_1 import ClientRelation
from app.models.v1_1 import Review
from app.services.notification_service import notify
from app.schemas.appointment import AppointmentCreateIn
from app.utils.time import to_iso, utcnow_naive


def generate_appointment_no() -> str:
    return "AP" + datetime.now().strftime("%Y%m%d%H%M%S") + uuid.uuid4().hex[:6].upper()


def create_appointment(
    db: Session,
    user: User,
    data: AppointmentCreateIn,
    idempotency_key: str | None,
) -> tuple[Appointment, bool]:
    coach = db.scalar(
        select(CoachProfile).where(
            CoachProfile.id == data.coach_id,
            CoachProfile.audit_status == "APPROVED",
            CoachProfile.deleted_at.is_(None),
        )
    )
    if coach is None:
        raise AppError(404, "COACH_NOT_FOUND", "教练不存在或暂未上架")
    if coach.user_id == user.id:
        raise AppError(409, "COACH_SELF_BOOKING", "不能预约自己")

    service = db.scalar(
        select(Service).where(
            Service.id == data.service_id,
            Service.coach_id == coach.id,
            Service.is_enabled.is_(True),
        )
    )
    if service is None:
        raise AppError(400, "SERVICE_INVALID", "服务项目不存在或已下架")

    slot = db.get(CoachSlot, data.slot_id)
    if slot is None or slot.coach_id != coach.id:
        raise AppError(409, "SLOT_UNAVAILABLE", "所选时段不可用")
    if slot.date < date_type.today():
        raise AppError(409, "SLOT_UNAVAILABLE", "所选时段已过期")

    result = db.execute(
        update(CoachSlot)
        .where(CoachSlot.id == slot.id, CoachSlot.status == "AVAILABLE")
        .values(status="BOOKED")
    )
    if result.rowcount == 0:
        raise AppError(409, "SLOT_UNAVAILABLE", "所选时段已被预约")

    appointment = Appointment(
        appointment_no=generate_appointment_no(),
        user_id=user.id,
        coach_id=coach.id,
        service_id=service.id,
        slot_id=slot.id,
        need_desc=data.need_desc.strip(),
        status="PENDING",
        idempotency_key=idempotency_key,
    )
    db.add(appointment)
    notify(
        db,
        coach.user_id,
        "APPOINTMENT",
        "收到新预约",
        f"{user.nickname}提交了新预约，请尽快确认。",
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if idempotency_key:
            existing = db.scalar(
                select(Appointment).where(
                    Appointment.user_id == user.id,
                    Appointment.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                return existing, False
        raise AppError(409, "SLOT_UNAVAILABLE", "所选时段已被预约")
    db.refresh(appointment)
    return appointment, True


def get_appointment_ctx(db: Session, appointment: Appointment) -> dict:
    coach_user = db.get(User, db.get(CoachProfile, appointment.coach_id).user_id)
    service = db.get(Service, appointment.service_id)
    slot = db.get(CoachSlot, appointment.slot_id)
    reviewed = db.scalar(select(Review.id).where(Review.appointment_id == appointment.id)) is not None
    return {
        "id": appointment.id,
        "appointment_no": appointment.appointment_no,
        "coach": {
            "id": appointment.coach_id,
            "nickname": coach_user.nickname if coach_user else "",
            "avatar_url": coach_user.avatar_url if coach_user else None,
        },
        "service": {
            "id": service.id,
            "name": service.name,
            "service_type": service.service_type,
            "price_in_cents": service.price_in_cents,
        },
        "slot": {
            "id": slot.id,
            "date": slot.date.isoformat(),
            "start_time": slot.start_time.strftime("%H:%M"),
            "end_time": slot.end_time.strftime("%H:%M"),
        },
        "need_desc": appointment.need_desc,
        "status": appointment.status,
        "cancel_reason": appointment.cancel_reason,
        "can_cancel": appointment.status in ("PENDING", "CONFIRMED"),
        "reviewed": reviewed,
        "created_at": to_iso(appointment.created_at),
    }


def list_my_appointments(
    db: Session, user_id: int, status: str | None, page: int, page_size: int
) -> tuple[list[Appointment], int]:
    stmt = select(Appointment).where(Appointment.user_id == user_id)
    if status:
        stmt = stmt.where(Appointment.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        db.scalars(
            stmt.order_by(Appointment.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return rows, total


def cancel_my_appointment(db: Session, user: User, appointment_id: int) -> Appointment:
    appointment = db.scalar(
        select(Appointment).where(Appointment.id == appointment_id, Appointment.user_id == user.id)
    )
    if appointment is None:
        raise AppError(404, "APPOINTMENT_NOT_FOUND", "预约记录不存在")
    if appointment.status not in ("PENDING", "CONFIRMED"):
        raise AppError(409, "INVALID_STATE_TRANSITION", "当前状态不允许取消")
    appointment.status = "CANCELLED"
    appointment.cancel_by = user.id
    release_slot(db, appointment.slot_id)
    db.commit()
    db.refresh(appointment)
    return appointment


def release_slot(db: Session, slot_id: int) -> None:
    slot = db.get(CoachSlot, slot_id)
    if slot is not None and slot.status == "BOOKED":
        slot.status = "AVAILABLE"


# ---------- 教练端 ----------


def get_coach_appointment_or_404(db: Session, coach_profile_id: int, appointment_id: int) -> Appointment:
    appointment = db.scalar(
        select(Appointment).where(
            Appointment.id == appointment_id,
            Appointment.coach_id == coach_profile_id,
        )
    )
    if appointment is None:
        raise AppError(404, "APPOINTMENT_NOT_FOUND", "预约记录不存在")
    return appointment


def list_coach_appointments(
    db: Session,
    coach_profile_id: int,
    status: str | None,
    slot_date: str | None,
    page: int,
    page_size: int,
) -> tuple[list[Appointment], int]:
    stmt = select(Appointment).where(Appointment.coach_id == coach_profile_id)
    if status:
        stmt = stmt.where(Appointment.status == status)
    if slot_date:
        try:
            parsed = date_type.fromisoformat(slot_date)
        except ValueError:
            raise AppError(400, "VALIDATION_ERROR", "日期格式应为 YYYY-MM-DD")
        stmt = (
            stmt.join(CoachSlot, CoachSlot.id == Appointment.slot_id)
            .where(CoachSlot.date == parsed)
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        db.scalars(
            stmt.order_by(Appointment.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return rows, total


def coach_appointment_to_out(db: Session, appointment: Appointment) -> dict:
    user = db.get(User, appointment.user_id)
    service = db.get(Service, appointment.service_id)
    slot = db.get(CoachSlot, appointment.slot_id)
    from app.services.auth_service import mask_phone

    return {
        "id": appointment.id,
        "appointment_no": appointment.appointment_no,
        "user": {
            "id": user.id,
            "nickname": user.nickname,
            "phone": mask_phone(user.phone),
        },
        "service": {
            "id": service.id,
            "name": service.name,
            "service_type": service.service_type,
            "price_in_cents": service.price_in_cents,
        },
        "slot": {
            "id": slot.id,
            "date": slot.date.isoformat(),
            "start_time": slot.start_time.strftime("%H:%M"),
            "end_time": slot.end_time.strftime("%H:%M"),
        },
        "need_desc": appointment.need_desc,
        "status": appointment.status,
        "cancel_reason": appointment.cancel_reason,
        "created_at": to_iso(appointment.created_at),
        "completed_at": to_iso(appointment.completed_at),
    }


def confirm_appointment(db: Session, coach_profile_id: int, appointment_id: int) -> Appointment:
    appointment = get_coach_appointment_or_404(db, coach_profile_id, appointment_id)
    if appointment.status != "PENDING":
        raise AppError(409, "INVALID_STATE_TRANSITION", "仅待确认预约可确认")
    appointment.status = "CONFIRMED"
    coach_profile = db.get(CoachProfile, coach_profile_id)
    coach_user = db.get(User, coach_profile.user_id) if coach_profile else None
    notify(
        db,
        appointment.user_id,
        "APPOINTMENT",
        "预约已确认",
        f"{coach_user.nickname if coach_user else '教练'}已确认你的预约，请按约定时间联系。",
    )
    db.commit()
    db.refresh(appointment)
    return appointment


def cancel_coach_appointment(
    db: Session, coach_profile_id: int, appointment_id: int, reason: str, coach_user_id: int
) -> Appointment:
    appointment = get_coach_appointment_or_404(db, coach_profile_id, appointment_id)
    if appointment.status not in ("PENDING", "CONFIRMED"):
        raise AppError(409, "INVALID_STATE_TRANSITION", "当前状态不允许取消")
    appointment.status = "CANCELLED"
    appointment.cancel_reason = reason
    appointment.cancel_by = coach_user_id
    notify(
        db,
        appointment.user_id,
        "APPOINTMENT",
        "预约已取消",
        f"你的预约已取消：{reason}",
    )
    release_slot(db, appointment.slot_id)
    db.commit()
    db.refresh(appointment)
    return appointment


def complete_appointment(db: Session, coach_profile_id: int, appointment_id: int) -> Appointment:
    appointment = get_coach_appointment_or_404(db, coach_profile_id, appointment_id)
    if appointment.status != "CONFIRMED":
        raise AppError(409, "INVALID_STATE_TRANSITION", "仅已确认预约可标记完成")
    appointment.status = "COMPLETED"
    appointment.completed_at = utcnow_naive()
    relation = db.scalar(
        select(ClientRelation).where(
            ClientRelation.coach_id == coach_profile_id,
            ClientRelation.user_id == appointment.user_id,
        )
    )
    if relation is not None:
        relation.last_appointment_at = appointment.completed_at
    else:
        db.add(ClientRelation(
            coach_id=coach_profile_id,
            user_id=appointment.user_id,
            last_appointment_at=appointment.completed_at,
        ))
    db.commit()
    db.refresh(appointment)
    return appointment
