"""用户订单接口（支付锁定阶段一）。"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, get_current_user, require_role
from app.api.response import ok, paginated
from app.models.user import User
from app.schemas.order import OrderOut, PayOrderIn
from app.services.order_service import (
    expire_pending_orders,
    get_order_or_404,
    list_my_orders,
    order_to_out,
    pay_order,
)

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post("/{order_no}/pay")
async def pay_order_route(
    order_no: str,
    body: PayOrderIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    order = await pay_order(db, user, order_no, body.method)
    return ok(OrderOut(**await order_to_out(db, order)).model_dump(by_alias=True), trace_id=request.state.trace_id)


@router.get("/{order_no}")
async def order_detail(
    order_no: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    order = await get_order_or_404(db, order_no, user)
    return ok(OrderOut(**await order_to_out(db, order)).model_dump(by_alias=True), trace_id=request.state.trace_id)


@router.get("")
async def my_orders(
    request: Request,
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=10, ge=1, le=50, alias="pageSize"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    items, total = await list_my_orders(db, user.id, page, pageSize)
    out = [OrderOut(**item).model_dump(by_alias=True) for item in items]
    return ok(paginated(out, total, page, pageSize), trace_id=request.state.trace_id)


@router.post("/expire-sweep")
async def expire_sweep_route(
    request: Request,
    admin: User = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    closed = await expire_pending_orders(db)
    return ok({"closed": closed}, trace_id=request.state.trace_id)
