from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, get_current_user
from app.api.response import ok, paginated
from app.core.exceptions import AppError
from app.models.ai_conversation import AiConversation
from app.models.growth import EmotionJournal
from app.models.user import User
from app.schemas.emotion_journal import (
    EmotionCalendarOut,
    EmotionJournalIn,
    EmotionJournalOut,
    EmotionTrendOut,
)
from app.services.emotion_journal_service import (
    count_journals,
    journals_trend,
    journals_calendar,
    get_own_journal_or_404,
    journal_to_out,
    pick_feedback,
)
from app.services.crisis_service import maybe_flag_crisis

router = APIRouter(prefix="/emotion-journals", tags=["emotion-journals"])


@router.post("", status_code=201)
async def create_journal(
    body: EmotionJournalIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    feedback = await pick_feedback(db, body.mood_type)
    source = body.source
    source_conversation_id = body.source_conversation_id
    if source_conversation_id is not None:
        conv = await db.scalar(
            select(AiConversation).where(
                AiConversation.id == source_conversation_id,
                AiConversation.user_id == user.id,
            )
        )
        if conv is None:
            raise AppError(404, "NOT_FOUND", "对话记录不存在")
        if conv.journal_id is not None:
            raise AppError(409, "JOURNAL_EXISTS", "该对话已生成情绪日记")
        source = "SELF_COACHING"
    journal = EmotionJournal(
        user_id=user.id,
        mood_type=body.mood_type,
        content=body.content.strip(),
        feedback=feedback,
        source=source,
        source_conversation_id=source_conversation_id,
    )
    db.add(journal)
    if source_conversation_id is not None:
        await db.flush()
        conv = await db.get(AiConversation, source_conversation_id)
        if conv is not None:
            conv.journal_id = journal.id
    await maybe_flag_crisis(db, user.id, "EMOTION_JOURNAL", body.content)
    await db.commit()
    await db.refresh(journal)
    return ok(
        EmotionJournalOut(**journal_to_out(journal)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


@router.get("")
async def list_journals(
    request: Request,
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=10, ge=1, le=50, alias="pageSize"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    stmt = (
        select(EmotionJournal)
        .where(EmotionJournal.user_id == user.id)
        .order_by(EmotionJournal.created_at.desc())
        .offset((page - 1) * pageSize)
        .limit(pageSize)
    )
    journals = list(await db.scalars(stmt))
    items = [EmotionJournalOut(**journal_to_out(j)).model_dump(by_alias=True) for j in journals]
    total = await count_journals(db, user.id)
    return ok(paginated(items, total, page, pageSize), trace_id=request.state.trace_id)


@router.get("/trend")
async def journal_trend(
    request: Request,
    days: int = Query(default=30, ge=7, le=90),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    return ok(
        EmotionTrendOut(**await journals_trend(db, user.id, days)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


@router.get("/calendar")
async def journal_calendar(
    request: Request,
    month: str | None = Query(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    if month:
        year, mon = (int(part) for part in month.split("-"))
    else:
        now = datetime.now()
        year, mon = now.year, now.month
    return ok(
        EmotionCalendarOut(**await journals_calendar(db, user.id, year, mon)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


@router.delete("/{journal_id}", status_code=204)
async def delete_journal(
    journal_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> None:
    journal = await get_own_journal_or_404(db, user.id, journal_id)
    await db.delete(journal)
    await db.commit()
