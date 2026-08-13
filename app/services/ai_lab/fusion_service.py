"""
多模态情感融合引擎
==================
【职责】
  1. 从 FacialBuffer 提取录音时段内的面部帧序列
  2. 聚合面部序列为代表性情感分布（概率平均 + 稳定性 + 趋势）
  3. 动态权重计算（基于各来源置信度与质量指标）
  4. 加权概率平均融合，输出最终情绪

【输入】三个情感来源 + 面部时序数据
【输出】融合后的最终情绪 + 面部聚合结果 + 权重信息

统一标签体系（7 类）：
  happy, sad, angry, surprised, fearful, disgusted, neutral
"""
from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any

from app.services.ai_lab import facial_buffer

_log = logging.getLogger("fusion-service")

# ============================================================
#  常量
# ============================================================
UNIFIED_LABELS = ["happy", "sad", "angry", "surprised", "fearful", "disgusted", "neutral"]

EMOTION_CN = {
    "happy": "开心", "sad": "悲伤", "angry": "愤怒", "surprised": "惊讶",
    "fearful": "恐惧", "disgusted": "厌恶", "neutral": "中性",
}

# 基础权重（经验值）
_BASE_W_TEXT = 0.40    # 语义内容是情感最直接的载体
_BASE_W_VOICE = 0.35   # 韵律承载情感意图，但受说话风格影响
_BASE_W_FACIAL = 0.25  # 视觉是补充信号，受遮挡/光线影响

# 面部时序下采样的最大关键点数
_MAX_SEQUENCE_POINTS = 10


# ============================================================
#  主入口
# ============================================================
def fuse(
    text_result: dict[str, Any],
    voice_result: dict[str, Any],
    sv_emo_result: dict[str, Any],
    sid: str,
    record_start_ts: float,
    record_end_ts: float,
) -> dict[str, Any]:
    """
    多模态融合主入口。

    参数：
        text_result:   文本情感分析结果（text_emotion_service.analyze 输出）
        voice_result:  语调情感分析结果（emotion2vec_service.analyze 输出）
        sv_emo_result: SenseVoice emo 辅助信号 {"emotion": "happy"/"sad"/...}
        sid:           SocketIO 客户端 ID（用于查询面部缓冲）
        record_start_ts: 录音开始时间戳（秒，epoch）
        record_end_ts:   录音结束时间戳（秒，epoch）

    返回：
        {
            "facial_emotion": {...},   # 面部聚合结果
            "fusion": {...},           # 融合结果
        }
    """
    # 1) 提取面部时序窗口
    facial_frames: list[dict] = []
    if sid and record_start_ts and record_end_ts:
        facial_frames = facial_buffer.get_window(sid, record_start_ts, record_end_ts)

    # 2) 聚合面部序列
    facial_result = _aggregate_facial(facial_frames, record_start_ts, record_end_ts)

    # 3) 动态权重计算
    weights, adjustments = _compute_dynamic_weights(
        text_result, voice_result, facial_result, sv_emo_result
    )

    # 4) 加权概率融合
    text_probs = text_result.get("probabilities", {})
    voice_probs = voice_result.get("probabilities", {})
    facial_probs = facial_result.get("emotion_distribution", {})

    fused: dict[str, float] = {}
    for emo in UNIFIED_LABELS:
        fused[emo] = (
            weights["text"] * text_probs.get(emo, 0.0)
            + weights["voice"] * voice_probs.get(emo, 0.0)
            + weights["facial"] * facial_probs.get(emo, 0.0)
        )

    final_emotion = max(fused, key=fused.get)
    overall_confidence = fused[final_emotion]

    _log.info(
        "[Fusion] 最终情绪=%s(%.3f) | 权重 text=%.2f voice=%.2f facial=%.2f | 面部帧数=%d",
        final_emotion, overall_confidence,
        weights["text"], weights["voice"], weights["facial"],
        facial_result["frame_count"],
    )

    return {
        "facial_emotion": facial_result,
        "fusion": {
            "final_emotion": final_emotion,
            "final_emotion_cn": EMOTION_CN[final_emotion],
            "overall_confidence": round(overall_confidence, 3),
            "probabilities": {k: round(v, 3) for k, v in fused.items()},
            "weights_used": weights,
            "weight_adjustments": adjustments,
        },
    }


# ============================================================
#  面部序列聚合
# ============================================================
def _aggregate_facial(
    facial_frames: list[dict],
    t_start: float,
    t_end: float,
) -> dict[str, Any]:
    """
    将录音窗口内的面部帧序列聚合为代表性情感分布。

    聚合策略：
      - 概率平均：每类情绪取所有帧 raw_probs 的均值
      - 稳定性：1 - 归一化熵（越高越稳定）
      - 趋势：对 dominant emotion 概率做线性回归斜率判定
      - 时序下采样：帧数 > 10 时等间隔取 10 个关键点
    """
    # 过滤无效帧（无 raw_probs 的帧不参与）
    valid = [f for f in facial_frames if f.get("raw_probs")]
    if not valid:
        return _empty_facial_result(t_start, t_end)

    # 1) 概率平均
    avg_probs: dict[str, float] = {}
    for emo in UNIFIED_LABELS:
        values = [f["raw_probs"].get(emo, 0.0) for f in valid]
        avg_probs[emo] = sum(values) / len(values)

    # 归一化
    total = sum(avg_probs.values())
    if total > 0:
        avg_probs = {k: v / total for k, v in avg_probs.items()}
    else:
        avg_probs = {k: 1.0 / len(UNIFIED_LABELS) for k in UNIFIED_LABELS}

    dominant = max(avg_probs, key=avg_probs.get)
    confidence = avg_probs[dominant]

    # 2) 稳定性指标：1 - 归一化熵
    entropy = -sum(p * math.log(p + 1e-9) for p in avg_probs.values() if p > 0)
    max_entropy = math.log(len(UNIFIED_LABELS))
    stability = 1.0 - (entropy / max_entropy) if max_entropy > 0 else 0.0
    stability = max(0.0, min(1.0, stability))

    # 3) 趋势检测：对 dominant emotion 的概率做线性回归
    timestamps = [f["ts"] - t_start for f in valid]  # 相对时间 0..N
    dom_probs = [f["raw_probs"].get(dominant, 0.0) for f in valid]
    trend = _detect_trend(timestamps, dom_probs)

    # 4) 时序下采样：等间隔取最多 10 个关键点
    if len(valid) > _MAX_SEQUENCE_POINTS:
        step = len(valid) / _MAX_SEQUENCE_POINTS
        sampled = [valid[int(i * step)] for i in range(_MAX_SEQUENCE_POINTS)]
    else:
        sampled = valid

    sequence_summary = [
        {
            "t": round(f["ts"] - t_start, 2),
            "emotion": max(f["raw_probs"], key=f["raw_probs"].get),
            "confidence": round(max(f["raw_probs"].values()), 3),
        }
        for f in sampled
    ]

    return {
        "dominant_emotion": dominant,
        "dominant_emotion_cn": EMOTION_CN[dominant],
        "confidence": round(confidence, 3),
        "stability": round(stability, 3),
        "trend": trend,
        "frame_count": len(valid),
        "time_window": {
            "start": _format_ts(t_start),
            "end": _format_ts(t_end),
            "duration_seconds": round(t_end - t_start, 2) if t_end > t_start else 0.0,
        },
        "emotion_distribution": {k: round(v, 3) for k, v in avg_probs.items()},
        "sequence_summary": sequence_summary,
    }


def _detect_trend(timestamps: list[float], values: list[float]) -> str:
    """对时序概率值做线性回归，判定趋势。"""
    n = len(timestamps)
    if n < 2:
        return "stable"

    # 简单线性回归斜率：slope = cov(t, v) / var(t)
    mean_t = sum(timestamps) / n
    mean_v = sum(values) / n

    num = sum((timestamps[i] - mean_t) * (values[i] - mean_v) for i in range(n))
    den = sum((timestamps[i] - mean_t) ** 2 for i in range(n))

    if den == 0:
        return "stable"

    slope = num / den

    # 斜率阈值（相对时间尺度下经验值）
    if slope > 0.02:
        return "rising"
    elif slope < -0.02:
        return "falling"
    else:
        return "stable"


def _format_ts(ts: float) -> str:
    """将 epoch 时间戳格式化为 HH:MM:SS.mmm。"""
    try:
        return datetime.fromtimestamp(ts).strftime("%H:%M:%S.") + f"{int(ts * 1000) % 1000:03d}"
    except Exception:
        return str(ts)


def _empty_facial_result(t_start: float, t_end: float) -> dict[str, Any]:
    """无面部帧时的空结果。"""
    return {
        "dominant_emotion": "neutral",
        "dominant_emotion_cn": "中性",
        "confidence": 0.0,
        "stability": 0.0,
        "trend": "no_data",
        "frame_count": 0,
        "time_window": {
            "start": _format_ts(t_start) if t_start else "",
            "end": _format_ts(t_end) if t_end else "",
            "duration_seconds": round(t_end - t_start, 2) if t_end > t_start else 0.0,
        },
        "emotion_distribution": {label: 0.0 for label in UNIFIED_LABELS},
        "sequence_summary": [],
    }


# ============================================================
#  动态权重计算
# ============================================================
def _compute_dynamic_weights(
    text_result: dict[str, Any],
    voice_result: dict[str, Any],
    facial_result: dict[str, Any],
    sv_emo_result: dict[str, Any],
) -> tuple[dict[str, float], list[str]]:
    """
    根据各来源的置信度和质量指标动态调整权重。

    调整规则：
      1. 无面部帧 → 面部权重归零，重分配到 text/voice
      2. 面部稳定性低 → 面部权重减半
      3. 文本过短 → 文本权重减半
      4. SenseVoice emo 与 emotion2vec+ 一致 → 语调权重 +20%
      5. 语调置信度过低 → 语调权重 -30%
      6. 文本置信度过低 → 文本权重 -30%
    """
    w_text = _BASE_W_TEXT
    w_voice = _BASE_W_VOICE
    w_facial = _BASE_W_FACIAL
    adjustments: list[str] = []

    # 规则1: 无面部帧
    if facial_result["frame_count"] == 0:
        reduction = w_facial
        w_facial = 0.0
        w_text += reduction * 0.6
        w_voice += reduction * 0.4
        adjustments.append("no_facial_frames: facial=0")
    # 规则2: 面部稳定性低
    elif facial_result["stability"] < 0.4:
        reduction = w_facial * 0.5
        w_facial -= reduction
        w_text += reduction * 0.6
        w_voice += reduction * 0.4
        adjustments.append(
            f"facial_stability_low({facial_result['stability']:.2f}): facial-50%"
        )

    # 规则3: 文本过短
    text_str = text_result.get("text", "")
    if len(text_str) < 5:
        reduction = w_text * 0.5
        w_text -= reduction
        w_voice += reduction * 0.7
        w_facial += reduction * 0.3
        adjustments.append(f"text_too_short({len(text_str)}chars): text-50%")

    # 规则4: SenseVoice emo 与 emotion2vec+ 一致（且非 neutral）
    sv_emo = sv_emo_result.get("emotion", "neutral")
    voice_emo = voice_result.get("emotion", "neutral")
    if sv_emo and voice_emo and sv_emo == voice_emo and sv_emo != "neutral":
        boost = w_voice * 0.20
        w_voice += boost
        w_text -= boost * 0.5
        w_facial -= boost * 0.5
        adjustments.append(f"sv_cross_check_agree({sv_emo}): voice+20%")

    # 规则5: 语调置信度过低
    voice_conf = voice_result.get("confidence", 0.0)
    if voice_conf < 0.4:
        reduction = w_voice * 0.30
        w_voice -= reduction
        w_text += reduction * 0.6
        w_facial += reduction * 0.4
        adjustments.append(f"voice_confidence_low({voice_conf:.2f}): voice-30%")

    # 规则6: 文本置信度过低
    text_conf = text_result.get("confidence", 0.0)
    if text_conf < 0.3:
        reduction = w_text * 0.30
        w_text -= reduction
        w_voice += reduction * 0.6
        w_facial += reduction * 0.4
        adjustments.append(f"text_confidence_low({text_conf:.2f}): text-30%")

    # 归一化（防止浮点累积误差 + 确保权重非负）
    w_text = max(0.0, w_text)
    w_voice = max(0.0, w_voice)
    w_facial = max(0.0, w_facial)
    total = w_text + w_voice + w_facial
    if total > 0:
        w_text, w_voice, w_facial = w_text / total, w_voice / total, w_facial / total
    else:
        # 极端情况：全部归零，回退到基础权重
        w_text, w_voice, w_facial = _BASE_W_TEXT, _BASE_W_VOICE, _BASE_W_FACIAL
        total = w_text + w_voice + w_facial
        w_text, w_voice, w_facial = w_text / total, w_voice / total, w_facial / total

    return (
        {
            "text": round(w_text, 3),
            "voice": round(w_voice, 3),
            "facial": round(w_facial, 3),
        },
        adjustments,
    )
