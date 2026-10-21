"""情绪日记业务逻辑（异步）。"""

import calendar as cal_mod
import random
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.models.growth import EmotionFeedbackLib, EmotionJournal
from app.utils.time import (
    business_day_range_utc_naive,
    business_month_range_utc_naive,
    business_today,
    to_iso,
)

FALLBACK_FEEDBACK = "每一种感受都值得被看见。你愿意记录它，就已经在靠近自己。"

MOOD_ORDER = ("CALM", "HAPPY", "ANXIOUS", "DOWN", "IRRITATED", "OTHER")


async def pick_feedback(db: AsyncSession, mood_type: str) -> str:
    stmt = (
        select(EmotionFeedbackLib.content)
        .where(
            EmotionFeedbackLib.mood_type == mood_type,
            EmotionFeedbackLib.is_enabled.is_(True),
        )
        .order_by(EmotionFeedbackLib.sort_order)
    )
    phrases = list(await db.scalars(stmt))
    return random.choice(phrases) if phrases else FALLBACK_FEEDBACK


async def get_own_journal_or_404(db: AsyncSession, user_id: int, journal_id: int) -> EmotionJournal:
    journal = await db.scalar(
        select(EmotionJournal).where(
            EmotionJournal.id == journal_id,
            EmotionJournal.user_id == user_id,
        )
    )
    if journal is None:
        raise AppError(404, "NOT_FOUND", "日记不存在")
    return journal


def journal_to_out(journal: EmotionJournal) -> dict:
    return {
        "id": journal.id,
        "mood_type": journal.mood_type,
        "content": journal.content,
        "feedback": journal.feedback,
        "source": journal.source,
        "source_conversation_id": journal.source_conversation_id,
        "created_at": to_iso(journal.created_at),
    }


async def count_journals(
    db: AsyncSession,
    user_id: int,
    *,
    start_at=None,
    end_at=None,
) -> int:
    stmt = select(func.count()).select_from(EmotionJournal).where(
        EmotionJournal.user_id == user_id
    )
    if start_at is not None:
        stmt = stmt.where(EmotionJournal.created_at >= start_at)
    if end_at is not None:
        stmt = stmt.where(EmotionJournal.created_at < end_at)
    return await db.scalar(stmt) or 0


async def journals_trend(db: AsyncSession, user_id: int, days: int) -> dict:
    """按上海自然日聚合最近 N 天，并先在 SQL 中限定时间范围。"""
    today = business_today()
    start = today - timedelta(days=days - 1)
    start_at, _ = business_day_range_utc_naive(start)
    _, end_at = business_day_range_utc_naive(today)
    day_col = func.date(
        func.convert_tz(EmotionJournal.created_at, "+00:00", "+08:00")
    ).label("day")
    rows = (
        await db.execute(
            select(day_col, EmotionJournal.mood_type, func.count().label("cnt"))
            .where(
                EmotionJournal.user_id == user_id,
                EmotionJournal.created_at >= start_at,
                EmotionJournal.created_at < end_at,
            )
            .group_by(day_col, EmotionJournal.mood_type)
            .order_by(day_col)
        )
    ).all()

    buckets: dict[str, dict[str, int]] = {}
    for day, mood, count in rows:
        buckets.setdefault(str(day), {})[mood] = int(count)

    items: list[dict] = []
    summary: dict[str, int] = {mood: 0 for mood in MOOD_ORDER}
    cursor = start
    while cursor <= today:
        key = cursor.isoformat()
        day_moods = buckets.get(key, {})
        items.append({"date": key, "moods": day_moods})
        for mood, count in day_moods.items():
            summary[mood] = summary.get(mood, 0) + count
        cursor += timedelta(days=1)
    return {"days": days, "items": items, "summary": summary}


async def journals_calendar(db: AsyncSession, user_id: int, year: int, month: int) -> dict:
    """按上海自然日聚合指定月份，仅扫描该月对应的 UTC 半开区间。"""
    _, start_at, end_at = business_month_range_utc_naive(year, month)
    day_col = func.date(
        func.convert_tz(EmotionJournal.created_at, "+00:00", "+08:00")
    ).label("day")
    rows = (
        await db.execute(
            select(day_col, EmotionJournal.mood_type, func.count().label("cnt"))
            .where(
                EmotionJournal.user_id == user_id,
                EmotionJournal.created_at >= start_at,
                EmotionJournal.created_at < end_at,
            )
            .group_by(day_col, EmotionJournal.mood_type)
        )
    ).all()
    buckets: dict[str, dict[str, int]] = {}
    for day, mood, count in rows:
        buckets.setdefault(str(day), {})[mood] = int(count)

    _, last_day = cal_mod.monthrange(year, month)
    days: list[dict] = []
    summary: dict[str, int] = {mood: 0 for mood in MOOD_ORDER}
    for day in range(1, last_day + 1):
        key = f"{year:04d}-{month:02d}-{day:02d}"
        moods = buckets.get(key, {})
        days.append({"date": key, "moods": moods, "count": sum(moods.values())})
        for mood, count in moods.items():
            summary[mood] = summary.get(mood, 0) + count
    return {"year": year, "month": month, "days": days, "summary": summary}
