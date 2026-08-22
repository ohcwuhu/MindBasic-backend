"""AI 自我教练对话持久化：会话与消息入库。"""

import asyncio

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppError
from app.models.ai_conversation import AiConversation, AiMessage
from app.services.ai_lab import config as _ai_config
from app.utils.time import to_iso, utcnow_naive


MOOD_MAP = {
    "焦虑": "ANXIOUS",
    "紧张": "ANXIOUS",
    "担忧": "ANXIOUS",
    "烦躁": "IRRITATED",
    "愤怒": "IRRITATED",
    "开心": "HAPPY",
    "平静": "CALM",
    "低落": "DOWN",
    "难过": "DOWN",
    "悲伤": "DOWN",
    "委屈": "DOWN",
}


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
            "journal_id": r.journal_id,
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


def _derive_mood(messages: list[dict]) -> str:
    """从最近一条用户消息的情绪快照推导日记情绪。"""
    for m in reversed(messages):
        if m["role"] == "USER" and m.get("emotion"):
            cn = m["emotion"].get("fusion_emotion_cn")
            if cn:
                return MOOD_MAP.get(str(cn), "OTHER")
    return "OTHER"


def _heuristic_summary(messages: list[dict], title: str) -> str:
    first_user = next((m["content"] for m in messages if m["role"] == "USER"), "")
    return (first_user.strip() or title.strip() or "自我教练对话")[:120]


async def _generate_summary(messages: list[dict], title: str) -> str:
    """用 DeepSeek 总结对话为一句情绪日记；失败回退启发式摘要。"""
    transcript = "\n".join(
        ("我：" if m["role"] == "USER" else "教练：") + m["content"]
        for m in messages
    )[-3000:]
    api_key = settings.deepseek_api_key
    if not api_key or not transcript.strip():
        return _heuristic_summary(messages, title)
    try:
        import requests

        def _call() -> str:
            resp = requests.post(
                f"{_ai_config.DEEPSEEK_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": _ai_config.DEEPSEEK_MODEL,
                    "messages": [
                        {
                            "role": "system",
                            "content": "把下面这段自我教练对话总结成一句第一人称的情绪日记（30~60字），"
                            "直接输出总结，不要引号、不要前缀、不要解释。",
                        },
                        {"role": "user", "content": transcript},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 120,
                },
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()

        summary = await asyncio.get_event_loop().run_in_executor(None, _call)
        return summary[:500] if summary else _heuristic_summary(messages, title)
    except Exception:
        return _heuristic_summary(messages, title)


async def generate_summary_draft(
    db: AsyncSession,
    user,
    conversation_id: int,
) -> dict:
    """生成情绪日记草稿（不落库，由前端确认后提交）。"""
    conv = await get_ai_conversation_or_404(db, user.id, conversation_id)
    messages = await list_ai_messages(db, conv.id)
    mood = _derive_mood(messages)
    summary = await _generate_summary(messages, conv.title)
    return {
        "mood_type": mood,
        "content": summary[:500],
        "source": "SELF_COACHING",
        "conversation_id": conv.id,
    }
