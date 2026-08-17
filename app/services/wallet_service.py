"""钱包服务：余额查询、充值与扣减、流水（阶段一支付锁定）。"""

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.models.v1_1 import Wallet, WalletTransaction
from app.utils.time import to_iso


async def get_wallet(db: AsyncSession, user_id: int) -> Wallet | None:
    return await db.scalar(select(Wallet).where(Wallet.user_id == user_id))


async def get_or_create_wallet(db: AsyncSession, user_id: int) -> Wallet:
    wallet = await get_wallet(db, user_id)
    if wallet is not None:
        return wallet
    wallet = Wallet(user_id=user_id, balance_in_cents=0, version=0)
    db.add(wallet)
    await db.flush()
    return wallet


async def _append_tx(
    db: AsyncSession,
    wallet: Wallet,
    change: int,
    biz_type: str,
    order_id: int | None,
    note: str | None,
) -> None:
    db.add(WalletTransaction(
        wallet_id=wallet.id,
        change_in_cents=change,
        balance_after=wallet.balance_in_cents,
        biz_type=biz_type,
        order_id=order_id,
        note=note,
    ))


async def credit_wallet(
    db: AsyncSession,
    user_id: int,
    amount_in_cents: int,
    biz_type: str,
    order_id: int | None = None,
    note: str | None = None,
) -> Wallet:
    """余额入账（充值/退款/后台发放）。"""
    wallet = await get_or_create_wallet(db, user_id)
    await db.execute(
        update(Wallet)
        .where(Wallet.id == wallet.id)
        .values(
            balance_in_cents=Wallet.balance_in_cents + amount_in_cents,
            version=Wallet.version + 1,
        )
    )
    await db.refresh(wallet)
    await _append_tx(db, wallet, amount_in_cents, biz_type, order_id, note)
    return wallet


async def debit_wallet(
    db: AsyncSession,
    user_id: int,
    amount_in_cents: int,
    biz_type: str,
    order_id: int | None = None,
    note: str | None = None,
) -> Wallet:
    """余额扣减（原子条件更新，防并发透支）。"""
    wallet = await get_or_create_wallet(db, user_id)
    if wallet.balance_in_cents < amount_in_cents:
        raise AppError(400, "INSUFFICIENT_BALANCE", "余额不足，请先充值")
    result = await db.execute(
        update(Wallet)
        .where(Wallet.id == wallet.id, Wallet.balance_in_cents >= amount_in_cents)
        .values(
            balance_in_cents=Wallet.balance_in_cents - amount_in_cents,
            version=Wallet.version + 1,
        )
    )
    if result.rowcount == 0:
        raise AppError(409, "BALANCE_CONFLICT", "余额变动冲突，请重试")
    await db.refresh(wallet)
    await _append_tx(db, wallet, -amount_in_cents, biz_type, order_id, note)
    return wallet


def wallet_to_out(wallet: Wallet) -> dict:
    return {"balance_in_cents": wallet.balance_in_cents}


async def list_wallet_transactions(
    db: AsyncSession,
    user_id: int,
    page: int,
    page_size: int,
) -> tuple[list[dict], int]:
    wallet = await get_or_create_wallet(db, user_id)
    stmt = select(WalletTransaction).where(WalletTransaction.wallet_id == wallet.id)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        await db.scalars(
            stmt.order_by(WalletTransaction.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    items = [
        {
            "change_in_cents": r.change_in_cents,
            "balance_after": r.balance_after,
            "biz_type": r.biz_type,
            "note": r.note,
            "created_at": to_iso(r.created_at),
        }
        for r in rows
    ]
    return items, total
