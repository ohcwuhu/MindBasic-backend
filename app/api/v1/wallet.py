"""用户钱包接口（阶段一：余额查询 / 模拟充值 / 流水）。"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, get_current_user
from app.api.response import ok, paginated
from app.models.user import User
from app.schemas.order import TopupIn, WalletOut, WalletTransactionOut
from app.services.wallet_service import (
    credit_wallet,
    get_or_create_wallet,
    list_wallet_transactions,
    wallet_to_out,
)

router = APIRouter(prefix="/wallet", tags=["wallet"])


@router.get("")
async def my_wallet(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    wallet = await get_or_create_wallet(db, user.id)
    return ok(WalletOut(**wallet_to_out(wallet)).model_dump(by_alias=True), trace_id=request.state.trace_id)


@router.get("/transactions")
async def wallet_transactions(
    request: Request,
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=20, ge=1, le=50, alias="pageSize"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    items, total = await list_wallet_transactions(db, user.id, page, pageSize)
    out = [WalletTransactionOut(**item).model_dump(by_alias=True) for item in items]
    return ok(paginated(out, total, page, pageSize), trace_id=request.state.trace_id)


@router.post("/topup", status_code=201)
async def topup_wallet(
    body: TopupIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    """阶段一：模拟充值，直接到账余额（后续接真支付后改为创建充值单）。"""
    wallet = await credit_wallet(
        db,
        user.id,
        body.amount_in_cents,
        "TOPUP",
        note="模拟充值",
    )
    await db.commit()
    return ok(
        {"balanceInCents": wallet.balance_in_cents, "credited": True},
        trace_id=request.state.trace_id,
    )
