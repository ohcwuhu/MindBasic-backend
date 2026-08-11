"""站内通知业务逻辑。"""

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.models.notification import Notification
from app.utils.time import to_iso


def notify(db: Session, user_id: int, type_: str, title: str, content: str) -> None:
    """写入一条通知（由调用方负责提交事务）。"""
    db.add(Notification(user_id=user_id, type=type_, title=title, content=content, is_read=False))


async def list_notifications(
    db: AsyncSession, user_id: int, unread_only: bool, page: int, page_size: int
) -> tuple[list[Notification], int]:
    stmt = select(Notification).where(Notification.user_id == user_id)
    if unread_only:
        stmt = stmt.where(Notification.is_read.is_(False))
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        await db.scalars(
            stmt.order_by(Notification.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return rows, total


async def unread_count(db: AsyncSession, user_id: int) -> int:
    return (
        await db.scalar(
            select(func.count()).select_from(Notification).where(
                Notification.user_id == user_id,
                Notification.is_read.is_(False),
            )
        )
        or 0
    )


async def mark_read(db: AsyncSession, user_id: int, notification_id: int) -> None:
    result = await db.execute(
        update(Notification)
        .where(Notification.id == notification_id, Notification.user_id == user_id)
        .values(is_read=True)
    )
    if result.rowcount == 0:
        raise AppError(404, "NOT_FOUND", "通知不存在")
    await db.commit()


async def mark_all_read(db: AsyncSession, user_id: int) -> int:
    result = await db.execute(
        update(Notification)
        .where(Notification.user_id == user_id, Notification.is_read.is_(False))
        .values(is_read=True)
    )
    await db.commit()
    return result.rowcount


def notification_to_out(notification: Notification) -> dict:
    return {
        "id": notification.id,
        "type": notification.type,
        "title": notification.title,
        "content": notification.content,
        "isRead": bool(notification.is_read),
        "createdAt": to_iso(notification.created_at),
    }
