"""情绪日记业务逻辑（异步）。"""

import random
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.models.growth import EmotionFeedbackLib, EmotionJournal
from app.utils.time import to_iso

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
        "created_at": to_iso(journal.created_at),
    }


async def count_journals(db: AsyncSession, user_id: int) -> int:
    return (
        await db.scalar(
            select(func.count()).select_from(EmotionJournal).where(EmotionJournal.user_id == user_id)
        )
        or 0
    )


async def journals_trend(db: AsyncSession, user_id: int, days: int) -> dict:
    """按天聚合情绪分布（以数据库本地日期为基准），返回最近 N 天 + 汇总。"""
    day_col = func.date(EmotionJournal.created_at).label("day")
    rows = (
        await db.execute(
            select(day_col, EmotionJournal.mood_type, func.count().label("cnt"))
            .where(EmotionJournal.user_id == user_id)
            .group_by(day_col, EmotionJournal.mood_type)
            .order_by(day_col)
        )
    ).all()
    today = await db.scalar(select(func.curdate()))
    start = today - timedelta(days=days - 1)

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
