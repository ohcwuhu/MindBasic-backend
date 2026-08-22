"""AI 自我教练对话记录接口。"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, get_current_user
from app.api.response import ok, paginated
from app.models.user import User
from app.schemas.ai_conversation import AiConversationOut, AiMessageOut
from app.services.ai_conversation_service import (
    get_ai_conversation_or_404,
    list_ai_conversations,
    list_ai_messages,
)
from app.utils.time import to_iso

router = APIRouter(prefix="/ai-conversations", tags=["ai-conversations"])


@router.get("")
async def my_ai_conversations(
    request: Request,
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=10, ge=1, le=50, alias="pageSize"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    items, total = await list_ai_conversations(db, user.id, page, pageSize)
    out = [AiConversationOut(**item).model_dump(by_alias=True) for item in items]
    return ok(paginated(out, total, page, pageSize), trace_id=request.state.trace_id)


@router.get("/{conversation_id}")
async def ai_conversation_detail(
    conversation_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    conv = await get_ai_conversation_or_404(db, user.id, conversation_id)
    messages = await list_ai_messages(db, conv.id)
    return ok(
        {
            "conversation": AiConversationOut(
                id=conv.id,
                title=conv.title,
                status=conv.status,
                message_count=conv.message_count,
                created_at=to_iso(conv.created_at) or "",
                updated_at=to_iso(conv.updated_at) or "",
            ).model_dump(by_alias=True),
            "messages": [AiMessageOut(**m).model_dump(by_alias=True) for m in messages],
        },
        trace_id=request.state.trace_id,
    )
