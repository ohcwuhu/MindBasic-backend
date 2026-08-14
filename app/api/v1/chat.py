"""线上聊天（用户—教练）。"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, get_current_user
from app.api.response import ok
from app.models.user import User
from app.schemas.chat import SendMessageIn, StartConversationIn
from app.services.chat_service import (
    get_or_create_conversation,
    list_conversations,
    list_messages,
    mark_read,
    message_to_out,
    send_message,
    unread_total,
)

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/conversations")
async def start_conversation(
    body: StartConversationIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    conv = await get_or_create_conversation(db, user, body.coach_id)
    return ok(
        {"id": conv.id, "coachId": conv.coach_id, "created": True},
        trace_id=request.state.trace_id,
    )


@router.get("/conversations")
async def my_conversations(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    return ok(await list_conversations(db, user), trace_id=request.state.trace_id)


@router.get("/conversations/{conversation_id}/messages")
async def conversation_messages(
    conversation_id: int,
    request: Request,
    beforeId: int | None = Query(default=None, alias="beforeId"),
    limit: int = Query(default=50, ge=1, le=100, alias="limit"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    items = await list_messages(db, user.id, conversation_id, beforeId, limit)
    return ok({"items": items}, trace_id=request.state.trace_id)


@router.post("/conversations/{conversation_id}/messages")
async def create_message(
    conversation_id: int,
    body: SendMessageIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    msg = await send_message(db, user.id, conversation_id, body.content.strip())
    return ok(message_to_out(msg), trace_id=request.state.trace_id)


@router.post("/conversations/{conversation_id}/read")
async def read_conversation(
    conversation_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    marked = await mark_read(db, user.id, conversation_id)
    return ok({"marked": marked}, trace_id=request.state.trace_id)


@router.get("/unread-count")
async def chat_unread(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    return ok({"count": await unread_total(db, user.id)}, trace_id=request.state.trace_id)
