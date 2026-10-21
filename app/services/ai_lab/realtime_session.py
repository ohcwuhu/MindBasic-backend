"""
视频通话会话管理
================
【职责】
  - 管理每个客户端的视频通话会话状态
  - 累积音频分片，供 ASR 批量推理
  - 缓存最新视频帧，供 VLM 视觉理解
  - 维护对话历史，供 LLM 上下文引用
  - 管理中断状态（用户说话时停止 TTS/LLM）

【会话状态机】
  idle → listening → thinking → speaking → listening ...
         ↑                                    ↓
         └────── interrupt ───────────────────┘
"""
from __future__ import annotations

import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

_log = logging.getLogger("realtime-session")

# 会话状态
STATE_IDLE = "idle"
STATE_LISTENING = "listening"
STATE_THINKING = "thinking"
STATE_SPEAKING = "speaking"

# 对话历史最大轮数
MAX_HISTORY_TURNS = 12

# 最新视频帧缓存（per-session）
MAX_FRAME_AGE_SECONDS = 10  # 超过10秒的帧视为过期

# 长时间"没有任何语音活动"后的兜底收尾（秒）。
# 前端在 75s 无语音时会自己结束通话；这里是服务端兜底，
# 覆盖"标签页被挂起，前端定时器被浏览器节流"这类前端管不到的情况。
# 只统计语音相关活动（收到音频分片 / 用户说完一轮 / 打断 / 改授权），
# 摄像头帧不算——一直开着摄像头但始终不说话，同样应该收尾。
IDLE_TIMEOUT_SECONDS = int(os.environ.get("VC_IDLE_TIMEOUT_SECONDS", "300"))


@dataclass
class VideoCallSession:
    """单个客户端的视频通话会话。"""

    sid: str

    # 会话状态
    state: str = STATE_IDLE

    # 音频分片累积缓冲（base64 字符串列表）
    # 注意：新方案中 chunk 可能带 idx（前端按 64KB 切分的完整文件切片）
    audio_chunks: list[str] = field(default_factory=list)
    # 录音开始时间戳
    audio_start_ts: float = 0.0

    # 最新视频帧（base64 JPEG）
    latest_frame: str = ""
    latest_frame_ts: float = 0.0

    # 对话历史（[{role, content}, ...]）
    chat_history: list[dict[str, str]] = field(default_factory=list)

    # VLM 上次的视觉描述（供 LLM 上下文引用）
    last_visual_description: str = ""

    # Dify 会话 ID（多轮上下文由 Dify 维护）
    dify_conversation_id: str = ""

    # 数据库中的会话 ID（ai_conversations.id，留痕失败时为 None）
    conversation_id: int | None = None

    # 用户授权的模态范围（vc_start 带入；缺省为全开，兼容旧客户端）
    #   camera=False    → 不使用摄像头画面（前端不上传帧）
    #   multimodal=False → 语音语调与面部不参与情绪融合，只按谈话内容判断
    consent_camera: bool = True
    consent_multimodal: bool = True

    # 已完成的对话轮次（用于阶段判定与留痕序号）
    turn_index: int = 0

    # 最近一轮的阶段判定结果（供 Dify 入参与留痕使用）
    stage_context: dict[str, Any] = field(default_factory=dict)

    # 中断标志
    interrupted: bool = False

    # 当前 LLM 生成任务（用于取消）
    llm_cancelled: bool = False

    # 情绪上下文（从面部识别结果更新）
    emotion_context: dict[str, Any] = field(default_factory=dict)

    # 最近一次"语音活动"的时间戳（进入通话、收到音频、说完一轮、打断、改授权）
    last_activity_at: float = field(default_factory=time.time)
    # 空闲超时是否已经通知过前端（避免重复 emit / 重复归档）
    idle_timeout_notified: bool = False
    # 是否已进入收尾；在途管线仍持有该对象，但同一 sid 的新通话应创建新对象
    closing: bool = False

    def touch(self) -> None:
        """标记一次语音活动：重置空闲计时，允许下一次超时再次触发。"""
        self.last_activity_at = time.time()
        self.idle_timeout_notified = False

    def add_audio_chunk(self, chunk_b64: str) -> None:
        """添加音频分片到缓冲（保持前端发送的先后顺序）。"""
        self.audio_chunks.append(chunk_b64)

    def get_accumulated_audio(self) -> list[str]:
        """获取并清空音频缓冲。"""
        chunks = self.audio_chunks
        self.audio_chunks = []
        return chunks

    def get_merged_audio_bytes(self) -> bytes:
        """将累积的 base64 分片按顺序拼接为完整二进制数据并清空缓冲。"""
        import base64 as _b64
        chunks = self.audio_chunks
        self.audio_chunks = []
        if not chunks:
            return b""
        try:
            return b"".join(_b64.b64decode(c) for c in chunks if c)
        except Exception as e:
            _log.error("[Session] 音频分片解码失败: %s", e)
            return b""

    def update_frame(self, frame_b64: str) -> None:
        """更新最新视频帧。"""
        self.latest_frame = frame_b64
        self.latest_frame_ts = time.time()

    def get_valid_frame(self) -> str | None:
        """获取有效的最新视频帧（未过期的）。"""
        if not self.latest_frame:
            return None
        if time.time() - self.latest_frame_ts > MAX_FRAME_AGE_SECONDS:
            return None
        return self.latest_frame

    def add_chat_message(self, role: str, content: str) -> None:
        """添加对话消息到历史。"""
        self.chat_history.append({"role": role, "content": content})
        # 控制历史长度
        if len(self.chat_history) > MAX_HISTORY_TURNS * 2:
            self.chat_history = self.chat_history[-(MAX_HISTORY_TURNS * 2):]

    def get_chat_history(self) -> list[dict[str, str]]:
        """获取对话历史（过滤空消息）。"""
        return [
            {"role": m["role"], "content": m["content"]}
            for m in self.chat_history
            if m.get("content", "").strip()
        ]

    def reset_interrupt(self) -> None:
        """重置中断状态。"""
        self.interrupted = False
        self.llm_cancelled = False

    def clear(self) -> None:
        """清理会话资源。"""
        self.audio_chunks.clear()
        self.chat_history.clear()
        self.latest_frame = ""
        self.last_visual_description = ""
        self.dify_conversation_id = ""
        self.conversation_id = None
        self.turn_index = 0
        self.stage_context.clear()
        self.emotion_context.clear()
        self.state = STATE_IDLE
        self.interrupted = False
        self.llm_cancelled = False


# ============================================================
#  全局会话管理（per-sid）
# ============================================================
_sessions: dict[str, VideoCallSession] = {}


def get_session(sid: str) -> VideoCallSession:
    """获取或创建会话。"""
    if sid not in _sessions:
        _sessions[sid] = VideoCallSession(sid=sid)
    return _sessions[sid]


def remove_session(sid: str, expected: VideoCallSession | None = None) -> None:
    """从会话表移除该 sid（不就地清空对象）。

    这里刻意不调用 ``clear()``：实时管线在 ``_run_video_call_pipeline_from_file``
    里持有同一个对象引用，一旦断线（或用户点结束通话）时把它清空，本轮还没落库的
    ``conversation_id`` / ``turn_index`` / 对话历史就被抹掉，表现为
    "断线后这一轮的助手回复没入库、留痕丢失会话关联"（日志里 ``conversation=None``）。

    只摘掉字典引用即可：管线跑完后对象自然被回收，下一通电话会拿到新对象。
    """
    current = _sessions.get(sid)
    if current is None:
        return
    if expected is not None and current is not expected:
        return
    _sessions.pop(sid, None)


def has_session(sid: str) -> bool:
    """检查会话是否存在。"""
    return sid in _sessions


# ============================================================
#  空闲看门狗：长时间无语音 → 结束通话
# ============================================================
# 真正的收尾动作（emit 事件、归档会话）要碰 socketio，所以在 socket_events
# 注册回调，避免本模块反向依赖 app.main（会形成循环导入）。
_idle_timeout_handler: Callable[[str, int], Awaitable[None]] | None = None


def set_idle_timeout_handler(handler: Callable[[str, int], Awaitable[None]] | None) -> None:
    """注册空闲超时的收尾回调（sid, 空闲秒数）。"""
    global _idle_timeout_handler
    _idle_timeout_handler = handler


def idle_calls(
    *,
    max_idle_seconds: int = IDLE_TIMEOUT_SECONDS,
    now: float | None = None,
) -> list[tuple[str, int]]:
    """列出空闲超时且仍在通话中的会话，返回 ``[(sid, 空闲秒数), ...]``。

    纯查询，不改状态——便于单测；实际收尾走 :func:`sweep_idle_calls`。
    """
    current = time.time() if now is None else now
    result: list[tuple[str, int]] = []
    for sid, session in _sessions.items():
        if session.state == STATE_IDLE or session.idle_timeout_notified:
            continue
        idle_seconds = int(current - session.last_activity_at)
        if idle_seconds >= max_idle_seconds:
            result.append((sid, idle_seconds))
    return result


async def sweep_idle_calls(
    *,
    max_idle_seconds: int = IDLE_TIMEOUT_SECONDS,
) -> list[str]:
    """结束长时间没有语音活动的会话，返回被收尾的 sid 列表。

    由定时任务调用（见 app/core/scheduler.py）。任何单个会话出错都不影响其他会话。
    """
    handler = _idle_timeout_handler
    expired = idle_calls(max_idle_seconds=max_idle_seconds)
    if not expired or handler is None:
        if expired:
            _log.warning("[Session] 有 %d 个空闲会话，但未注册收尾回调，跳过", len(expired))
        return []

    ended: list[str] = []
    for sid, idle_seconds in expired:
        session = _sessions.get(sid)
        if session is None:
            continue
        # 先打标记再执行收尾：即便收尾过程抛异常，也不会每次扫描重复触发
        session.idle_timeout_notified = True
        try:
            await handler(sid, idle_seconds)
            ended.append(sid)
        except Exception as exc:  # noqa: BLE001 - 单个会话失败不影响其他会话
            _log.warning("[Session] 空闲收尾失败 sid=%s: %s", sid, exc)
    return ended
