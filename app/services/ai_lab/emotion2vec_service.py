"""
emotion2vec+ 语调情感分析常驻单例
==================================
【来源】从 PaddleSpeech-develop/app.py 整合，去除 FastAPI 层，保留推理核心。

【模型】emotion2vec_plus_large（阿里达摩院，ModelScope）
  - 42526 小时多语种训练数据
  - 9 类输出：angry/disgusted/fearful/happy/neutral/sad/surprised/other/unknown
  - 与 SenseVoice 同属 funasr 生态，共享 torch 依赖

【降级策略】emotion2vec+ 加载失败时，由调用方回退到 opensmile_service
"""
from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
from typing import Any

_log = logging.getLogger("emotion2vec-service")

# ============================================================
#  模块级单例状态
# ============================================================
_model = None
_lock = threading.Lock()
_loaded = False
_load_error: str | None = None
_device_used: str = ""

# ============================================================
#  标签映射：emotion2vec+ 9 类 → 统一 7 类
# ============================================================
EMO2VEC_MAP = {
    "angry": "angry",
    "disgusted": "disgusted",
    "fearful": "fearful",
    "happy": "happy",
    "neutral": "neutral",
    "sad": "sad",
    "surprised": "surprised",
    "other": "neutral",     # 合并到 neutral
    "unknown": "neutral",   # 合并到 neutral
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
    """懒加载 emotion2vec+ large 模型。"""
    global _model, _loaded, _load_error, _device_used

    if _loaded and _model is not None:
        return

    with _lock:
        # 双重检查：等待锁期间可能已被其他线程加载完成
        if _loaded and _model is not None:
            return
        _log.info("[emotion2vec] 开始加载 emotion2vec_plus_large ...")
        try:
            from funasr import AutoModel
            import torch

            device = "cuda" if torch.cuda.is_available() else "cpu"
            _model = AutoModel(
                model="iic/emotion2vec_plus_large",
                hub="ms",             # ModelScope（国内网络更快）
                device=device,
                disable_update=True,
            )
            _loaded = True
            _load_error = None
            _device_used = device
            _log.info("[emotion2vec] emotion2vec_plus_large 加载完成 (device=%s)", device)
        except Exception as e:
            _model = None
            _loaded = False
            _load_error = f"{type(e).__name__}: {e}"
            _log.exception("[emotion2vec] 加载失败: %s", e)
            raise


# ============================================================
#  音频读取（复用 sensevoice 的 WAV 转码逻辑）
# ============================================================
def _ensure_wav(audio_path: str) -> str:
    """确保音频是 16kHz mono WAV，必要时转码。返回临时文件路径（需调用方清理）。"""
    if audio_path.lower().endswith(".wav"):
        return audio_path

    import subprocess
    try:
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        ffmpeg_exe = "ffmpeg"

    fd, tmp_wav = tempfile.mkstemp(suffix=".wav", prefix="emo2vec_")
    os.close(fd)

    proc = subprocess.run(
        [
            ffmpeg_exe, "-y", "-i", audio_path,
            "-ar", "16000", "-ac", "1", "-sample_fmt", "s16",
            tmp_wav,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=False,
        timeout=120,
    )
    if proc.returncode != 0:
        errmsg = proc.stderr.decode("utf-8", errors="replace")[-500:]
        try:
            os.remove(tmp_wav)
        except Exception:
            pass
        raise RuntimeError(f"ffmpeg 转码失败(exit={proc.returncode}): {errmsg}")
    return tmp_wav


# ============================================================
#  对外接口
# ============================================================
def analyze(audio_path: str) -> dict[str, Any]:
    """
    语调情感分析，返回统一 7 类概率分布。

    返回：
        {
            "emotion": "sad",
            "emotion_cn": "悲伤",
            "confidence": 0.71,
            "probabilities": {"happy": 0.06, "sad": 0.71, ...},
            "method": "emotion2vec_plus_large",
        }
    """
    _load_once()
    if _model is None:
        raise RuntimeError(f"emotion2vec+ 未加载，上次错误: {_load_error}")

    tmp_wav: str | None = None
    t0 = time.time()
    try:
        wav_path = _ensure_wav(audio_path)
        tmp_wav = wav_path if wav_path != audio_path else None

        with _lock:
            rec = _model.generate(
                input=wav_path,
                granularity="utterance",
                extract_embedding=False,
            )

        if not rec:
            return _empty_result()

        item = rec[0]
        labels = item.get("labels", [])
        scores = item.get("scores", [])

        if not labels or not scores:
            return _empty_result()

        # 映射到统一 7 类（other/unknown 合并到 neutral）
        probs = {label: 0.0 for label in UNIFIED_LABELS}
        for label, score in zip(labels, scores):
            lab_str = str(label).split("/")[-1]
            if lab_str == "<unk>":
                lab_str = "unknown"
            unified = EMO2VEC_MAP.get(lab_str, "neutral")
            probs[unified] = max(probs[unified], float(score))

        # 归一化到和为 1
        total = sum(probs.values())
        if total > 0:
            probs = {k: v / total for k, v in probs.items()}

        dominant = max(probs, key=probs.get)
        elapsed = round(time.time() - t0, 3)
        _log.info(
            "[emotion2vec] 分析完成 | 耗时=%ss | emotion=%s(%.2f)",
            elapsed, dominant, probs[dominant],
        )

        return {
            "emotion": dominant,
            "emotion_cn": EMOTION_CN[dominant],
            "confidence": round(probs[dominant], 3),
            "probabilities": {k: round(v, 3) for k, v in probs.items()},
            "method": "emotion2vec_plus_large",
        }
    finally:
        if tmp_wav and os.path.exists(tmp_wav):
            try:
                os.remove(tmp_wav)
            except Exception:
                pass


def warmup():
    """预热：加载模型 + 1s 静默音频空跑。"""
    import numpy as np

    _load_once()
    _log.info("[emotion2vec] 开始预热推理（1s 静默音频）...")
    try:
        import soundfile as sf
        silence = np.zeros(16000, dtype=np.float32)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            sf.write(f.name, silence, 16000)
            tmp_path = f.name
        try:
            analyze(tmp_path)
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        _log.info("[emotion2vec] 预热完成")
    except Exception as e:
        _log.warning("[emotion2vec] 预热推理失败（不影响模型已加载）: %s", e)


def get_status() -> dict[str, Any]:
    return {
        "loaded": _loaded,
        "load_error": _load_error,
        "device_used": _device_used,
        "mode": "in-process (integrated)",
    }


def _empty_result() -> dict[str, Any]:
    return {
        "emotion": "neutral",
        "emotion_cn": "中性",
        "confidence": 0.0,
        "probabilities": {label: 0.0 for label in UNIFIED_LABELS},
        "method": "emotion2vec_plus_large (empty)",
    }
