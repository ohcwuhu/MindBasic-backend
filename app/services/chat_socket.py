"""聊天 SocketIO 实时推送（独立 /chat 命名空间，连接时校验 JWT）。"""

import jwt

from sqlalchemy import select

from app.core.exceptions import AppError
from app.core.security import decode_access_token
from app.core.token_blacklist import is_blacklisted
from app.db.session import AsyncSessionLocal
from app.models.chat import ChatConversation
from app.models.coach import CoachProfile
from app.models.user import User
from app.services.chat_service import (
    get_peer_user_id,
    mark_read,
    message_to_out,
    send_message,
)
from app.services.notification_service import notify

_sid_user: dict[str, int] = {}


async def _auth_user(auth: dict | None) -> int | None:
    token = (auth or {}).get("token")
    if not token:
        return None
    try:
        payload = decode_access_token(token)
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None
    if is_blacklisted(payload.get("jti", "")):
        return None
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        return None
    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        if user is None or user.deleted_at is not None or user.status != "ENABLED":
            return None
    return user_id


def register_chat_socket_events(sio, log) -> None:
    """注册 /chat 命名空间的实时聊天事件。"""

    @sio.on("connect", namespace="/chat")
    async def chat_connect(sid, environ, auth):
        user_id = await _auth_user(auth)
        if user_id is None:
            return False
        _sid_user[sid] = user_id
        log.info("[CHAT] 连接 sid=%s user=%s", sid, user_id)
        return True

    @sio.on("disconnect", namespace="/chat")
    async def chat_disconnect(sid):
        uid = _sid_user.pop(sid, None)
        if uid:
            log.info("[CHAT] 断开 sid=%s user=%s", sid, uid)

    @sio.on("chat:join", namespace="/chat")
    async def chat_join(sid, data):
        uid = _sid_user.get(sid)
        conv_id = int((data or {}).get("conversationId", 0))
        if not uid or not conv_id:
            return
        async with AsyncSessionLocal() as db:
            conv = await db.get(ChatConversation, conv_id)
            if conv is None:
                return
            if conv.user_id == uid:
                ok_member = True
            else:
                profile = await db.scalar(select(CoachProfile).where(CoachProfile.user_id == uid))
                ok_member = profile is not None and profile.id == conv.coach_id
        if ok_member:
            await sio.enter_room(sid, f"conv:{conv_id}", namespace="/chat")

    @sio.on("chat:send", namespace="/chat")
    async def chat_send(sid, data):
        uid = _sid_user.get(sid)
        conv_id = int((data or {}).get("conversationId", 0))
        content = str((data or {}).get("content", "")).strip()
        if not uid or not conv_id or not content:
            return
        async with AsyncSessionLocal() as db:
            try:
                msg = await send_message(db, uid, conv_id, content)
                peer_uid = await get_peer_user_id(db, conv_id, uid)
            except AppError:
                return
        await sio.emit("chat:message", message_to_out(msg), room=f"conv:{conv_id}", namespace="/chat")
        if peer_uid:
            async with AsyncSessionLocal() as db:
                await notify(db, peer_uid, "CHAT", "新的聊天消息", content[:50])
                await db.commit()

    @sio.on("chat:read", namespace="/chat")
    async def chat_read(sid, data):
        uid = _sid_user.get(sid)
        conv_id = int((data or {}).get("conversationId", 0))
        if not uid or not conv_id:
            return
        async with AsyncSessionLocal() as db:
            try:
                await mark_read(db, uid, conv_id)
            except AppError:
                return
        await sio.emit("chat:read", {"conversationId": conv_id, "userId": uid}, room=f"conv:{conv_id}", namespace="/chat")

    log.info("[INIT] 聊天 SocketIO 路由注册完成（/chat 命名空间）")
