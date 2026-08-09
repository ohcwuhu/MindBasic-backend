from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.response import ok, paginated
from app.models.growth import EmotionJournal
from app.models.user import User
from app.schemas.emotion_journal import EmotionJournalIn, EmotionJournalOut
from app.services.emotion_journal_service import (
    count_journals,
    get_own_journal_or_404,
    journal_to_out,
    pick_feedback,
)

router = APIRouter(prefix="/emotion-journals", tags=["emotion-journals"])


@router.post("", status_code=201)
def create_journal(
    body: EmotionJournalIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    feedback = pick_feedback(db, body.mood_type)
    journal = EmotionJournal(
        user_id=user.id,
        mood_type=body.mood_type,
        content=body.content.strip(),
        feedback=feedback,
    )
    db.add(journal)
    db.commit()
    db.refresh(journal)
    return ok(
        EmotionJournalOut(**journal_to_out(journal)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


@router.get("")
def list_journals(
    request: Request,
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=10, ge=1, le=50, alias="pageSize"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    stmt = (
        select(EmotionJournal)
        .where(EmotionJournal.user_id == user.id)
        .order_by(EmotionJournal.created_at.desc())
        .offset((page - 1) * pageSize)
        .limit(pageSize)
    )
    journals = list(db.scalars(stmt))
    items = [EmotionJournalOut(**journal_to_out(j)).model_dump(by_alias=True) for j in journals]
    total = count_journals(db, user.id)
    return ok(paginated(items, total, page, pageSize), trace_id=request.state.trace_id)


@router.delete("/{journal_id}", status_code=204)
def delete_journal(
    journal_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    journal = get_own_journal_or_404(db, user.id, journal_id)
    db.delete(journal)
    db.commit()
