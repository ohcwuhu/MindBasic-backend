"""
面部情绪时序缓冲（per-sid 滚动窗口）
====================================
【用途】
  SocketIO 层 DeepFace 每帧结果写入此缓冲，
  HTTP 层 /api/analyze_audio 在语音分析完成后，
  按 [record_start_ts, record_end_ts] 时间窗口提取面部帧进行融合。

【设计】
  - 每个 SocketIO sid 维护独立 deque，disconnect 时清理
  - 滚动窗口保留最近 ~80 秒数据（@2.5fps ≈ 200 帧）
  - 线程安全（SocketIO async + HTTP thread pool 并发访问）
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

# ============================================================
#  全局缓冲（per-sid）
# ============================================================
_lock = threading.Lock()
_buffers: dict[str, deque] = {}
_MAX_FRAMES = 200  # 每个客户端最多保留 200 帧（~80秒 @2.5fps）


def init_client(sid: str) -> None:
    """SocketIO connect 时调用：初始化客户端缓冲。"""
    with _lock:
        _buffers[sid] = deque(maxlen=_MAX_FRAMES)


def remove_client(sid: str) -> None:
    """SocketIO disconnect 时调用：清理客户端缓冲。"""
    with _lock:
        _buffers.pop(sid, None)


def append_frame(
    sid: str,
    emotions: dict,
    score: int,
    raw_probs: dict,
    server_ts: float | None = None,
) -> None:
    """
    DeepFace 每帧结果写入缓冲。

    参数：
        sid: SocketIO 客户端 ID
        emotions: {"happy": 2, "neutral": 1, ...}  人脸情绪计数
        score: 平均投入分数 0-100
        raw_probs: {"happy": 0.6, "neutral": 0.3, ...}  7 类概率分布
        server_ts: 服务端时间戳，None 则取当前时间
    """
    ts = server_ts if server_ts is not None else time.time()
    with _lock:
        if sid not in _buffers:
            _buffers[sid] = deque(maxlen=_MAX_FRAMES)
        _buffers[sid].append({
            "ts": ts,
            "emotions": emotions,
            "score": score,
            "raw_probs": raw_probs,
        })


def get_window(sid: str, t_start: float, t_end: float) -> list[dict]:
    """
    提取 [t_start, t_end] 时间窗口内的面部帧。

    参数：
        t_start: 录音开始时间戳（秒，epoch）
        t_end:   录音结束时间戳（秒，epoch）
    返回：
        [{"ts": 12.3, "emotions": {...}, "score": 55, "raw_probs": {...}}, ...]
    """
    with _lock:
        buf = _buffers.get(sid, deque())
        if not buf:
            return []
        return [f for f in buf if t_start <= f["ts"] <= t_end]


def get_status() -> dict[str, int]:
    """返回每个 sid 的缓冲帧数（调试用）。"""
    with _lock:
        return {sid: len(buf) for sid, buf in _buffers.items()}
