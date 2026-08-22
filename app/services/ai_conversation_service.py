"""AI 自我教练对话持久化：会话与消息入库。"""

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.models.ai_conversation import AiConversation, AiMessage
from app.utils.time import to_iso, utcnow_naive


async def get_or_create_active_conversation(db: AsyncSession, user_id: int) -> AiConversation:
    """取用户最近的 ACTIVE 会话，没有则新建。"""
    conv = await db.scalar(
        select(AiConversation)
        .where(AiConversation.user_id == user_id, AiConversation.status == "ACTIVE")
        .order_by(AiConversation.created_at.desc())
        .limit(1)
    )
    if conv is not None:
        return conv
    conv = AiConversation(user_id=user_id, title="自我教练对话", status="ACTIVE", message_count=0)
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    return conv


async def append_ai_message(
    db: AsyncSession,
    conversation_id: int,
    role: str,
    content: str,
    emotion: dict | None = None,
) -> AiMessage:
    """写入一条对话消息并更新计数；首条用户消息作为标题。"""
    msg = AiMessage(
        conversation_id=conversation_id,
        role=role,
        content=content,
        emotion=emotion,
    )
    db.add(msg)
    if role == "USER":
        conv = await db.get(AiConversation, conversation_id)
        if conv is not None and conv.message_count == 0:
            conv.title = content.strip()[:30] or "自我教练对话"
    await db.execute(
        update(AiConversation)
        .where(AiConversation.id == conversation_id)
        .values(message_count=AiConversation.message_count + 1)
    )
    await db.commit()
    await db.refresh(msg)
    return msg


async def end_ai_conversation(db: AsyncSession, conversation_id: int) -> None:
    await db.execute(
        update(AiConversation)
        .where(AiConversation.id == conversation_id, AiConversation.status == "ACTIVE")
        .values(status="ENDED", ended_at=utcnow_naive())
    )
    await db.commit()


async def list_ai_conversations(
    db: AsyncSession,
    user_id: int,
    page: int,
    page_size: int,
) -> tuple[list[dict], int]:
    stmt = select(AiConversation).where(AiConversation.user_id == user_id)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        await db.scalars(
            stmt.order_by(AiConversation.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    items = [
        {
            "id": r.id,
            "title": r.title,
            "status": r.status,
            "message_count": r.message_count,
            "created_at": to_iso(r.created_at),
            "updated_at": to_iso(r.updated_at),
        }
        for r in rows
    ]
    return items, total


async def get_ai_conversation_or_404(db: AsyncSession, user_id: int, conversation_id: int) -> AiConversation:
    conv = await db.scalar(
        select(AiConversation).where(
            AiConversation.id == conversation_id,
            AiConversation.user_id == user_id,
        )
    )
    if conv is None:
        raise AppError(404, "NOT_FOUND", "对话记录不存在")
    return conv


async def list_ai_messages(db: AsyncSession, conversation_id: int) -> list[dict]:
    rows = list(
        await db.scalars(
            select(AiMessage)
            .where(AiMessage.conversation_id == conversation_id)
            .order_by(AiMessage.created_at.asc())
        )
    )
    return [
        {
            "id": r.id,
            "role": r.role,
            "content": r.content,
            "emotion": r.emotion,
            "created_at": to_iso(r.created_at),
        }
        for r in rows
    ]
