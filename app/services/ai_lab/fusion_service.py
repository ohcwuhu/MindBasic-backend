"""
多模态情感融合引擎
==================
【职责】
  1. 从 FacialBuffer 提取录音时段内的面部帧序列
  2. 聚合面部序列为代表性情感分布（概率平均 + 稳定性 + 趋势）
  3. 动态权重计算（基于各来源置信度与质量指标）
  4. 加权概率平均融合，输出最终情绪
  5. 置信度校准（温度缩放，默认关闭）：让"置信度"可用于阈值判断

【输入】三个情感来源 + 面部时序数据
【输出】融合后的最终情绪 + 面部聚合结果 + 权重信息 + 校准信息

统一标签体系（7 类）：
  happy, sad, angry, surprised, fearful, disgusted, neutral

线索冲突检测（conflict）
------------------------
融合结果除了"最终情绪 + 置信度"，还输出各模态是否互相矛盾：
用 JS 散度度量两两模态分布的差异，按模态权重加权得到 ``conflict.score``，
再结合 top1-top2 的置信度间距决定是否需要向用户澄清。
这一步让"线索冲突时通过提问确认"从产品描述变成可复核的算法输出。
"""
from __future__ import annotations

import logging
import math
import os
from datetime import datetime
from typing import Any, Mapping

from app.services.ai_lab import calibration as _calibration
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
#
# 面部基础权重由 0.25 下调到 0.15（可用 FACIAL_WEIGHT_BASE 覆盖，范围 0~0.50）。
# 依据：真实通话留痕中，面部只有「中性/恐惧」两种输出（29 轮里中性 25 轮），
# 且把"我今天非常的高兴"判成恐惧（0.44）、问句判成恐惧（0.87）；加权平均是线性
# 叠加，86% 的中性输出会持续把融合结果往中性拉，高权重下更会直接误导结论。
# 取值依据 experiments/data/fusion_samples.jsonl 实测（40 样本、线上动态权重）：
#   0.25 → 准确率 0.975 / 宏F1 0.9712（基线）
#   0.20 → 0.975 / 0.9712
#   0.15 → 0.975 / 0.9712   ← 无损的最低值
#   0.10 → 0.950 / 0.9576（S32 由 neutral 误判为 sad，故不取）
# 注意：该评测集的面部标签是理想值，无法反映"真实场景面部 86% 输出中性"的偏置，
# 真实收益（减少中性稀释与错误恐惧票）比评测集体现的更大。
# 腾出的权重按原 text:voice = 0.40:0.35 等比例回补，三者仍归一化到 1.0。
_FACIAL_WEIGHT_BASE = float(os.environ.get("FACIAL_WEIGHT_BASE", "0.15") or 0.15)
_FACIAL_WEIGHT_BASE = min(max(_FACIAL_WEIGHT_BASE, 0.0), 0.50)
_REMAIN_W = 1.0 - _FACIAL_WEIGHT_BASE
_BASE_W_TEXT = _REMAIN_W * (0.40 / 0.75)   # 语义内容是情感最直接的载体
_BASE_W_VOICE = _REMAIN_W * (0.35 / 0.75)  # 韵律承载情感意图，但受说话风格影响
_BASE_W_FACIAL = _FACIAL_WEIGHT_BASE       # 视觉是补充信号，受遮挡/光线影响

# 面部时序下采样的最大关键点数
_MAX_SEQUENCE_POINTS = 10

# ── 线索冲突判定阈值 ─────────────────────────────────────────────
#: JS 散度（以 2 为底，取值 0–1）低于该值视为"基本一致"
_CONFLICT_LOW = 0.25
#: 高于该值视为"明显冲突"，无论置信度间距如何都建议澄清
_CONFLICT_HIGH = 0.45
#: 置信度间距（top1 - top2）低于该值说明结论本身不稳
_MARGIN_LOW = 0.15
#: 权重低于该值的模态不参与分歧计算，避免把"几乎没参与的模态"算成冲突
_CONFLICT_MIN_WEIGHT = 0.15


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
    *,
    weights_override: Mapping[str, float] | None = None,
    temperature: float | None = None,
    include_facial: bool = True,
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
        weights_override: 指定固定权重，用于消融实验（单模态 / 固定权重基线）。
            传入时跳过动态权重计算，但融合数学与线上完全一致，
            保证实验结论与线上行为可比。缺省为 None，即线上动态权重。
        temperature: 显式指定校准温度，用于消融与敏感性分析。
            缺省为 None，即按环境变量配置（默认不校准）。
        include_facial: 是否使用面部模态。用户未授权多模态分析时传 False：
            面部帧不进入融合，权重按"无面部"规则重分配，并在
            ``weight_adjustments`` 中留下 ``multimodal_consent_off`` 痕证。

    返回：
        {
            "facial_emotion": {...},   # 面部聚合结果
            "fusion": {...},           # 融合结果（含校准前后置信度、线索冲突）
        }
    """
    # 1) 提取面部时序窗口
    facial_frames: list[dict] = []
    if include_facial and sid and record_start_ts and record_end_ts:
        facial_frames = facial_buffer.get_window(sid, record_start_ts, record_end_ts)

    # 2) 聚合面部序列
    facial_result = _aggregate_facial(facial_frames, record_start_ts, record_end_ts)

    # 3) 权重计算：线上为规则化动态权重，实验可指定固定权重
    if weights_override is None:
        weights, adjustments = _compute_dynamic_weights(
            text_result, voice_result, facial_result, sv_emo_result
        )
    else:
        weights = _normalize_override_weights(weights_override)
        adjustments = ["weights_override: 固定权重基线（消融实验）"]
    if not include_facial:
        adjustments.insert(0, "multimodal_consent_off: facial=0")

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

    # 5) 置信度校准（温度缩放）：只修置信度，不改变 argmax
    #    未校准时 calibrated 与 fused 完全相同，因此默认行为与历史一致。
    cal_sum = sum(fused.values())
    if cal_sum > 0:
        fused = {key: value / cal_sum for key, value in fused.items()}
    calibrated, calibration_meta = _calibration.calibrate(fused, temperature=temperature)

    raw_emotion = max(fused, key=fused.get)
    raw_confidence = fused[raw_emotion]
    final_emotion = max(calibrated, key=calibrated.get)
    overall_confidence = calibrated[final_emotion]
    if final_emotion != raw_emotion:
        # 温度缩放是单调变换，argmax 必须保持不变；出现差异说明校准实现有误
        _log.error(
            "[Fusion] 校准改变了最终情绪判定：raw=%s calibrated=%s（T=%s）",
            raw_emotion, final_emotion, calibration_meta["temperature"],
        )

    # 6) 线索冲突检测：模态之间是否互相矛盾，是否需要向用户澄清
    conflict = _compute_conflict(
        text_probs=text_probs,
        voice_probs=voice_probs,
        facial_probs=facial_probs,
        weights=weights,
        fused=fused,
    )

    _log.info(
        "[Fusion] 最终情绪=%s | 置信度 raw=%.3f -> calibrated=%.3f (T=%.4f, enabled=%s)"
        " | 权重 text=%.2f voice=%.2f facial=%.2f | 面部帧数=%d | 冲突=%s(%.2f)",
        final_emotion, raw_confidence, overall_confidence,
        calibration_meta["temperature"], calibration_meta["enabled"],
        weights["text"], weights["voice"], weights["facial"],
        facial_result["frame_count"],
        conflict["level"], conflict["score"],
    )

    return {
        "facial_emotion": facial_result,
        "fusion": {
            "final_emotion": final_emotion,
            "final_emotion_cn": EMOTION_CN[final_emotion],
            "overall_confidence": round(overall_confidence, 3),
            "raw_confidence": round(raw_confidence, 3),
            "probabilities": {k: round(v, 3) for k, v in calibrated.items()},
            "probabilities_raw": {k: round(v, 3) for k, v in fused.items()},
            "weights_used": weights,
            "weight_adjustments": adjustments,
            "calibration": calibration_meta,
            "conflict": conflict,
        },
    }


# ============================================================
#  线索冲突检测
# ============================================================
def _js_divergence(left: Mapping[str, float], right: Mapping[str, float]) -> float:
    """Jensen–Shannon 散度（以 2 为底），取值 0–1，0 表示两个分布完全一致。"""
    left_total = sum(left.values())
    right_total = sum(right.values())
    if left_total <= 0 or right_total <= 0:
        return 0.0
    p = {label: left.get(label, 0.0) / left_total for label in UNIFIED_LABELS}
    q = {label: right.get(label, 0.0) / right_total for label in UNIFIED_LABELS}
    m = {label: (p[label] + q[label]) / 2 for label in UNIFIED_LABELS}

    divergence = 0.0
    for label in UNIFIED_LABELS:
        if p[label] > 0:
            divergence += 0.5 * p[label] * math.log2(p[label] / m[label])
        if q[label] > 0:
            divergence += 0.5 * q[label] * math.log2(q[label] / m[label])
    return max(0.0, min(1.0, divergence))


def _compute_conflict(
    *,
    text_probs: Mapping[str, float],
    voice_probs: Mapping[str, float],
    facial_probs: Mapping[str, float],
    weights: Mapping[str, float],
    fused: Mapping[str, float],
) -> dict[str, Any]:
    """度量三个模态的相互矛盾程度，并给出是否需要澄清的建议。

    做法：
      1. 只保留权重 ≥ ``_CONFLICT_MIN_WEIGHT`` 且有有效分布的模态；
      2. 两两计算 JS 散度（0–1），按两个模态的权重和加权平均 → ``score``；
      3. 置信度间距 ``margin = top1 - top2``；
      4. ``needs_clarification`` = 明显冲突，或中等冲突且结论不稳。

    输出确定性、可单测，不依赖任何模型调用。
    """
    modalities: dict[str, dict[str, float]] = {}
    for name, probs, weight in (
        ("text", text_probs, weights.get("text", 0.0)),
        ("voice", voice_probs, weights.get("voice", 0.0)),
        ("facial", facial_probs, weights.get("facial", 0.0)),
    ):
        if weight >= _CONFLICT_MIN_WEIGHT and sum(probs.values()) > 0:
            modalities[name] = dict(probs)

    pairwise: dict[str, float] = {}
    names = list(modalities)
    weighted_sum = 0.0
    weight_total = 0.0
    for index, left in enumerate(names):
        for right in names[index + 1:]:
            distance = _js_divergence(modalities[left], modalities[right])
            pairwise[f"{left}-{right}"] = round(distance, 3)
            pair_weight = weights.get(left, 0.0) + weights.get(right, 0.0)
            weighted_sum += distance * pair_weight
            weight_total += pair_weight

    score = round(weighted_sum / weight_total, 3) if weight_total > 0 else 0.0

    ranked = sorted(fused.items(), key=lambda item: item[1], reverse=True)
    margin = round(ranked[0][1] - ranked[1][1], 3) if len(ranked) > 1 else 1.0

    level = "low"
    if score >= _CONFLICT_HIGH:
        level = "high"
    elif score >= _CONFLICT_LOW or margin < _MARGIN_LOW:
        level = "medium"

    needs_clarification = score >= _CONFLICT_HIGH or (
        score >= _CONFLICT_LOW and margin < _MARGIN_LOW
    )

    modal_emotions = {
        name: max(probs, key=probs.get) for name, probs in modalities.items()
    }
    labels = {
        name: EMOTION_CN.get(emotion, emotion) for name, emotion in modal_emotions.items()
    }

    if len(modalities) < 2:
        reason = "可用模态不足，无法判断线索是否冲突"
    elif needs_clarification:
        detail = "、".join(
            f"{pair.split('-')[0]}={labels.get(pair.split('-')[0], '?')}"
            f"/{pair.split('-')[1]}={labels.get(pair.split('-')[1], '?')}"
            for pair, value in sorted(pairwise.items(), key=lambda item: item[1], reverse=True)
            if value >= _CONFLICT_LOW
        )
        reason = f"模态判断不一致（{detail or '置信度偏低'}），建议先澄清再回应"
    else:
        reason = "各模态判断基本一致"

    return {
        "score": score,
        "level": level,
        "needs_clarification": bool(needs_clarification),
        "margin": margin,
        "pairwise": pairwise,
        "modal_emotions": modal_emotions,
        "modal_emotions_cn": labels,
        "reason": reason,
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
def _normalize_override_weights(override: Mapping[str, float]) -> dict[str, float]:
    """归一化实验指定的固定权重。

    负值按 0 处理；全部为 0 时回退到基础权重，避免出现无效融合。
    """
    raw = {
        "text": max(0.0, float(override.get("text", 0.0))),
        "voice": max(0.0, float(override.get("voice", 0.0))),
        "facial": max(0.0, float(override.get("facial", 0.0))),
    }
    total = sum(raw.values())
    if total <= 0:
        raw = {"text": _BASE_W_TEXT, "voice": _BASE_W_VOICE, "facial": _BASE_W_FACIAL}
        total = sum(raw.values())
    return {key: round(value / total, 3) for key, value in raw.items()}


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
        # 削掉的权重只回补给另一路可靠模态，不再回补面部：否则短句轮次会把面部
        # 权重抬回接近原值（实测 0.15 → 0.245），抵消压低头部权重的策略。
        w_voice += reduction
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
        w_text += reduction  # 同上：统一不给面部回补
        adjustments.append(f"voice_confidence_low({voice_conf:.2f}): voice-30%")

    # 规则6: 文本置信度过低
    text_conf = text_result.get("confidence", 0.0)
    if text_conf < 0.3:
        reduction = w_text * 0.30
        w_text -= reduction
        w_voice += reduction  # 同上：统一不给面部回补
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
