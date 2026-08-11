"""每日打卡、勋章与排行榜业务逻辑（异步）。"""

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.models.growth import Badge, CheckIn, UserBadge
from app.models.user import User
from app.utils.time import to_iso


async def check_in(db: AsyncSession, user: User, content: str | None) -> tuple[CheckIn, list[Badge]]:
    today = date.today()
    if await db.scalar(select(CheckIn.id).where(CheckIn.user_id == user.id, CheckIn.check_date == today)):
        raise AppError(409, "CONFLICT", "今天已经打过卡了")
    record = CheckIn(user_id=user.id, check_date=today, content=content)
    db.add(record)
    await db.flush()
    earned = await award_badges(db, user.id, today)
    await db.commit()
    await db.refresh(record)
    return record, earned


async def streak_days(db: AsyncSession, user_id: int, end: date) -> int:
    streak = 0
    day = end
    while await db.scalar(select(CheckIn.id).where(CheckIn.user_id == user_id, CheckIn.check_date == day)):
        streak += 1
        day -= timedelta(days=1)
    return streak


async def award_badges(db: AsyncSession, user_id: int, today: date) -> list[Badge]:
    total = await db.scalar(select(func.count()).select_from(CheckIn).where(CheckIn.user_id == user_id)) or 0
    streak = await streak_days(db, user_id, today)
    month_start = today.replace(day=1)
    month_count = (
        await db.scalar(
            select(func.count()).select_from(CheckIn).where(
                CheckIn.user_id == user_id,
                CheckIn.check_date >= month_start,
            )
        )
        or 0
    )
    targets = {
        "FIRST_CHECKIN": total >= 1,
        "STREAK_3": streak >= 3,
        "STREAK_7": streak >= 7,
        "TOTAL_10": total >= 10,
        "MONTH_15": month_count >= 15,
    }
    earned: list[Badge] = []
    for key, ok in targets.items():
        if not ok:
            continue
        badge = await db.scalar(select(Badge).where(Badge.key == key))
        if badge is None:
            continue
        owned = await db.scalar(
            select(UserBadge.id).where(UserBadge.user_id == user_id, UserBadge.badge_id == badge.id)
        )
        if owned is None:
            db.add(UserBadge(user_id=user_id, badge_id=badge.id))
            earned.append(badge)
    return earned


async def my_checkins(db: AsyncSession, user_id: int, month: str) -> list[CheckIn]:
    try:
        year, mon = map(int, month.split("-"))
        start = date(year, mon, 1)
        end = (start + timedelta(days=32)).replace(day=1)
    except ValueError:
        raise AppError(400, "VALIDATION_ERROR", "月份格式应为 YYYY-MM")
    return list(
        await db.scalars(
            select(CheckIn)
            .where(CheckIn.user_id == user_id, CheckIn.check_date >= start, CheckIn.check_date < end)
            .order_by(CheckIn.check_date.desc())
        )
    )


async def my_badges(db: AsyncSession, user_id: int) -> list[dict]:
    rows = (
        await db.execute(
            select(Badge, UserBadge.earned_at)
            .join(UserBadge, UserBadge.badge_id == Badge.id)
            .where(UserBadge.user_id == user_id)
            .order_by(Badge.sort_order)
        )
    ).all()
    return [
        {
            "id": badge.id,
            "key": badge.key,
            "name": badge.name,
            "description": badge.description,
            "icon": badge.icon,
            "earned_at": to_iso(earned_at),
        }
        for badge, earned_at in rows
    ]


async def leaderboard(db: AsyncSession, period: str) -> list[dict]:
    today = date.today()
    start = today - timedelta(days=6) if period == "week" else today.replace(day=1)
    rows = (
        await db.execute(
            # 按 user_id 分组：即使昵称相同也分开计数，昵称取自所属行
            select(User.id, User.nickname, func.count(CheckIn.id))
            .join(CheckIn, CheckIn.user_id == User.id)
            .where(CheckIn.check_date >= start, User.deleted_at.is_(None))
            .group_by(User.id, User.nickname)
            .order_by(func.count(CheckIn.id).desc())
            .limit(10)
        )
    ).all()
    return [{"rank": i + 1, "nickname": nickname, "count": count} for i, (_, nickname, count) in enumerate(rows)]


async def my_stats(db: AsyncSession, user_id: int) -> dict:
    today = date.today()
    total = await db.scalar(select(func.count()).select_from(CheckIn).where(CheckIn.user_id == user_id)) or 0
    month_start = today.replace(day=1)
    month_count = (
        await db.scalar(
            select(func.count()).select_from(CheckIn).where(
                CheckIn.user_id == user_id,
                CheckIn.check_date >= month_start,
            )
        )
        or 0
    )
    return {
        "streak_days": await streak_days(db, user_id, today),
        "total_count": total,
        "month_count": month_count,
    }
