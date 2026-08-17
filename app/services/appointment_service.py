"""预约业务逻辑（用户端下单 + 教练端处理）。"""

import uuid
import os
from datetime import date as date_type
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.models.coach import Appointment, AppointmentEvent, CoachProfile, CoachSlot, Service
from app.models.user import User
from app.models.v1_1 import ClientRelation
from app.models.v1_1 import Review
from app.services.notification_service import notify
from app.utils.format import mask_phone
from app.schemas.appointment import AppointmentCreateIn
from app.utils.time import to_iso, utcnow_naive


def generate_appointment_no() -> str:
    return "AP" + datetime.now().strftime("%Y%m%d%H%M%S") + uuid.uuid4().hex[:6].upper()


# ============================================================
#  履约规则（可经环境变量覆盖）
# ============================================================
FREE_CANCEL_HOURS = int(os.environ.get("APPT_FREE_CANCEL_HOURS", "24"))
NEAR_CANCEL_HOURS = int(os.environ.get("APPT_NEAR_CANCEL_HOURS", "2"))
NO_SHOW_GRACE_MIN = int(os.environ.get("APPT_NO_SHOW_GRACE_MIN", "15"))


def _slot_start(slot: CoachSlot) -> datetime:
    """预约开始时间（槽位为本地业务时间，与现有过期判断保持一致）。"""
    return datetime.combine(slot.date, slot.start_time)


def _cancel_deadline(slot: CoachSlot) -> datetime:
    return _slot_start(slot) - timedelta(hours=FREE_CANCEL_HOURS)


def _near_cancel_deadline(slot: CoachSlot) -> datetime:
    return _slot_start(slot) - timedelta(hours=NEAR_CANCEL_HOURS)


def _no_show_threshold(slot: CoachSlot) -> datetime:
    return _slot_start(slot) + timedelta(minutes=NO_SHOW_GRACE_MIN)


def _cancel_window(slot: CoachSlot | None, now: datetime | None = None) -> str:
    """返回取消窗口：free（免费）/ near（临近）/ closed（已关闭）。"""
    if slot is None:
        return "closed"
    now = now or datetime.now()
    if now <= _cancel_deadline(slot):
        return "free"
    if now <= _near_cancel_deadline(slot):
        return "near"
    return "closed"


def _is_overdue_no_show(slot: CoachSlot | None) -> bool:
    return slot is not None and datetime.now() > _no_show_threshold(slot)


def _effective_status(appointment: Appointment, slot: CoachSlot | None) -> str:
    """读取时计算有效状态：逾期未赴约且仍待开始 → 视为 NO_SHOW（不落库）。"""
    if appointment.status in ("PENDING", "CONFIRMED"):
        if slot is not None and _is_overdue_no_show(slot):
            return "NO_SHOW"
    return appointment.status


async def _record_event(
    db: AsyncSession,
    appointment_id: int,
    actor_id: int | None,
    actor_role: str,
    event: str,
    note: str | None = None,
) -> None:
    db.add(AppointmentEvent(
        appointment_id=appointment_id,
        actor_id=actor_id,
        actor_role=actor_role,
        event=event,
        note=note,
    ))


async def _mark_no_show(
    db: AsyncSession,
    appointment: Appointment,
    actor_id: int | None,
    actor_role: str,
    note: str = "逾期未进入服务房间",
) -> None:
    appointment.status = "NO_SHOW"
    appointment.no_show_at = utcnow_naive()
    await _record_event(db, appointment.id, actor_id, actor_role, "NO_SHOW_USER", note)
    await release_slot(db, appointment.slot_id)
    await db.commit()
    await db.refresh(appointment)


async def create_appointment(
    db: AsyncSession,
    user: User,
    data: AppointmentCreateIn,
    idempotency_key: str | None,
) -> tuple[Appointment, bool]:
    coach = await db.scalar(
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

    service = await db.scalar(
        select(Service).where(
            Service.id == data.service_id,
            Service.coach_id == coach.id,
            Service.is_enabled.is_(True),
        )
    )
    if service is None:
        raise AppError(400, "SERVICE_INVALID", "服务项目不存在或已下架")

    slot = await db.get(CoachSlot, data.slot_id)
    if slot is None or slot.coach_id != coach.id:
        raise AppError(409, "SLOT_UNAVAILABLE", "所选时段不可用")
    if slot.date < date_type.today():
        raise AppError(409, "SLOT_UNAVAILABLE", "所选时段已过期")

    result = await db.execute(
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
        cancel_deadline_at=_cancel_deadline(slot),
        idempotency_key=idempotency_key,
    )
    db.add(appointment)
    await db.flush()
    await _record_event(db, appointment.id, user.id, "USER", "BOOKED", "创建预约")
    await notify(
        db,
        coach.user_id,
        "APPOINTMENT",
        "收到新预约",
        f"{user.nickname}提交了新预约，请尽快确认。",
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if idempotency_key:
            existing = await db.scalar(
                select(Appointment).where(
                    Appointment.user_id == user.id,
                    Appointment.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                return existing, False
        raise AppError(409, "SLOT_UNAVAILABLE", "所选时段已被预约")
    await db.refresh(appointment)
    return appointment, True


async def get_appointment_ctx(db: AsyncSession, appointment: Appointment) -> dict:
    coach_user = await db.get(User, (await db.get(CoachProfile, appointment.coach_id)).user_id)
    service = await db.get(Service, appointment.service_id)
    slot = await db.get(CoachSlot, appointment.slot_id)
    reviewed = await db.scalar(select(Review.id).where(Review.appointment_id == appointment.id)) is not None
    effective = _effective_status(appointment, slot)
    window = _cancel_window(slot)
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
        "status": effective,
        "cancel_reason": appointment.cancel_reason,
        "cancel_deadline_at": to_iso(appointment.cancel_deadline_at),
        "no_show_at": to_iso(appointment.no_show_at),
        "cancel_window": window,
        "can_cancel": effective in ("PENDING", "CONFIRMED") and window != "closed",
        "reviewed": reviewed,
        "created_at": to_iso(appointment.created_at),
    }


async def my_appointments_to_out(db: AsyncSession, appointments: list[Appointment]) -> list[dict]:
    """批量序列化我的预约（避免列表接口 N+1）。"""
    if not appointments:
        return []
    coach_ids = [a.coach_id for a in appointments]
    service_ids = [a.service_id for a in appointments]
    slot_ids = [a.slot_id for a in appointments]
    appointment_ids = [a.id for a in appointments]

    coaches = {c.id: c for c in await db.scalars(select(CoachProfile).where(CoachProfile.id.in_(coach_ids)))}
    coach_user_ids = [c.user_id for c in coaches.values()]
    users = {u.id: u for u in await db.scalars(select(User).where(User.id.in_(coach_user_ids)))}
    services = {s.id: s for s in await db.scalars(select(Service).where(Service.id.in_(service_ids)))}
    slots = {s.id: s for s in await db.scalars(select(CoachSlot).where(CoachSlot.id.in_(slot_ids)))}
    reviewed_ids = set(await db.scalars(select(Review.appointment_id).where(Review.appointment_id.in_(appointment_ids))))

    items: list[dict] = []
    for a in appointments:
        coach = coaches.get(a.coach_id)
        coach_user = users.get(coach.user_id) if coach else None
        service = services.get(a.service_id)
        slot = slots.get(a.slot_id)
        effective = _effective_status(a, slot)
        window = _cancel_window(slot)
        items.append({
            "id": a.id,
            "appointment_no": a.appointment_no,
            "coach": {
                "id": a.coach_id,
                "nickname": coach_user.nickname if coach_user else "",
                "avatar_url": coach_user.avatar_url if coach_user else None,
            },
            "service": {
                "id": service.id,
                "name": service.name,
                "service_type": service.service_type,
                "price_in_cents": service.price_in_cents,
            } if service else None,
            "slot": {
                "id": slot.id,
                "date": slot.date.isoformat(),
                "start_time": slot.start_time.strftime("%H:%M"),
                "end_time": slot.end_time.strftime("%H:%M"),
            } if slot else None,
            "need_desc": a.need_desc,
            "status": effective,
            "cancel_reason": a.cancel_reason,
            "cancel_deadline_at": to_iso(a.cancel_deadline_at),
            "no_show_at": to_iso(a.no_show_at),
            "cancel_window": window,
            "can_cancel": effective in ("PENDING", "CONFIRMED") and window != "closed",
            "reviewed": a.id in reviewed_ids,
            "created_at": to_iso(a.created_at),
        })
    return items


async def list_my_appointments(
    db: AsyncSession, user_id: int, status: str | None, page: int, page_size: int
) -> tuple[list[Appointment], int]:
    stmt = select(Appointment).where(Appointment.user_id == user_id)
    if status:
        stmt = stmt.where(Appointment.status == status)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        await db.scalars(
            stmt.order_by(Appointment.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return rows, total


async def cancel_my_appointment(
    db: AsyncSession, user: User, appointment_id: int, reason: str | None = None
) -> Appointment:
    appointment = await db.scalar(
        select(Appointment).where(Appointment.id == appointment_id, Appointment.user_id == user.id)
    )
    if appointment is None:
        raise AppError(404, "APPOINTMENT_NOT_FOUND", "预约记录不存在")
    if appointment.status not in ("PENDING", "CONFIRMED"):
        raise AppError(409, "INVALID_STATE_TRANSITION", "当前状态不允许取消")
    slot = await db.get(CoachSlot, appointment.slot_id)

    # 逾期未赴约：先落 NO_SHOW，再拒绝“取消”
    if _is_overdue_no_show(slot):
        await _mark_no_show(db, appointment, user.id, "SYSTEM", "用户逾期未进入服务房间")
        raise AppError(409, "APPOINTMENT_NO_SHOW", "已逾期未赴约，系统已记录未赴约，无法取消")

    window = _cancel_window(slot)
    if window == "closed":
        raise AppError(
            409,
            "APPOINTMENT_CANCEL_CLOSED",
            f"已进入临近时段（距开始不足 {NEAR_CANCEL_HOURS} 小时），请联系教练或客服处理",
        )

    appointment.status = "CANCELLED"
    appointment.cancel_by = user.id
    appointment.cancel_reason = reason or ("临近时段取消" if window == "near" else "用户取消")
    await _record_event(
        db,
        appointment.id,
        user.id,
        "USER",
        "CANCEL_USER",
        f"取消窗口={window}",
    )
    if slot is not None and slot.status == "BOOKED":
        slot.status = "AVAILABLE"
    coach_profile = await db.get(CoachProfile, appointment.coach_id)
    coach_user = await db.get(User, coach_profile.user_id) if coach_profile else None
    await notify(
        db,
        coach_user.id if coach_user else 0,
        "APPOINTMENT",
        "预约已取消",
        f"用户取消了预约：{appointment.cancel_reason}",
    )
    await db.commit()
    await db.refresh(appointment)
    return appointment


async def release_slot(db: AsyncSession, slot_id: int) -> None:
    slot = await db.get(CoachSlot, slot_id)
    if slot is not None and slot.status == "BOOKED":
        slot.status = "AVAILABLE"


# ---------- 教练端 ----------


async def get_coach_appointment_or_404(db: AsyncSession, coach_profile_id: int, appointment_id: int) -> Appointment:
    appointment = await db.scalar(
        select(Appointment).where(
            Appointment.id == appointment_id,
            Appointment.coach_id == coach_profile_id,
        )
    )
    if appointment is None:
        raise AppError(404, "APPOINTMENT_NOT_FOUND", "预约记录不存在")
    return appointment


async def list_coach_appointments(
    db: AsyncSession,
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
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        await db.scalars(
            stmt.order_by(Appointment.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return rows, total


async def coach_appointment_to_out(db: AsyncSession, appointment: Appointment) -> dict:
    user = await db.get(User, appointment.user_id)
    service = await db.get(Service, appointment.service_id)
    slot = await db.get(CoachSlot, appointment.slot_id)
    effective = _effective_status(appointment, slot)
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
        "status": effective,
        "cancel_reason": appointment.cancel_reason,
        "cancel_deadline_at": to_iso(appointment.cancel_deadline_at),
        "no_show_at": to_iso(appointment.no_show_at),
        "cancel_window": _cancel_window(slot),
        "created_at": to_iso(appointment.created_at),
        "completed_at": to_iso(appointment.completed_at),
    }


async def coach_appointments_to_out(db: AsyncSession, appointments: list[Appointment]) -> list[dict]:
    """批量序列化教练端预约列表（避免列表接口 N+1）。"""
    if not appointments:
        return []
    user_ids = [a.user_id for a in appointments]
    service_ids = [a.service_id for a in appointments]
    slot_ids = [a.slot_id for a in appointments]

    users = {u.id: u for u in await db.scalars(select(User).where(User.id.in_(user_ids)))}
    services = {s.id: s for s in await db.scalars(select(Service).where(Service.id.in_(service_ids)))}
    slots = {s.id: s for s in await db.scalars(select(CoachSlot).where(CoachSlot.id.in_(slot_ids)))}

    items: list[dict] = []
    for a in appointments:
        user = users.get(a.user_id)
        service = services.get(a.service_id)
        slot = slots.get(a.slot_id)
        effective = _effective_status(a, slot)
        items.append({
            "id": a.id,
            "appointment_no": a.appointment_no,
            "user": {
                "id": user.id if user else 0,
                "nickname": user.nickname if user else "",
                "phone": mask_phone(user.phone) if user else "",
            },
            "service": {
                "id": service.id,
                "name": service.name,
                "service_type": service.service_type,
                "price_in_cents": service.price_in_cents,
            } if service else None,
            "slot": {
                "id": slot.id,
                "date": slot.date.isoformat(),
                "start_time": slot.start_time.strftime("%H:%M"),
                "end_time": slot.end_time.strftime("%H:%M"),
            } if slot else None,
            "need_desc": a.need_desc,
            "status": effective,
            "cancel_reason": a.cancel_reason,
            "cancel_deadline_at": to_iso(a.cancel_deadline_at),
            "no_show_at": to_iso(a.no_show_at),
            "cancel_window": _cancel_window(slot),
            "created_at": to_iso(a.created_at),
            "completed_at": to_iso(a.completed_at),
        })
    return items


async def confirm_appointment(db: AsyncSession, coach_profile_id: int, appointment_id: int) -> Appointment:
    appointment = await get_coach_appointment_or_404(db, coach_profile_id, appointment_id)
    if appointment.status != "PENDING":
        raise AppError(409, "INVALID_STATE_TRANSITION", "仅待确认预约可确认")
    appointment.status = "CONFIRMED"
    coach_profile = await db.get(CoachProfile, coach_profile_id)
    coach_user = await db.get(User, coach_profile.user_id) if coach_profile else None
    await notify(
        db,
        appointment.user_id,
        "APPOINTMENT",
        "预约已确认",
        f"{coach_user.nickname if coach_user else '教练'}已确认你的预约，请按约定时间联系。",
    )
    await db.commit()
    await db.refresh(appointment)
    return appointment


async def cancel_coach_appointment(
    db: AsyncSession, coach_profile_id: int, appointment_id: int, reason: str, coach_user_id: int
) -> Appointment:
    appointment = await get_coach_appointment_or_404(db, coach_profile_id, appointment_id)
    if appointment.status not in ("PENDING", "CONFIRMED"):
        raise AppError(409, "INVALID_STATE_TRANSITION", "当前状态不允许取消")
    appointment.status = "CANCELLED"
    appointment.cancel_reason = reason
    appointment.cancel_by = coach_user_id
    await _record_event(
        db,
        appointment.id,
        coach_user_id,
        "COACH",
        "CANCEL_COACH",
        f"教练取消：{reason}",
    )
    await notify(
        db,
        appointment.user_id,
        "APPOINTMENT",
        "预约已取消",
        f"你的预约已取消：{reason}",
    )
    await release_slot(db, appointment.slot_id)
    await db.commit()
    await db.refresh(appointment)
    return appointment


async def reschedule_appointment(
    db: AsyncSession,
    user: User,
    appointment_id: int,
    new_slot_id: int,
    service_id: int | None,
) -> tuple[Appointment, Appointment]:
    """用户改期：原单标记 RESCHEDULED + 释放旧时段，并创建新预约单。"""
    appointment = await db.scalar(
        select(Appointment).where(Appointment.id == appointment_id, Appointment.user_id == user.id)
    )
    if appointment is None:
        raise AppError(404, "APPOINTMENT_NOT_FOUND", "预约记录不存在")
    if appointment.status not in ("PENDING", "CONFIRMED"):
        raise AppError(409, "INVALID_STATE_TRANSITION", "当前状态不允许改期")
    old_slot = await db.get(CoachSlot, appointment.slot_id)
    if _is_overdue_no_show(old_slot):
        await _mark_no_show(db, appointment, user.id, "SYSTEM", "用户逾期未进入服务房间")
        raise AppError(409, "APPOINTMENT_NO_SHOW", "已逾期未赴约，无法改期")
    if _cancel_window(old_slot) == "closed":
        raise AppError(409, "APPOINTMENT_CANCEL_CLOSED", "已进入临近时段，请联系教练或客服处理")

    coach = await db.get(CoachProfile, appointment.coach_id)
    service = await db.get(Service, service_id or appointment.service_id)
    if service is None or service.coach_id != coach.id or not service.is_enabled:
        raise AppError(400, "SERVICE_INVALID", "服务项目不存在或已下架")
    new_slot = await db.get(CoachSlot, new_slot_id)
    if new_slot is None or new_slot.coach_id != coach.id:
        raise AppError(409, "SLOT_UNAVAILABLE", "所选时段不可用")
    if new_slot.date < date_type.today():
        raise AppError(409, "SLOT_UNAVAILABLE", "所选时段已过期")
    result = await db.execute(
        update(CoachSlot)
        .where(CoachSlot.id == new_slot.id, CoachSlot.status == "AVAILABLE")
        .values(status="BOOKED")
    )
    if result.rowcount == 0:
        raise AppError(409, "SLOT_UNAVAILABLE", "所选时段已被预约")

    appointment.status = "RESCHEDULED"
    appointment.cancel_by = user.id
    appointment.cancel_reason = "改期"
    await _record_event(db, appointment.id, user.id, "USER", "RESCHEDULE", f"改期至新时段 {new_slot.id}")
    if old_slot is not None and old_slot.status == "BOOKED":
        old_slot.status = "AVAILABLE"

    new_appointment = Appointment(
        appointment_no=generate_appointment_no(),
        user_id=user.id,
        coach_id=coach.id,
        service_id=service.id,
        slot_id=new_slot.id,
        need_desc=appointment.need_desc,
        status="PENDING",
        cancel_deadline_at=_cancel_deadline(new_slot),
    )
    db.add(new_appointment)
    await db.flush()
    await _record_event(db, new_appointment.id, user.id, "USER", "BOOKED", "改期后重新预约")
    coach_user = await db.get(User, coach.user_id)
    await notify(
        db,
        coach_user.id if coach_user else 0,
        "APPOINTMENT",
        "预约已改期",
        f"{user.nickname}将预约改期到 {new_slot.date} {new_slot.start_time.strftime('%H:%M')}，请尽快确认。",
    )
    await db.commit()
    await db.refresh(appointment)
    await db.refresh(new_appointment)
    return appointment, new_appointment


async def mark_no_show_appointment(
    db: AsyncSession,
    actor: User,
    appointment_id: int,
    coach_profile_id: int | None = None,
) -> Appointment:
    """标记用户未赴约（教练端或管理员触发，落库 + 释放时段 + 通知用户）。"""
    if actor.role == "ADMIN":
        appointment = await db.get(Appointment, appointment_id)
    elif coach_profile_id is not None:
        appointment = await get_coach_appointment_or_404(db, coach_profile_id, appointment_id)
    else:
        raise AppError(403, "FORBIDDEN", "无权操作")
    if appointment is None:
        raise AppError(404, "APPOINTMENT_NOT_FOUND", "预约记录不存在")
    if appointment.status not in ("PENDING", "CONFIRMED"):
        raise AppError(409, "INVALID_STATE_TRANSITION", "当前状态不允许标记未赴约")
    slot = await db.get(CoachSlot, appointment.slot_id)
    if not _is_overdue_no_show(slot):
        raise AppError(409, "APPOINTMENT_NOT_OVERDUE", "尚未到未赴约判定时间")
    await _mark_no_show(db, appointment, actor.id, actor.role, "标记用户未赴约")
    await notify(
        db,
        appointment.user_id,
        "APPOINTMENT",
        "预约未赴约",
        "本次预约已超时未赴约，已记录；如需帮助请联系平台。",
    )
    await db.commit()
    await db.refresh(appointment)
    return appointment


async def complete_appointment(db: AsyncSession, coach_profile_id: int, appointment_id: int) -> Appointment:
    appointment = await get_coach_appointment_or_404(db, coach_profile_id, appointment_id)
    if appointment.status != "CONFIRMED":
        raise AppError(409, "INVALID_STATE_TRANSITION", "仅已确认预约可标记完成")
    appointment.status = "COMPLETED"
    appointment.completed_at = utcnow_naive()
    relation = await db.scalar(
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
    await db.commit()
    await db.refresh(appointment)
    return appointment
