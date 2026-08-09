"""情绪日记业务逻辑。"""

import random

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.models.growth import EmotionFeedbackLib, EmotionJournal
from app.utils.time import to_iso

FALLBACK_FEEDBACK = "每一种感受都值得被看见。你愿意记录它，就已经在靠近自己。"


def pick_feedback(db: Session, mood_type: str) -> str:
    stmt = (
        select(EmotionFeedbackLib.content)
        .where(
            EmotionFeedbackLib.mood_type == mood_type,
            EmotionFeedbackLib.is_enabled.is_(True),
        )
        .order_by(EmotionFeedbackLib.sort_order)
    )
    phrases = list(db.scalars(stmt))
    return random.choice(phrases) if phrases else FALLBACK_FEEDBACK


def get_own_journal_or_404(db: Session, user_id: int, journal_id: int) -> EmotionJournal:
    journal = db.scalar(
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


def count_journals(db: Session, user_id: int) -> int:
    return db.scalar(
        select(func.count()).select_from(EmotionJournal).where(EmotionJournal.user_id == user_id)
    ) or 0
