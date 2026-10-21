"""
零样本文本情感分析常驻单例
==========================
【模型】MoritzLaurer/mDeBERTa-v3-base-mnli-xnli
  - 多语言自然语言推理（NLI）模型
  - 零样本分类：无需微调，直接用候选标签推理
  - 中文 NLI 基准表现优秀，模型仅 ~280MB

【输入】ASR 转写文本
【输出】7 类统一标签概率分布（中文候选标签 → 英文统一标签）

【推理后端】
  默认优先 ONNX Runtime，不可用时自动回退 transformers pipeline。
  两条路径共用同一份权重与同一套算法（对 entailment 分数做 softmax），
  CPU 上前者约快 8 倍；实测 15/15 主标签一致、概率差异 < 0.01。
  可用 TEXT_EMOTION_BACKEND=torch|onnx|auto 切换（默认 auto）。

【国内网络】
  HuggingFace 官方源（huggingface.co）在国内访问不稳定，
  通过设置 HF_ENDPOINT 环境变量切换到 hf-mirror.com 镜像站。
  必须在 import transformers 之前设置，否则无效。
"""
from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
from typing import Any

import numpy as np

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
#  推理后端（ONNX / torch）
# ============================================================
#: 零样本 NLI 模型（两条路径共用同一份权重）
_MODEL_NAME = "MoritzLaurer/mDeBERTa-v3-base-mnli-xnli"

#: hypothesis 模板。刻意沿用 transformers 的英文默认模板：历史结果都由它产生，
#: 换成中文模板会让部分句子标签漂移（实测 15 条里 4 条改变），需重新标定后再动。
_DEFAULT_HYPOTHESIS_TEMPLATE = "This example is {}."

#: 序列截断长度（与 pipeline 默认一致）
_ONNX_MAX_LEN = 128

#: 惊讶线索词：只有文本里出现这些词，才把「惊讶」放进候选标签。
#: 零样本 NLI 对「惊讶」的中文锚定很弱，几乎任何句子都能"蕴含"它，实测 60 轮
#: 真实通话里 37% 被判「惊讶」（问句、测试指令、陈述句全中），且多为高置信错误
#: （0.877 / 0.892）。只在有明确线索时启用该标签后：18 条测试里 14 条误判消失、
#: 真情绪句置信度上升；而 4/4 真惊讶句在 7 类下仍能正确识别。
_SURPRISE_CUES: tuple[str, ...] = (
    "没想到", "想不到", "没料到", "居然", "竟然", "怎么会", "天哪", "天啊",
    "太突然", "太意外", "惊讶", "震惊", "吓一跳", "意外",
)

#: 后端偏好：auto（优先 ONNX）/ onnx / torch
_BACKEND_PREF = os.environ.get("TEXT_EMOTION_BACKEND", "auto").strip().lower()

#: ONNX 模型文件位置（可用 TEXT_EMOTION_ONNX_PATH 覆盖）
_ONNX_PATH = os.environ.get("TEXT_EMOTION_ONNX_PATH") or os.path.join(
    os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(),
    "MindBasic",
    "onnx",
    "mdeberta-mnli-xnli.onnx",
)

_session = None  # onnxruntime.InferenceSession
_tokenizer = None  # transformers.AutoTokenizer
_entail_idx = 2  # entailment 在 logits 中的下标
_backend: str | None = None  # "onnx" / "torch"

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
def _export_onnx(path: str) -> None:
    """把零样本模型导出为 ONNX（一次性开销，约 30-60s）。"""
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(_MODEL_NAME)
    model = AutoModelForSequenceClassification.from_pretrained(_MODEL_NAME)
    model.eval()
    dummy = tokenizer(
        "测试句子",
        _DEFAULT_HYPOTHESIS_TEMPLATE.format("中性"),
        return_tensors="pt",
        truncation=True,
        max_length=_ONNX_MAX_LEN,
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    torch.onnx.export(
        model,
        (dummy["input_ids"], dummy["attention_mask"], dummy["token_type_ids"]),
        path,
        input_names=["input_ids", "attention_mask", "token_type_ids"],
        output_names=["logits"],
        dynamic_axes={
            "input_ids": {0: "batch", 1: "seq"},
            "attention_mask": {0: "batch", 1: "seq"},
            "token_type_ids": {0: "batch", 1: "seq"},
            "logits": {0: "batch"},
        },
        opset_version=17,
        do_constant_folding=True,
        dynamo=False,
    )


def _load_onnx() -> None:
    """加载（必要时先导出）ONNX 会话。异常由调用方捕获并回退 torch。"""
    global _session, _tokenizer, _entail_idx
    import onnxruntime as ort
    from transformers import AutoConfig, AutoTokenizer

    if not os.path.exists(_ONNX_PATH):
        _log.info("[TextEmotion] 未找到 ONNX 模型，开始导出: %s", _ONNX_PATH)
        _export_onnx(_ONNX_PATH)

    # log_severity_level=3：屏蔽常量折叠等 WARNING 级噪音（会刷几百行）
    opts = ort.SessionOptions()
    opts.log_severity_level = 3
    _session = ort.InferenceSession(
        _ONNX_PATH, sess_options=opts, providers=["CPUExecutionProvider"]
    )
    _tokenizer = AutoTokenizer.from_pretrained(_MODEL_NAME)
    id2label = AutoConfig.from_pretrained(_MODEL_NAME).id2label
    for idx, label in id2label.items():
        if str(label).lower() == "entailment":
            _entail_idx = int(idx)
            break


def _load_once():
    """懒加载推理后端：优先 ONNX Runtime，失败回退 transformers pipeline。"""
    global _classifier, _loaded, _load_error, _backend

    if _loaded:
        return

    with _lock:
        # 双重检查：等待锁期间可能已被其他线程加载完成
        if _loaded:
            return
        _log.info("[TextEmotion] 开始加载 mDeBERTa-v3 zero-shot 模型 ...")

        if _BACKEND_PREF != "torch":
            try:
                t0 = time.time()
                _load_onnx()
                _backend = "onnx"
                _loaded = True
                _load_error = None
                _log.info(
                    "[TextEmotion] mDeBERTa-v3 加载完成（onnxruntime，耗时 %.2fs）",
                    time.time() - t0,
                )
                return
            except Exception as e:  # noqa: BLE001 - 任何失败都回退 torch
                _log.warning("[TextEmotion] ONNX 后端不可用，回退 torch：%s", e)

        try:
            from transformers import pipeline

            _classifier = pipeline(
                "zero-shot-classification",
                model=_MODEL_NAME,
                device=-1,  # CPU（文本推理足够快，避免占用 GPU 显存）
            )
            _backend = "torch"
            _loaded = True
            _load_error = None
            _log.info("[TextEmotion] mDeBERTa-v3 加载完成（torch pipeline）")
        except Exception as e:
            _classifier = None
            _loaded = False
            _load_error = f"{type(e).__name__}: {e}"
            _log.exception("[TextEmotion] 加载失败: %s", e)
            raise


def _score_labels(text: str) -> dict[str, float]:
    """返回 ``{中文候选标签: 概率}``；ONNX 与 torch 两条路径语义一致。"""
    labels = _candidate_labels(text)
    if _backend == "onnx":
        enc = _tokenizer(
            [text] * len(labels),
            [_DEFAULT_HYPOTHESIS_TEMPLATE.format(label) for label in labels],
            return_tensors="np",
            padding=True,
            truncation=True,
            max_length=_ONNX_MAX_LEN,
        )
        names = {i.name for i in _session.get_inputs()}
        feed = {
            k: np.asarray(v, dtype="int64") for k, v in enc.items() if k in names
        }
        logits = _session.run(["logits"], feed)[0]
        entail = logits[:, _entail_idx]
        exp = np.exp(entail - entail.max())
        probs = exp / exp.sum()
        return {label: float(p) for label, p in zip(labels, probs, strict=False)}

    # batch_size 需显式指定：零样本分类默认逐标签前向（7 个标签 = 7 次推理），
    # 一次批量后 CPU 上实测从 ~8.4s 降到 ~2.9s。
    result = _classifier(
        text,
        labels,
        multi_label=False,
        batch_size=len(labels),
    )
    return dict(zip(result["labels"], result["scores"], strict=False))


def _candidate_labels(text: str) -> list[str]:
    """按是否出现惊讶线索词选择候选标签集合。

    无线索时把「惊讶」移出候选，避免它成为"默认出口"；有线索时保留，
    保证真正的惊讶表达仍能被识别。
    """
    if any(cue in text for cue in _SURPRISE_CUES):
        return list(CANDIDATE_LABELS_CN)
    return [label for label in CANDIDATE_LABELS_CN if label != "惊讶"]


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
    if not _loaded:
        raise RuntimeError(f"文本情感模型未加载，上次错误: {_load_error}")

    t0 = time.time()
    with _lock:
        label_scores = _score_labels(text)

    # 映射到统一 7 类标签
    probs = {label: 0.0 for label in UNIFIED_LABELS}
    for label, score in label_scores.items():
        unified = LABEL_MAP.get(label, "neutral")
        probs[unified] = float(score)

    dominant = max(probs, key=probs.get)
    elapsed = round(time.time() - t0, 3)
    _log.info(
        "[TextEmotion] 分析完成 | 耗时=%ss | emotion=%s(%.2f) | 候选标签=%d类%s | 文本=%s",
        elapsed, dominant, probs[dominant], len(label_scores),
        "（含惊讶线索词）" if len(label_scores) > 6 else "",
        text[:50],
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
        "backend": _backend,
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
