"""
零样本文本情感分析常驻单例
==========================
【模型】MoritzLaurer/mDeBERTa-v3-base-mnli-xnli
  - 多语言自然语言推理（NLI）模型
  - 零样本分类：无需微调，直接用候选标签推理
  - 中文 NLI 基准表现优秀，模型仅 ~280MB

【输入】ASR 转写文本
【输出】7 类统一标签概率分布（中文候选标签 → 英文统一标签）

【国内网络】
  HuggingFace 官方源（huggingface.co）在国内访问不稳定，
  通过设置 HF_ENDPOINT 环境变量切换到 hf-mirror.com 镜像站。
  必须在 import transformers 之前设置，否则无效。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

# ============================================================
#  HuggingFace 镜像站配置（必须在 import transformers 之前设置）
# ============================================================
# 国内网络下 huggingface.co 经常超时，切换到 hf-mirror.com 镜像
# 该镜像站完整代理 HuggingFace Hub，支持 resolve/下载
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
# 禁用 transformers 的符号链接警告（Windows 不支持）
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

_log = logging.getLogger("text-emotion-service")

# ============================================================
#  模块级单例状态
# ============================================================
_classifier = None
_lock = threading.Lock()
_loaded = False
_load_error: str | None = None

# ============================================================
#  候选标签 & 映射
# ============================================================
# 中文候选标签（零样本分类的 hypothesis 模板会自动构造）
CANDIDATE_LABELS_CN = ["开心", "悲伤", "愤怒", "惊讶", "恐惧", "厌恶", "中性"]

# 中文标签 → 统一英文标签
LABEL_MAP = {
    "开心": "happy",
    "悲伤": "sad",
    "愤怒": "angry",
    "惊讶": "surprised",
    "恐惧": "fearful",
    "厌恶": "disgusted",
    "中性": "neutral",
}

UNIFIED_LABELS = ["happy", "sad", "angry", "surprised", "fearful", "disgusted", "neutral"]

EMOTION_CN = {
    "happy": "开心", "sad": "悲伤", "angry": "愤怒", "surprised": "惊讶",
    "fearful": "恐惧", "disgusted": "厌恶", "neutral": "中性",
}


# ============================================================
#  模型加载
# ============================================================
def _load_once():
    """懒加载 transformers zero-shot-classification pipeline。"""
    global _classifier, _loaded, _load_error

    if _loaded and _classifier is not None:
        return

    with _lock:
        # 双重检查：等待锁期间可能已被其他线程加载完成
        if _loaded and _classifier is not None:
            return
        _log.info("[TextEmotion] 开始加载 mDeBERTa-v3 zero-shot 模型 ...")
        try:
            from transformers import pipeline

            _classifier = pipeline(
                "zero-shot-classification",
                model="MoritzLaurer/mDeBERTa-v3-base-mnli-xnli",
                device=-1,  # CPU（文本推理足够快，避免占用 GPU 显存）
            )
            _loaded = True
            _load_error = None
            _log.info("[TextEmotion] mDeBERTa-v3 加载完成")
        except Exception as e:
            _classifier = None
            _loaded = False
            _load_error = f"{type(e).__name__}: {e}"
            _log.exception("[TextEmotion] 加载失败: %s", e)
            raise


# ============================================================
#  对外接口
# ============================================================
def analyze(text: str) -> dict[str, Any]:
    """
    零样本文本情感分析。

    返回：
        {
            "emotion": "sad",
            "emotion_cn": "悲伤",
            "confidence": 0.82,
            "probabilities": {"happy": 0.03, "sad": 0.82, ...},
            "method": "mDeBERTa-v3 zero-shot",
            "text": "今天工作好累啊",
        }
    """
    if not text or len(text.strip()) < 2:
        return _empty_result(text or "")

    _load_once()
    if _classifier is None:
        raise RuntimeError(f"文本情感模型未加载，上次错误: {_load_error}")

    t0 = time.time()
    with _lock:
        result = _classifier(text, CANDIDATE_LABELS_CN, multi_label=False)

    # 映射到统一 7 类标签
    probs = {label: 0.0 for label in UNIFIED_LABELS}
    for label, score in zip(result["labels"], result["scores"]):
        unified = LABEL_MAP.get(label, "neutral")
        probs[unified] = float(score)

    dominant = max(probs, key=probs.get)
    elapsed = round(time.time() - t0, 3)
    _log.info(
        "[TextEmotion] 分析完成 | 耗时=%ss | emotion=%s(%.2f) | 文本=%s",
        elapsed, dominant, probs[dominant], text[:50],
    )

    return {
        "emotion": dominant,
        "emotion_cn": EMOTION_CN[dominant],
        "confidence": round(probs[dominant], 3),
        "probabilities": {k: round(v, 3) for k, v in probs.items()},
        "method": "mDeBERTa-v3 zero-shot",
        "text": text,
    }


def warmup():
    """预热：加载模型 + 空文本触发首次推理。"""
    _load_once()
    _log.info("[TextEmotion] 开始预热推理 ...")
    try:
        analyze("测试一下情感分析")
        _log.info("[TextEmotion] 预热完成")
    except Exception as e:
        _log.warning("[TextEmotion] 预热推理失败（不影响模型已加载）: %s", e)


def get_status() -> dict[str, Any]:
    return {
        "loaded": _loaded,
        "load_error": _load_error,
        "mode": "in-process (integrated)",
    }


def _empty_result(text: str) -> dict[str, Any]:
    return {
        "emotion": "neutral",
        "emotion_cn": "中性",
        "confidence": 0.0,
        "probabilities": {label: 0.0 for label in UNIFIED_LABELS},
        "method": "mDeBERTa-v3 zero-shot (empty/too_short)",
        "text": text,
    }
