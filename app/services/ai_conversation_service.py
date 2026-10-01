"""AI 自我教练对话留痕与成长记录承接。

这一层解决四个问题：

1. **对话可追溯**：把实时通话的用户表达与 AI 回复落库（``ai_conversations`` /
   ``ai_messages``），"我的成长"与对话历史都有真实数据；
2. **阶段可统计**：每一轮的五阶段判定与风险分级随消息一起保存，
   成为"阶段判断 vs 人工标签"一致性实验的数据来源；
3. **授权留痕**：麦克风 / 摄像头 / 多模态的授权范围写入 ``consent_records``，
   管线据此决定使用哪些模态（科技伦理审核依据）；
4. **结果可承接**：通话结束后生成阶段总结草稿，用户确认后写入情绪日记并与原会话关联。

两类入口：

* 实时管线（SocketIO）调用 ``*_safely`` 系列：**尽力而为**，任何异常只记日志，
  绝不影响用户正在进行的对话；
* 用户接口（REST）调用普通函数：异常向上抛，由 API 层转换为业务错误。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppError
from app.db.session import AsyncSessionLocal
from app.models.ai_conversation import AiConversation, AiMessage, ConsentRecord
from app.models.growth import EmotionJournal
from app.services import crisis_rules as _crisis
from app.services.ai_lab import config as _ai_config
from app.utils.time import to_iso, utcnow_naive

_log = logging.getLogger("ai-conversation")

#: 单条消息入库长度上限（TEXT 足够，这里做防御性截断）
MAX_CONTENT_LENGTH = 8000

#: 会话总结草稿长度上限（与 emotion_journals.content 字段一致）
MAX_SUMMARY_LENGTH = 500

#: 会话来源标识
SOURCE_VIDEO_CALL = "VIDEO_CALL"

#: 当前服务协议版本（授权存证用）
DEFAULT_POLICY_VERSION = "2026-09"

MOOD_MAP = {
    "焦虑": "ANXIOUS",
    "紧张": "ANXIOUS",
    "担忧": "ANXIOUS",
    "压力": "ANXIOUS",
    "不安": "ANXIOUS",
    "烦躁": "IRRITATED",
    "愤怒": "IRRITATED",
    "生气": "IRRITATED",
    "恼火": "IRRITATED",
    "开心": "HAPPY",
    "高兴": "HAPPY",
    "愉快": "HAPPY",
    "平静": "CALM",
    "平和": "CALM",
    "中性": "OTHER",
    "低落": "DOWN",
    "难过": "DOWN",
    "悲伤": "DOWN",
    "委屈": "DOWN",
    "沮丧": "DOWN",
}

KEYWORD_MOOD = [
    ("焦虑", "ANXIOUS"),
    ("压力", "ANXIOUS"),
    ("紧张", "ANXIOUS"),
    ("烦躁", "IRRITATED"),
    ("生气", "IRRITATED"),
    ("低落", "DOWN"),
    ("难过", "DOWN"),
    ("伤心", "DOWN"),
    ("开心", "HAPPY"),
    ("平静", "CALM"),
]

#: 融合情绪（英文统一标签） → 情绪日记 mood_type 的映射
_EMOTION_TO_MOOD: dict[str, str] = {
    "happy": "HAPPY",
    "surprised": "HAPPY",
    "sad": "DOWN",
    "fearful": "ANXIOUS",
    "angry": "IRRITATED",
    "disgusted": "IRRITATED",
    "neutral": "CALM",
}

#: 阶段 → 总结草稿的收束语句（确定性模板，不依赖模型可用性）
_STAGE_SENTENCE: dict[str, str] = {
    "closing": "目前比较清楚的是：我想先把这件事的下一步定下来。",
    "action_planning": "目前比较清楚的是：我想先把这件事的下一步定下来。",
    "goal_setting": "现在更清楚自己想要什么了，还需要再想想具体怎么做。",
    "exploration": "把事情摊开说了一遍，有些部分还需要继续理清。",
    "opening": "先聊了聊最近的状态。",
}


@dataclass(slots=True)
class TurnSnapshot:
    """一轮对话的落库快照（阶段判定 + 风险分级 + 分段耗时）。"""

    turn_index: int
    user_text: str
    assistant_text: str = ""
    stage: str | None = None
    goal_clear: bool | None = None
    action_ready: bool | None = None
    should_summarize: bool | None = None
    summary_reason: str | None = None
    risk_level: str | None = None
    risk_score: int | None = None
    fusion_emotion: str | None = None
    fusion_confidence: float | None = None
    timings: dict[str, Any] = field(default_factory=dict)


# ============================================================
#  纯函数：可单测、无副作用
# ============================================================
def normalize_consent(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """规范化授权快照：只保留已知字段，布尔化，附带策略版本与时间。

    缺省视为"全部未授权"，避免客户端漏传参数时被当成已授权处理。

    ``basis`` 说明这次授权的来源（前端显式勾选 / 旧客户端缺省），
    与 ``policyVersion`` 一起构成伦理审核时"这个授权从哪来"的可追溯依据。
    """
    raw = payload or {}
    scopes = raw.get("scopes") if isinstance(raw.get("scopes"), Mapping) else raw
    return {
        "mic": bool(scopes.get("mic", False)),
        "camera": bool(scopes.get("camera", False)),
        "multimodal": bool(scopes.get("multimodal", False)),
        "policyVersion": str(raw.get("policyVersion") or DEFAULT_POLICY_VERSION)[:32],
        "basis": str(raw.get("basis") or "VIDEO_CALL_START")[:32],
        "grantedAt": datetime.now().isoformat(timespec="seconds"),
    }


def suggest_mood(fusion_emotion: str | None, *, fallback: str = "OTHER") -> str:
    """由融合情绪推导情绪日记的候选情绪类型。

    仅在用户没有选择时作为默认值，最终以用户确认为准。
    """
    if not fusion_emotion:
        return fallback
    return _EMOTION_TO_MOOD.get(str(fusion_emotion).strip().lower(), fallback)


def max_risk_level(levels: Iterable[str | None]) -> str | None:
    """取一组风险等级中的最高级别（等级序定义在 ``crisis_rules``）。"""
    best: str | None = None
    best_order = -1
    for level in levels:
        if not level:
            continue
        order = _crisis.LEVEL_ORDER.get(str(level).upper())
        if order is None:
            continue
        if order > best_order:
            best, best_order = str(level).upper(), order
    return best


def build_summary_draft(
    *,
    theme: str,
    final_stage: str | None,
    closing_text: str | None = None,
) -> str:
    """生成阶段总结草稿。

    优先使用 AI 收束分支产出的总结原文（事实一致性最好）；
    拿不到时退化为确定性模板，保证"用户确认"这一步始终有可用草稿。
    """
    if closing_text and closing_text.strip():
        return closing_text.strip()[:MAX_SUMMARY_LENGTH]
    clean_theme = " ".join(str(theme or "").split())
    if len(clean_theme) > 24:
        clean_theme = clean_theme[:24] + "…"
    sentence = _STAGE_SENTENCE.get(str(final_stage or ""), _STAGE_SENTENCE["exploration"])
    if not clean_theme:
        return sentence[:MAX_SUMMARY_LENGTH]
    return f"今天主要聊了「{clean_theme}」。{sentence}"[:MAX_SUMMARY_LENGTH]


def _truncate(text: str | None) -> str:
    return (text or "")[:MAX_CONTENT_LENGTH]


# ============================================================
#  数据库写入：实时管线使用（尽力而为，绝不抛出）
# ============================================================
async def start_session_safely(
    *,
    user_id: int | None,
    client_session_id: str,
    consent: Mapping[str, Any] | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> int | None:
    """开启一次通话会话并写入授权存证；失败只记日志。

    Returns:
        新建会话的 ``ai_conversations.id``；失败时为 ``None``（对话照常进行）。
    """
    try:
        snapshot = normalize_consent(consent)
        async with AsyncSessionLocal() as db:
            conversation = AiConversation(
                user_id=user_id,
                client_session_id=client_session_id,
                title="自我教练对话",
                status="ACTIVE",
                message_count=0,
                turn_count=0,
                consent=snapshot,
            )
            db.add(conversation)
            await db.flush()

            db.add(
                ConsentRecord(
                    user_id=user_id,
                    client_session_id=client_session_id,
                    conversation_id=conversation.id,
                    policy_version=snapshot["policyVersion"],
                    scopes={
                        "mic": snapshot["mic"],
                        "camera": snapshot["camera"],
                        "multimodal": snapshot["multimodal"],
                        "basis": snapshot["basis"],
                    },
                    source=SOURCE_VIDEO_CALL,
                    ip=(ip or None),
                    user_agent=(user_agent or None),
                )
            )
            await db.commit()
            return int(conversation.id)
    except Exception as exc:  # noqa: BLE001 - 留痕失败不得影响对话
        _log.warning("开启 AI 对话会话失败 user=%s sid=%s: %s", user_id, client_session_id, exc)
        return None


#: 断线重连续接时回填给模型的最近历史条数（与 realtime_session 的历史上限同量级）
RESUME_HISTORY_LIMIT = 20


#: 允许被"重连接回"的会话状态。
#: ``ACTIVE``  = 会话仍在进行；
#: ``ABANDONED`` = 连接异常断开（socket 掉线）被标记为中断——这类恰恰是要接回来的；
#: ``ENDED``    = 用户主动挂断，不允许再接回（否则会把已结束的通话续写下去）。
RESUMABLE_STATUSES = ("ACTIVE", "ABANDONED")


async def resume_session_safely(
    *,
    conversation_id: int | None,
    user_id: int | None,
    client_session_id: str,
) -> tuple[int | None, list[dict[str, str]]]:
    """断线重连时接回原会话，并返回可直接用于判阶段与生成的历史。

    背景：通话会话绑在 socket sid 上。断线重连会拿到新的 sid，
    服务端因此会认为是全新通话——若照原逻辑 ``start_session_safely``，
    一次通话会被拆成两条记录，且模型丢失全部上下文（表现为"教练突然失忆"）。

    Args:
        conversation_id: 客户端带回来的原会话 id。
        user_id: 当前连接已认证的用户；用于校验会话归属。
        client_session_id: 新的 sid，仅用于日志。

    Returns:
        ``(会话 id, 历史消息)``。无法接回时返回 ``(None, [])``，
        调用方应退回"新建会话"。
    """
    if not conversation_id:
        return None, []
    try:
        cid = int(conversation_id)
    except (TypeError, ValueError):
        return None, []

    try:
        async with AsyncSessionLocal() as db:
            conversation = await db.get(AiConversation, cid)
            if conversation is None:
                _log.info("重连接回失败：会话 %s 不存在", cid)
                return None, []
            if conversation.status not in RESUMABLE_STATUSES:
                _log.info(
                    "重连接回失败：会话 %s 状态为 %s（用户已挂断，不再接回）",
                    cid, conversation.status,
                )
                return None, []
            # 只允许接回自己的会话，避免用别人的 id 续写记录
            if (
                user_id is not None
                and conversation.user_id is not None
                and int(conversation.user_id) != int(user_id)
            ):
                _log.warning("重连接回被拒绝：会话 %s 不属于 user=%s", cid, user_id)
                return None, []
            messages = await list_ai_messages(db, cid)
            if conversation.status == "ABANDONED":
                # 掉线被标记为中断的通话重新接上：回到 ACTIVE，清掉结束时间
                conversation.status = "ACTIVE"
                conversation.ended_at = None
                await db.commit()

        history = [
            {"role": str(m.get("role") or ""), "content": str(m.get("content") or "")}
            for m in messages[-RESUME_HISTORY_LIMIT:]
            if m.get("role") and m.get("content")
        ]
        _log.info(
            "重连接回原会话 %s（sid=%s），回填历史 %d 条",
            cid, client_session_id, len(history),
        )
        return cid, history
    except Exception as exc:  # noqa: BLE001 - 接回失败不得影响通话
        _log.warning("重连接回会话失败 conversation=%s sid=%s: %s", conversation_id, client_session_id, exc)
        return None, []


async def record_consent_change_safely(
    *,
    user_id: int | None,
    client_session_id: str,
    conversation_id: int | None = None,
    consent: Mapping[str, Any] | None = None,
    revoked_scopes: Iterable[str] = (),
    ip: str | None = None,
    user_agent: str | None = None,
) -> int | None:
    """记录一次通话过程中的授权范围变更（重新授权或撤回）。

    ``start_session_safely`` 写的是"通话开始时的授权快照"，本函数写的是"通话过程中的
    每一次变更"，两者共同构成可追溯的授权时间线：用户中途关掉摄像头之后，伦理审核
    能在 ``consent_records`` 里看到撤回发生在哪一刻、撤回后剩哪些范围仍然有效。

    Args:
        user_id: 用户 ID（未登录为空）。
        client_session_id: SocketIO 会话标识，用于把同一通电话的授权串起来。
        conversation_id: ``ai_conversations.id``；给出时同步会话表上的授权快照。
        consent: 变更**之后**的完整授权范围（只传变化项时其余按未授权处理）。
        revoked_scopes: 本次被撤回的范围名（``camera`` / ``multimodal`` / ``mic``）。
            列出的范围会把该会话最近一条仍有效的授权行补上 ``revoked_at``。

    Returns:
        新写入的 ``consent_records.id``；失败时为 ``None``（授权留痕是旁路能力，
        任何时候都不影响用户正在进行的对话）。
    """
    try:
        snapshot = normalize_consent(consent)
        revoked = {str(scope) for scope in revoked_scopes if scope}
        async with AsyncSessionLocal() as db:
            record = ConsentRecord(
                user_id=user_id,
                client_session_id=client_session_id,
                conversation_id=conversation_id,
                policy_version=snapshot["policyVersion"],
                scopes={
                    "mic": snapshot["mic"],
                    "camera": snapshot["camera"],
                    "multimodal": snapshot["multimodal"],
                    "basis": snapshot["basis"],
                },
                source=SOURCE_VIDEO_CALL,
                ip=(ip or None),
                user_agent=(user_agent or None),
            )
            db.add(record)

            if revoked:
                # 撤回：给该会话最近一条仍然有效的授权行补 revoked_at，
                # 使"哪一项授权在什么时刻结束"有明确记录。
                previous = await db.scalar(
                    select(ConsentRecord)
                    .where(
                        ConsentRecord.client_session_id == client_session_id,
                        ConsentRecord.revoked_at.is_(None),
                    )
                    .order_by(ConsentRecord.id.desc())
                    .limit(1)
                )
                if previous is not None and any(
                    bool((previous.scopes or {}).get(scope)) for scope in revoked
                ):
                    previous.revoked_at = utcnow_naive()

            if conversation_id:
                # 会话表上的快照跟随到"当前有效范围"，读到会话行时不会看到过期授权。
                conversation = await db.get(AiConversation, int(conversation_id))
                if conversation is not None:
                    conversation.consent = snapshot

            await db.commit()
            return int(record.id)
    except Exception as exc:  # noqa: BLE001 - 留痕失败不得影响对话
        _log.warning(
            "记录授权变更失败 user=%s sid=%s: %s", user_id, client_session_id, exc
        )
        return None


async def record_message_safely(
    conversation_id: int | None,
    *,
    role: str,
    content: str,
    turn_index: int = 0,
    emotion: dict | None = None,
    snapshot: TurnSnapshot | None = None,
    timings: Mapping[str, Any] | None = None,
) -> None:
    """写入一条对话消息，并把轮次、阶段、最高风险同步到会话。

    写库失败只记日志：留痕是"旁路能力"，任何时候都不能让用户等它。

    Args:
        conversation_id: 目标会话；为空时直接返回。
        role: ``USER`` / ``ASSISTANT``。
        content: 消息正文。
        turn_index: 轮次序号（同一轮的两条消息共享序号）。
        emotion: 该轮情绪上下文快照（JSON）。
        snapshot: 轮次判定快照；给出时其中的阶段与风险字段一并落库。
        timings: 该轮分段耗时（秒）。
    """
    if not conversation_id:
        return
    try:
        async with AsyncSessionLocal() as db:
            message = AiMessage(
                conversation_id=conversation_id,
                role=role,
                content=_truncate(content),
                emotion=emotion,
                turn_index=max(0, int(turn_index or 0)),
                stage=snapshot.stage if snapshot else None,
                goal_clear=snapshot.goal_clear if snapshot else None,
                action_ready=snapshot.action_ready if snapshot else None,
                should_summarize=snapshot.should_summarize if snapshot else None,
                summary_reason=(snapshot.summary_reason if snapshot else None) or None,
                risk_level=snapshot.risk_level if snapshot else None,
                risk_score=snapshot.risk_score if snapshot else None,
                fusion_emotion=snapshot.fusion_emotion if snapshot else None,
                fusion_confidence=snapshot.fusion_confidence if snapshot else None,
                timings=dict(timings) if timings else None,
            )
            db.add(message)
            await db.flush()

            conversation = await db.get(AiConversation, conversation_id)
            if conversation is not None:
                if role == "USER" and (conversation.message_count or 0) == 0:
                    conversation.title = content.strip()[:30] or "自我教练对话"
                conversation.message_count = (conversation.message_count or 0) + 1
                if turn_index:
                    conversation.turn_count = max(conversation.turn_count or 0, int(turn_index))
                if snapshot is not None:
                    conversation.final_stage = snapshot.stage or conversation.final_stage
                    conversation.max_risk_level = max_risk_level(
                        [conversation.max_risk_level, snapshot.risk_level]
                    )
            await db.commit()
    except Exception as exc:  # noqa: BLE001 - 留痕失败不得影响对话
        _log.warning(
            "写入 AI 对话消息失败 conversation=%s role=%s turn=%s: %s",
            conversation_id, role, turn_index, exc,
        )


async def record_turn_safely(conversation_id: int | None, snapshot: TurnSnapshot) -> None:
    """一次性写入一轮的用户表达与 AI 回复（阶段/风险/耗时同步落库）。

    与 :func:`record_message_safely` 的分工：后者用于"用户消息先落库、
    AI 回复稍后落库"的实时管线（生成被中断时仍保留用户表达），
    本函数用于补齐完整轮次（例如离线回放或补写）。
    """
    if not conversation_id:
        return
    await record_message_safely(
        conversation_id,
        role="USER",
        content=snapshot.user_text,
        turn_index=snapshot.turn_index,
        snapshot=snapshot,
        timings=snapshot.timings,
    )
    if snapshot.assistant_text:
        await record_message_safely(
            conversation_id,
            role="ASSISTANT",
            content=snapshot.assistant_text,
            turn_index=snapshot.turn_index,
            snapshot=snapshot,
            timings=snapshot.timings,
        )


async def end_session_safely(conversation_id: int | None, *, status: str = "ENDED") -> None:
    """结束会话（正常挂断 ``ENDED``，异常中断 ``ABANDONED``）；失败只记日志。"""
    if not conversation_id:
        return
    try:
        async with AsyncSessionLocal() as db:
            conversation = await db.get(AiConversation, conversation_id)
            if conversation is None:
                return
            conversation.status = status
            conversation.ended_at = utcnow_naive()
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        _log.warning("结束 AI 对话会话失败 conversation=%s: %s", conversation_id, exc)


# ============================================================
#  数据库读写：REST 接口使用（异常向上抛）
# ============================================================
async def get_or_create_active_conversation(db: AsyncSession, user_id: int) -> AiConversation:
    """取用户最近的 ACTIVE 会话，没有则新建（文字版自我教练使用）。"""
    conversation = await db.scalar(
        select(AiConversation)
        .where(AiConversation.user_id == user_id, AiConversation.status == "ACTIVE")
        .order_by(AiConversation.created_at.desc())
        .limit(1)
    )
    if conversation is not None:
        return conversation
    conversation = AiConversation(
        user_id=user_id, title="自我教练对话", status="ACTIVE", message_count=0,
    )
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    return conversation


async def append_ai_message(
    db: AsyncSession,
    conversation_id: int,
    role: str,
    content: str,
    emotion: dict | None = None,
    *,
    turn_index: int = 0,
    stage: str | None = None,
    goal_clear: bool | None = None,
    action_ready: bool | None = None,
    should_summarize: bool | None = None,
    summary_reason: str | None = None,
    risk_level: str | None = None,
    risk_score: int | None = None,
    fusion_emotion: str | None = None,
    fusion_confidence: float | None = None,
    timings: dict | None = None,
) -> AiMessage:
    """写入一条对话消息并更新计数；首条用户消息作为标题。"""
    message = AiMessage(
        conversation_id=conversation_id,
        role=role,
        content=_truncate(content),
        emotion=emotion,
        turn_index=max(0, int(turn_index or 0)),
        stage=stage,
        goal_clear=goal_clear,
        action_ready=action_ready,
        should_summarize=should_summarize,
        summary_reason=summary_reason,
        risk_level=risk_level,
        risk_score=risk_score,
        fusion_emotion=fusion_emotion,
        fusion_confidence=fusion_confidence,
        timings=timings,
    )
    db.add(message)
    conversation = await db.get(AiConversation, conversation_id)
    if conversation is not None:
        if role == "USER" and (conversation.message_count or 0) == 0:
            conversation.title = content.strip()[:30] or "自我教练对话"
        if turn_index:
            conversation.turn_count = max(conversation.turn_count or 0, int(turn_index))
        if stage:
            conversation.final_stage = stage
        if risk_level:
            conversation.max_risk_level = max_risk_level(
                [conversation.max_risk_level, risk_level]
            )
    await db.execute(
        update(AiConversation)
        .where(AiConversation.id == conversation_id)
        .values(message_count=AiConversation.message_count + 1)
    )
    await db.commit()
    await db.refresh(message)
    return message


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
            "turn_count": r.turn_count or 0,
            "final_stage": r.final_stage,
            "max_risk_level": r.max_risk_level,
            "summary": r.summary,
            "journal_id": r.journal_id,
            "created_at": to_iso(r.created_at),
            "updated_at": to_iso(r.updated_at),
        }
        for r in rows
    ]
    return items, total


async def get_ai_conversation_or_404(
    db: AsyncSession, user_id: int, conversation_id: int
) -> AiConversation:
    conversation = await db.scalar(
        select(AiConversation).where(
            AiConversation.id == conversation_id,
            AiConversation.user_id == user_id,
        )
    )
    if conversation is None:
        raise AppError(404, "NOT_FOUND", "对话记录不存在")
    return conversation


async def list_ai_messages(db: AsyncSession, conversation_id: int) -> list[dict]:
    rows = list(
        await db.scalars(
            select(AiMessage)
            .where(AiMessage.conversation_id == conversation_id)
            .order_by(AiMessage.turn_index.asc(), AiMessage.created_at.asc(), AiMessage.id.asc())
        )
    )
    return [
        {
            "id": r.id,
            "role": r.role,
            "content": r.content,
            "emotion": r.emotion,
            "turn_index": r.turn_index or 0,
            "stage": r.stage,
            "goal_clear": r.goal_clear,
            "action_ready": r.action_ready,
            "should_summarize": r.should_summarize,
            "risk_level": r.risk_level,
            "fusion_emotion": r.fusion_emotion,
            "fusion_confidence": r.fusion_confidence,
            "timings": r.timings,
            "created_at": to_iso(r.created_at),
        }
        for r in rows
    ]


# ============================================================
#  会话总结：草稿生成与确认（成长记录闭环）
# ============================================================
def _derive_mood(messages: list[dict]) -> str:
    """从最近一轮的情绪快照推导日记情绪；缺失时按关键词兜底。"""
    for m in reversed(messages):
        if m["role"] != "USER":
            continue
        fusion = m.get("fusion_emotion")
        if fusion:
            mood = _EMOTION_TO_MOOD.get(str(fusion).strip().lower())
            if mood:
                return mood
        snapshot = m.get("emotion") or {}
        cn = snapshot.get("fusion_emotion_cn") if isinstance(snapshot, Mapping) else None
        if cn and str(cn) in MOOD_MAP:
            return MOOD_MAP[str(cn)]
    for m in messages:
        if m["role"] != "USER":
            continue
        for keyword, mood in KEYWORD_MOOD:
            if keyword in m["content"]:
                return mood
    return "OTHER"


def _closing_text(messages: list[dict]) -> str | None:
    """取最后一轮"满足收束条件"的 AI 回复作为总结原文。"""
    for message in reversed(messages):
        if message["role"] == "ASSISTANT" and message.get("should_summarize"):
            return str(message.get("content") or "")
    return None


def build_draft_from_messages(
    *,
    title: str,
    final_stage: str | None,
    turn_count: int,
    messages: list[dict],
    already_confirmed: bool,
) -> dict[str, Any]:
    """由已落库消息构造阶段总结草稿（纯函数，便于单测）。

    先取会话主题，再优先使用收束轮的 AI 原文，最后回退到阶段模板。
    """
    users = [m["content"].strip() for m in messages if m["role"] == "USER" and m["content"].strip()]
    theme = users[0] if users else title
    return {
        "draft": build_summary_draft(
            theme=theme,
            final_stage=final_stage,
            closing_text=_closing_text(messages),
        ),
        "mood_type": _derive_mood(messages),
        "final_stage": final_stage,
        "turn_count": turn_count,
        "already_confirmed": already_confirmed,
    }


def _heuristic_summary(messages: list[dict], title: str) -> str:
    users = [
        m["content"].strip() for m in messages if m["role"] == "USER" and m["content"].strip()
    ]
    if not users:
        return (title.strip() or "自我教练对话")[:120]
    if len(users) == 1:
        return f"这次自我教练，我提到了：{users[0]}"[:120]
    return f"这次自我教练，我聊到了「{users[0]}」等话题，AI 教练陪我一起梳理了感受。"[:120]


async def _generate_summary(messages: list[dict], title: str) -> str:
    """用 DeepSeek 总结对话为一句情绪日记；失败回退启发式摘要。"""
    transcript = "\n".join(
        ("我：" if m["role"] == "USER" else "教练：") + m["content"]
        for m in messages
    )[-3000:]
    api_key = settings.deepseek_api_key
    if not api_key or not transcript.strip():
        return _heuristic_summary(messages, title)
    import asyncio
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
                "max_tokens": 160,
            },
            timeout=45,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()

    last_exc: Exception | None = None
    for attempt in range(1, 3):
        try:
            summary = await asyncio.get_event_loop().run_in_executor(None, _call)
            return summary[:MAX_SUMMARY_LENGTH] if summary else _heuristic_summary(messages, title)
        except Exception as e:  # noqa: BLE001
            last_exc = e
            if attempt < 2:
                # 必须用 await：同步 sleep 会把整个事件循环卡住 0.6 秒，
                # 期间同一进程的其它会话与 HTTP 请求都要等
                await asyncio.sleep(0.6)
    _log.warning("AI 对话总结失败，使用兜底摘要: %s", last_exc)
    return _heuristic_summary(messages, title)


async def generate_summary_draft(
    db: AsyncSession,
    user,
    conversation_id: int,
) -> dict:
    """生成情绪日记草稿（不落库，由前端确认后提交）。

    返回字段在保留 ``mood_type`` / ``content`` / ``source`` / ``conversation_id``
    契约的同时，补充阶段与轮次信息，便于前端展示"这次聊到了哪个阶段"。
    """
    conversation = await get_ai_conversation_or_404(db, user.id, conversation_id)
    messages = await list_ai_messages(db, conversation.id)

    deterministic = build_draft_from_messages(
        title=conversation.title,
        final_stage=conversation.final_stage,
        turn_count=conversation.turn_count or 0,
        messages=messages,
        already_confirmed=conversation.summary_confirmed_at is not None,
    )
    summary = await _generate_summary(messages, conversation.title)
    return {
        "mood_type": deterministic["mood_type"],
        "content": summary[:MAX_SUMMARY_LENGTH],
        "source": "SELF_COACHING",
        "conversation_id": conversation.id,
        "final_stage": deterministic["final_stage"],
        "turn_count": deterministic["turn_count"],
        "already_confirmed": deterministic["already_confirmed"],
        "stage_draft": deterministic["draft"],
    }


async def confirm_summary(
    db: AsyncSession,
    conversation: AiConversation,
    *,
    content: str,
    mood_type: str,
) -> EmotionJournal:
    """确认阶段总结：写入会话，并生成或更新关联的情绪日记。

    - 会话已有总结时更新，保证重复确认是幂等的；
    - 同一会话只产生一篇日记（``source_conversation_id`` 唯一约束）。
    """
    from app.services.emotion_journal_service import pick_feedback

    clean = content.strip()[:MAX_SUMMARY_LENGTH]
    feedback = await pick_feedback(db, mood_type)

    journal = await db.scalar(
        select(EmotionJournal)
        .where(EmotionJournal.source_conversation_id == conversation.id)
        .limit(1)
    )
    if journal is None:
        journal = EmotionJournal(
            user_id=int(conversation.user_id) if conversation.user_id else 0,
            mood_type=mood_type,
            content=clean,
            feedback=feedback,
            source="SELF_COACHING",
            source_conversation_id=int(conversation.id),
        )
        db.add(journal)
        await db.flush()
    else:
        journal.mood_type = mood_type
        journal.content = clean
        journal.feedback = feedback

    conversation.summary = clean
    conversation.summary_confirmed_at = utcnow_naive()
    conversation.journal_id = int(journal.id)
    await db.commit()
    await db.refresh(journal)
    return journal


async def delete_conversation(db: AsyncSession, conversation: AiConversation) -> None:
    """删除本人的一次对话记录。

    消息随外键级联删除（``ai_messages`` 的 ``ON DELETE CASCADE``）；
    已确认的情绪日记属于用户自己的成长记录，保留但解除与会话的关联。
    """
    journal = await db.scalar(
        select(EmotionJournal)
        .where(EmotionJournal.source_conversation_id == conversation.id)
        .limit(1)
    )
    if journal is not None:
        journal.source_conversation_id = None
    await db.delete(conversation)
    await db.commit()


__all__ = [
    "DEFAULT_POLICY_VERSION",
    "MAX_CONTENT_LENGTH",
    "MAX_SUMMARY_LENGTH",
    "SOURCE_VIDEO_CALL",
    "TurnSnapshot",
    "append_ai_message",
    "build_draft_from_messages",
    "build_summary_draft",
    "confirm_summary",
    "delete_conversation",
    "end_ai_conversation",
    "end_session_safely",
    "generate_summary_draft",
    "get_ai_conversation_or_404",
    "get_or_create_active_conversation",
    "list_ai_conversations",
    "list_ai_messages",
    "max_risk_level",
    "normalize_consent",
    "record_consent_change_safely",
    "record_message_safely",
    "record_turn_safely",
    "start_session_safely",
    "suggest_mood",
]
