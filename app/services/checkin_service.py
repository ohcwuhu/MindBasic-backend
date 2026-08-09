"""每日打卡、勋章与排行榜业务逻辑。"""

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.models.growth import Badge, CheckIn, UserBadge
from app.models.user import User
from app.utils.time import to_iso


def check_in(db: Session, user: User, content: str | None) -> tuple[CheckIn, list[Badge]]:
    today = date.today()
    if db.scalar(select(CheckIn.id).where(CheckIn.user_id == user.id, CheckIn.check_date == today)):
        raise AppError(409, "CONFLICT", "今天已经打过卡了")
    record = CheckIn(user_id=user.id, check_date=today, content=content)
    db.add(record)
    db.flush()
    earned = award_badges(db, user.id, today)
    db.commit()
    db.refresh(record)
    return record, earned


def streak_days(db: Session, user_id: int, end: date) -> int:
    streak = 0
    day = end
    while db.scalar(select(CheckIn.id).where(CheckIn.user_id == user_id, CheckIn.check_date == day)):
        streak += 1
        day -= timedelta(days=1)
    return streak


def award_badges(db: Session, user_id: int, today: date) -> list[Badge]:
    total = db.scalar(select(func.count()).select_from(CheckIn).where(CheckIn.user_id == user_id)) or 0
    streak = streak_days(db, user_id, today)
    month_start = today.replace(day=1)
    month_count = (
        db.scalar(
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
        badge = db.scalar(select(Badge).where(Badge.key == key))
        if badge is None:
            continue
        owned = db.scalar(
            select(UserBadge.id).where(UserBadge.user_id == user_id, UserBadge.badge_id == badge.id)
        )
        if owned is None:
            db.add(UserBadge(user_id=user_id, badge_id=badge.id))
            earned.append(badge)
    return earned


def my_checkins(db: Session, user_id: int, month: str) -> list[CheckIn]:
    try:
        year, mon = map(int, month.split("-"))
        start = date(year, mon, 1)
        end = (start + timedelta(days=32)).replace(day=1)
    except ValueError:
        raise AppError(400, "VALIDATION_ERROR", "月份格式应为 YYYY-MM")
    return list(
        db.scalars(
            select(CheckIn)
            .where(CheckIn.user_id == user_id, CheckIn.check_date >= start, CheckIn.check_date < end)
            .order_by(CheckIn.check_date.desc())
        )
    )


def my_badges(db: Session, user_id: int) -> list[dict]:
    rows = db.execute(
        select(Badge, UserBadge.earned_at)
        .join(UserBadge, UserBadge.badge_id == Badge.id)
        .where(UserBadge.user_id == user_id)
        .order_by(Badge.sort_order)
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


def leaderboard(db: Session, period: str) -> list[dict]:
    today = date.today()
    start = today - timedelta(days=6) if period == "week" else today.replace(day=1)
    rows = db.execute(
        select(User.nickname, func.count(CheckIn.id))
        .join(CheckIn, CheckIn.user_id == User.id)
        .where(CheckIn.check_date >= start, User.deleted_at.is_(None))
        .group_by(User.id, User.nickname)
        .order_by(func.count(CheckIn.id).desc())
        .limit(10)
    ).all()
    return [{"rank": i + 1, "nickname": nickname, "count": count} for i, (nickname, count) in enumerate(rows)]


def my_stats(db: Session, user_id: int) -> dict:
    today = date.today()
    total = db.scalar(select(func.count()).select_from(CheckIn).where(CheckIn.user_id == user_id)) or 0
    month_start = today.replace(day=1)
    month_count = (
        db.scalar(
            select(func.count()).select_from(CheckIn).where(
                CheckIn.user_id == user_id,
                CheckIn.check_date >= month_start,
            )
        )
        or 0
    )
    return {
        "streak_days": streak_days(db, user_id, today),
        "total_count": total,
        "month_count": month_count,
    }
