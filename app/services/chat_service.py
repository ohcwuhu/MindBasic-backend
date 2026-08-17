"""线上聊天业务逻辑（用户—教练）。"""

import os

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.models.chat import ChatConversation, ChatMessage
from app.models.coach import CoachProfile
from app.models.user import User
from app.services.crisis_service import maybe_flag_crisis
from app.utils.time import to_iso, utcnow_naive


# 免费沟通额度（可经环境变量覆盖）
FREE_REPLY_LIMIT = int(os.environ.get("CHAT_FREE_REPLY_LIMIT", "3"))


async def _get_approved_coach(db: AsyncSession, coach_id: int) -> CoachProfile:
    profile = await db.get(CoachProfile, coach_id)
    if profile is None or profile.deleted_at is not None or profile.audit_status != "APPROVED":
        raise AppError(404, "COACH_NOT_FOUND", "教练不存在或未通过审核")
    return profile


async def _is_member(db: AsyncSession, user_id: int, conversation_id: int) -> ChatConversation | None:
    """校验用户是否为会话成员（普通用户或该教练本人），返回会话。"""
    conv = await db.get(ChatConversation, conversation_id)
    if conv is None:
        raise AppError(404, "CONVERSATION_NOT_FOUND", "会话不存在")
    if conv.user_id == user_id:
        return conv
    profile = await db.scalar(select(CoachProfile).where(CoachProfile.user_id == user_id))
    if profile is not None and profile.id == conv.coach_id:
        return conv
    raise AppError(403, "FORBIDDEN", "无权访问该会话")


async def get_or_create_conversation(db: AsyncSession, user: User, coach_id: int) -> ChatConversation:
    profile = await _get_approved_coach(db, coach_id)
    if profile.user_id == user.id:
        raise AppError(400, "CANNOT_CHAT_SELF", "不能和自己发起会话")
    conv = await db.scalar(
        select(ChatConversation).where(
            ChatConversation.user_id == user.id,
            ChatConversation.coach_id == coach_id,
        )
    )
    if conv is None:
        conv = ChatConversation(user_id=user.id, coach_id=coach_id, free_limit=FREE_REPLY_LIMIT)
        db.add(conv)
        await db.commit()
        await db.refresh(conv)
    return conv


async def list_conversations(db: AsyncSession, user: User) -> list[dict]:
    """按角色返回我的会话列表（含对方昵称/头像/未读数）。"""
    profile = await db.scalar(select(CoachProfile).where(CoachProfile.user_id == user.id))
    if profile is not None:
        convs = list(
            await db.scalars(
                select(ChatConversation)
                .where(ChatConversation.coach_id == profile.id)
                .order_by(ChatConversation.last_message_at.desc(), ChatConversation.id.desc())
            )
        )
        is_coach = True
    else:
        convs = list(
            await db.scalars(
                select(ChatConversation)
                .where(ChatConversation.user_id == user.id)
                .order_by(ChatConversation.last_message_at.desc(), ChatConversation.id.desc())
            )
        )
        is_coach = False

    if not convs:
        return []

    # 对方信息（批量查询，避免 N+1）
    if is_coach:
        peers = list(await db.scalars(select(User).where(User.id.in_({c.user_id for c in convs}))))
        user_map = {p.id: (p.nickname, p.avatar_url) for p in peers}
        peer_map = {c.id: user_map.get(c.user_id, ("用户", None)) for c in convs}
    else:
        profiles = list(
            await db.scalars(select(CoachProfile).where(CoachProfile.id.in_({c.coach_id for c in convs})))
        )
        prof_map = {p.id: p.user_id for p in profiles}
        peers = list(await db.scalars(select(User).where(User.id.in_(set(prof_map.values())))))
        user_map = {p.id: (p.nickname, p.avatar_url) for p in peers}
        peer_map = {c.id: user_map.get(prof_map.get(c.coach_id, 0), ("教练", None)) for c in convs}

    # 未读数（对方发来的未读消息）
    conv_ids = [c.id for c in convs]
    unread_rows = (
        await db.execute(
            select(ChatMessage.conversation_id, func.count())
            .where(
                ChatMessage.conversation_id.in_(conv_ids),
                ChatMessage.sender_id != user.id,
                ChatMessage.read_at.is_(None),
            )
            .group_by(ChatMessage.conversation_id)
        )
    ).all()
    unread_map = {conv_id: count for conv_id, count in unread_rows}

    result = []
    for conv in convs:
        nickname, avatar = peer_map[conv.id]
        result.append(
            {
                "id": conv.id,
                "coachId": conv.coach_id,
                "peerNickname": nickname,
                "peerAvatar": avatar,
                "lastMessagePreview": conv.last_message_preview or "",
                "lastMessageAt": to_iso(conv.last_message_at),
                "unreadCount": unread_map.get(conv.id, 0),
                "freeLimit": conv.free_limit,
                "coachReplyCount": conv.coach_reply_count,
                "unlocked": bool(conv.unlocked),
                "limitReached": (not conv.unlocked) and conv.coach_reply_count >= conv.free_limit,
            }
        )
    return result


async def list_messages(
    db: AsyncSession,
    user_id: int,
    conversation_id: int,
    before_id: int | None,
    limit: int,
) -> list[dict]:
    await _is_member(db, user_id, conversation_id)
    stmt = select(ChatMessage).where(ChatMessage.conversation_id == conversation_id)
    if before_id:
        stmt = stmt.where(ChatMessage.id < before_id)
    rows = list(
        await db.scalars(
            stmt.order_by(ChatMessage.id.desc()).limit(limit)
        )
    )
    rows.reverse()
    return [message_to_out(m) for m in rows]


async def send_message(
    db: AsyncSession, user_id: int, conversation_id: int, content: str
) -> ChatMessage:
    conv = await _is_member(db, user_id, conversation_id)
    role = "COACH" if conv.user_id != user_id else "USER"
    if not conv.unlocked and conv.coach_reply_count >= conv.free_limit:
        if role == "COACH":
            raise AppError(403, "CHAT_LIMIT_REACHED", "免费沟通额度已用完，请引导用户预约正式服务后再继续")
        raise AppError(403, "CHAT_LIMIT_REACHED", "免费沟通额度已用完，请预约正式服务后继续沟通")
    msg = ChatMessage(
        conversation_id=conversation_id,
        sender_id=user_id,
        sender_role=role,
        content=content,
    )
    db.add(msg)
    if role == "COACH":
        conv.coach_reply_count += 1
    conv.last_message_preview = content[:255]
    conv.last_message_at = utcnow_naive()
    if role == "USER":
        await maybe_flag_crisis(db, user_id, "CHAT", content)
    await db.commit()
    await db.refresh(msg)
    return msg


async def unlock_conversation(db: AsyncSession, user_id: int, coach_id: int) -> None:
    """用户预约后解锁该用户-教练会话（后续沟通不再受限）。"""
    conv = await db.scalar(
        select(ChatConversation).where(
            ChatConversation.user_id == user_id,
            ChatConversation.coach_id == coach_id,
        )
    )
    if conv is not None and not conv.unlocked:
        conv.unlocked = True
        await db.commit()


async def mark_read(db: AsyncSession, user_id: int, conversation_id: int) -> int:
    await _is_member(db, user_id, conversation_id)
    result = await db.execute(
        update(ChatMessage)
        .where(
            ChatMessage.conversation_id == conversation_id,
            ChatMessage.sender_id != user_id,
            ChatMessage.read_at.is_(None),
        )
        .values(read_at=utcnow_naive())
    )
    await db.commit()
    return result.rowcount


async def unread_total(db: AsyncSession, user_id: int) -> int:
    """当前用户在所有会话中的未读消息数。"""
    if user_id is None:
        return 0
    user = await db.get(User, user_id)
    if user is None:
        return 0
    profile = await db.scalar(select(CoachProfile).where(CoachProfile.user_id == user_id))
    if profile is not None:
        conv_ids = list(
            await db.scalars(select(ChatConversation.id).where(ChatConversation.coach_id == profile.id))
        )
    else:
        conv_ids = list(
            await db.scalars(select(ChatConversation.id).where(ChatConversation.user_id == user_id))
        )
    if not conv_ids:
        return 0
    return (
        await db.scalar(
            select(func.count()).select_from(ChatMessage).where(
                ChatMessage.conversation_id.in_(conv_ids),
                ChatMessage.sender_id != user_id,
                ChatMessage.read_at.is_(None),
            )
        )
        or 0
    )


async def get_peer_user_id(db: AsyncSession, conversation_id: int, my_user_id: int) -> int:
    conv = await db.get(ChatConversation, conversation_id)
    if conv is None:
        return 0
    if conv.user_id == my_user_id:
        profile = await db.get(CoachProfile, conv.coach_id)
        return profile.user_id if profile else 0
    return conv.user_id


def message_to_out(msg: ChatMessage) -> dict:
    return {
        "id": msg.id,
        "conversationId": msg.conversation_id,
        "senderId": msg.sender_id,
        "senderRole": msg.sender_role,
        "content": msg.content,
        "readAt": to_iso(msg.read_at),
        "createdAt": to_iso(msg.created_at),
    }
