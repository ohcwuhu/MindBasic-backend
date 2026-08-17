"""管理端：订单管理 / 手动退款 / 余额发放。"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, require_role
from app.api.response import ok, paginated
from app.core.exceptions import AppError
from app.models.coach import Appointment, AppointmentEvent, CoachSlot
from app.models.user import User
from app.models.v1_1 import Order
from app.schemas.base import ApiModel
from app.schemas.order import WalletGrantIn
from app.services.notification_service import notify
from app.services.audit_service import record_audit
from app.services.order_service import refund_paid_order
from app.services.wallet_service import credit_wallet
from app.utils.format import mask_phone
from app.utils.time import to_iso


class AdminRefundIn(ApiModel):
    reason: str | None = None


router = APIRouter(prefix="/admin/orders", tags=["admin-orders"])
wallet_router = APIRouter(prefix="/admin/wallet", tags=["admin-wallet"])


@router.get("")
async def admin_list_orders(
    request: Request,
    status: str | None = Query(default=None, pattern="^(CREATED|PAID|CLOSED|REFUNDED)$"),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=10, ge=1, le=50, alias="pageSize"),
    admin: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    stmt = select(Order)
    if status:
        stmt = stmt.where(Order.status == status)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        await db.scalars(
            stmt.order_by(Order.created_at.desc())
            .offset((page - 1) * pageSize)
            .limit(pageSize)
        )
    )
    users = {
        u.id: u
        for u in await db.scalars(select(User).where(User.id.in_([o.user_id for o in rows])))
    }
    items = [
        {
            "id": o.id,
            "order_no": o.order_no,
            "type": o.type,
            "status": o.status,
            "amount_in_cents": o.amount_in_cents,
            "user": {
                "id": users[o.user_id].id if o.user_id in users else 0,
                "nickname": users[o.user_id].nickname if o.user_id in users else "",
                "phone": mask_phone(users[o.user_id].phone) if o.user_id in users else "",
            },
            "paid_at": to_iso(o.paid_at),
            "refunded_at": to_iso(o.refunded_at),
            "created_at": to_iso(o.created_at),
        }
        for o in rows
    ]
    return ok(paginated(items, total, page, pageSize), trace_id=request.state.trace_id)


@router.post("/{order_id}/refund")
async def admin_refund_order(
    order_id: int,
    body: AdminRefundIn,
    request: Request,
    admin: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    order = await db.get(Order, order_id)
    if order is None:
        raise AppError(404, "ORDER_NOT_FOUND", "订单不存在")
    order = await refund_paid_order(db, order, order.amount_in_cents, body.reason or "管理员手动退款")
    await record_audit(
        db,
        actor_user_id=admin.id,
        actor_role="ADMIN",
        action="ADMIN_ORDER_REFUND",
        target_type="ORDER",
        target_id=order.id,
        detail={"order_no": order.order_no, "amount_in_cents": order.amount_in_cents},
        ip=request.client.host if request.client else None,
    )
    if order.appointment_id is not None:
        appointment = await db.get(Appointment, order.appointment_id)
        if appointment is not None and appointment.status in ("PENDING", "CONFIRMED"):
            appointment.status = "CANCELLED"
            appointment.cancel_reason = "管理员退款取消"
            appointment.cancel_by = admin.id
            db.add(AppointmentEvent(
                appointment_id=appointment.id,
                actor_id=admin.id,
                actor_role="ADMIN",
                event="CANCEL_SYSTEM",
                note="管理员退款取消",
            ))
            slot = await db.get(CoachSlot, appointment.slot_id)
            if slot is not None and slot.status == "BOOKED":
                slot.status = "AVAILABLE"
            await notify(
                db,
                appointment.user_id,
                "APPOINTMENT",
                "退款已到账",
                f"订单 {order.order_no} 已退款到余额。",
            )
    await db.commit()
    return ok({"order_no": order.order_no, "status": order.status}, trace_id=request.state.trace_id)


@wallet_router.post("/grant", status_code=201)
async def admin_grant_wallet(
    body: WalletGrantIn,
    request: Request,
    admin: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    wallet = await credit_wallet(
        db,
        body.user_id,
        body.amount_in_cents,
        "ADMIN_GRANT",
        note=body.note or "管理员发放",
    )
    await record_audit(
        db,
        actor_user_id=admin.id,
        actor_role="ADMIN",
        action="ADMIN_WALLET_GRANT",
        target_type="USER",
        target_id=body.user_id,
        detail={"amount_in_cents": body.amount_in_cents, "balance_in_cents": wallet.balance_in_cents},
        ip=request.client.host if request.client else None,
    )
    await db.commit()
    return ok(
        {"user_id": body.user_id, "balance_in_cents": wallet.balance_in_cents},
        trace_id=request.state.trace_id,
    )
