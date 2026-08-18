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
import time
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

    # 中断标志
    interrupted: bool = False

    # 当前 LLM 生成任务（用于取消）
    llm_cancelled: bool = False

    # 情绪上下文（从面部识别结果更新）
    emotion_context: dict[str, Any] = field(default_factory=dict)

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


def remove_session(sid: str) -> None:
    """移除会话。"""
    if sid in _sessions:
        _sessions[sid].clear()
        del _sessions[sid]


def has_session(sid: str) -> bool:
    """检查会话是否存在。"""
    return sid in _sessions
