"""订单服务：预约付费锁定状态机（阶段一：余额 / 模拟支付）。"""

import os
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.models.coach import Appointment, AppointmentEvent, CoachSlot
from app.models.user import User
from app.models.v1_1 import Order, Payment, Refund
from app.services.notification_service import notify
from app.services.wallet_service import credit_wallet, debit_wallet
from app.utils.time import to_iso, utcnow_naive

ORDER_PAY_TIMEOUT_MIN = int(os.environ.get("ORDER_PAY_TIMEOUT_MIN", "15"))
PAYMENT_MODE = os.environ.get("PAYMENT_MODE", "mock").lower()
APPT_REFUND_NEAR_PERCENT = int(os.environ.get("APPT_REFUND_NEAR_PERCENT", "50"))


def generate_order_no(prefix: str = "OR") -> str:
    return prefix + datetime.now().strftime("%Y%m%d%H%M%S") + uuid.uuid4().hex[:6].upper()


def order_pay_expire_at() -> datetime:
    return utcnow_naive() + timedelta(minutes=ORDER_PAY_TIMEOUT_MIN)


async def _load_order_for_update(db: AsyncSession, order_no: str, user_id: int) -> Order:
    order = await db.scalar(
        select(Order).where(Order.order_no == order_no).with_for_update()
    )
    if order is None or order.user_id != user_id:
        raise AppError(404, "ORDER_NOT_FOUND", "订单不存在")
    return order


async def get_order_or_404(db: AsyncSession, order_no: str, user: User) -> Order:
    order = await db.scalar(select(Order).where(Order.order_no == order_no))
    if order is None or order.user_id != user.id:
        raise AppError(404, "ORDER_NOT_FOUND", "订单不存在")
    return order


async def get_appointment_order(db: AsyncSession, appointment: Appointment) -> Order | None:
    if appointment.order_id is None:
        return None
    return await db.get(Order, appointment.order_id)


async def order_to_out(db: AsyncSession, order: Order) -> dict:
    appointment = None
    if order.appointment_id is not None:
        a = await db.get(Appointment, order.appointment_id)
        if a is not None:
            appointment = {"id": a.id, "appointment_no": a.appointment_no}
    return {
        "order_no": order.order_no,
        "type": order.type,
        "status": order.status,
        "amount_in_cents": order.amount_in_cents,
        "pay_expire_at": to_iso(order.expire_at),
        "paid_at": to_iso(order.paid_at),
        "refunded_at": to_iso(order.refunded_at),
        "appointment": appointment,
        "created_at": to_iso(order.created_at),
    }


async def pay_order(
    db: AsyncSession,
    user: User,
    order_no: str,
    method: str,
) -> Order:
    """支付订单：BALANCE 扣余额，MOCK 模拟渠道成功；成功后订单 PAID。"""
    order = await _load_order_for_update(db, order_no, user.id)
    if order.status != "CREATED":
        raise AppError(409, "INVALID_STATE_TRANSITION", "订单状态不允许支付")
    now = utcnow_naive()
    if order.expire_at is not None and now > order.expire_at:
        await close_expired_order(db, order, commit=False)
        await db.commit()
        raise AppError(410, "ORDER_EXPIRED", "订单已超时关闭，请重新预约")
    if PAYMENT_MODE == "disabled":
        raise AppError(400, "PAYMENT_DISABLED", "支付功能暂未开放")
    if method == "BALANCE":
        await debit_wallet(
            db,
            user.id,
            order.amount_in_cents,
            "APPOINTMENT_PAY",
            order_id=order.id,
            note=f"预约支付 {order.order_no}",
        )
    order.status = "PAID"
    order.paid_at = now
    db.add(Payment(
        order_id=order.id,
        pay_channel=method,
        transaction_id=generate_order_no("PAY"),
        status="SUCCESS",
        paid_at=now,
    ))
    if order.appointment_id is not None:
        appointment = await db.get(Appointment, order.appointment_id)
        if appointment is not None and appointment.status == "PENDING":
            await notify(
                db,
                appointment.user_id,
                "APPOINTMENT",
                "预约已支付",
                f"订单 {order.order_no} 支付成功，等待教练确认。",
            )
    await db.commit()
    await db.refresh(order)
    return order


async def close_expired_order(db: AsyncSession, order: Order, commit: bool = True) -> None:
    """超时未支付：订单 CLOSED + 预约取消 + 释放时段。"""
    order.status = "CLOSED"
    if order.appointment_id is not None:
        appointment = await db.get(Appointment, order.appointment_id)
        if appointment is not None and appointment.status == "PENDING":
            appointment.status = "CANCELLED"
            appointment.cancel_reason = "超时未支付，订单已关闭"
            db.add(AppointmentEvent(
                appointment_id=appointment.id,
                actor_id=None,
                actor_role="SYSTEM",
                event="CANCEL_SYSTEM",
                note="超时未支付自动取消",
            ))
            slot = await db.get(CoachSlot, appointment.slot_id)
            if slot is not None and slot.status == "BOOKED":
                slot.status = "AVAILABLE"
            await notify(
                db,
                appointment.user_id,
                "APPOINTMENT",
                "预约已取消",
                "预约超时未支付，已自动取消并释放时段，可重新预约。",
            )
    if commit:
        await db.commit()


async def close_unpaid_order(db: AsyncSession, order: Order) -> Order:
    """用户主动取消未支付订单。"""
    if order.status != "CREATED":
        raise AppError(409, "INVALID_STATE_TRANSITION", "订单状态不允许关闭")
    order.status = "CLOSED"
    return order


async def refund_paid_order(
    db: AsyncSession,
    order: Order,
    amount_in_cents: int,
    reason: str,
) -> Order:
    """已支付订单退款：退款单 + 余额退回 + 支付流水标记 REFUNDED。"""
    if order.status != "PAID":
        raise AppError(409, "INVALID_STATE_TRANSITION", "仅已支付订单可退款")
    if amount_in_cents <= 0 or amount_in_cents > order.amount_in_cents:
        raise AppError(400, "REFUND_AMOUNT_INVALID", "退款金额不合法")
    order.status = "REFUNDED"
    order.refunded_at = utcnow_naive()
    db.add(Refund(
        order_id=order.id,
        appointment_id=order.appointment_id,
        amount_in_cents=amount_in_cents,
        reason=reason,
    ))
    await credit_wallet(
        db,
        order.user_id,
        amount_in_cents,
        "REFUND",
        order_id=order.id,
        note=f"预约退款 {order.order_no}",
    )
    payment = await db.scalar(
        select(Payment)
        .where(Payment.order_id == order.id, Payment.status == "SUCCESS")
        .order_by(Payment.id.desc())
    )
    if payment is not None:
        payment.status = "REFUNDED"
    return order


async def expire_pending_orders(db: AsyncSession) -> int:
    """系统扫单：关闭所有超时未支付订单。"""
    now = utcnow_naive()
    rows = list(
        await db.scalars(
            select(Order)
            .where(
                Order.status == "CREATED",
                Order.expire_at.is_not(None),
                Order.expire_at < now,
            )
            .with_for_update()
        )
    )
    for order in rows:
        await close_expired_order(db, order, commit=False)
    if rows:
        await db.commit()
    return len(rows)


async def list_my_orders(
    db: AsyncSession,
    user_id: int,
    page: int,
    page_size: int,
) -> tuple[list[dict], int]:
    from sqlalchemy import func

    stmt = select(Order).where(Order.user_id == user_id)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        await db.scalars(
            stmt.order_by(Order.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    items = [await order_to_out(db, o) for o in rows]
    return items, total
