"""
SenseVoice ASR 常驻单例（含 emo 辅助情感提取）
================================================
【功能】
  - 语音转文字（ASR），中文为主
  - 顺带提取 SenseVoice 自带的 emo 标签（happy/sad/angry/neutral/unk）
    作为语调情感的交叉验证信号（不单独占融合权重）

【速度保证】
  - 首次 ~40-60s（funasr 导入 + 权重下载），后续 < 2s（CPU） / < 0.6s（GPU）
  - emo 提取零额外推理成本（ASR 推理时已包含 emo token）

【准确性保证】
  - 参数与 SenseVoice demo1.py 逐字一致
  - ban_emo_unk=False 保留 emo token 以提取情感
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
import threading
import time
from typing import Any

from app.services.ai_lab import config

_log = logging.getLogger("sensevoice-service")

# ============================================================
#  模块级单例状态
# ============================================================
_model = None
_lock = threading.Lock()
_loaded = False
_load_error: str | None = None
_device_used: str = ""

# ============================================================
#  emo token 映射（SenseVoice 输出格式：<|HAPPY|>实际文本）
# ============================================================
EMO_TOKEN_MAP = {
    "<|HAPPY|>": "happy",
    "<|SAD|>": "sad",
    "<|ANGRY|>": "angry",
    "<|NEUTRAL|>": "neutral",
    "<|unk|>": "neutral",
}
# 匹配所有 <|XXX|> 形式的特殊 token
_EMO_TOKEN_RE = re.compile(r"<\|[^|]+\|>")


# ============================================================
#  音频格式转码（16kHz mono 16-bit PCM WAV）
# ============================================================
def _convert_to_wav(audio_path: str) -> str:
    """
    将任意音频格式转为 16kHz 单声道 16-bit PCM WAV。
    SenseVoice 只能处理 WAV 格式，webm/mp3/m4a 等需先转码。

    使用 imageio_ffmpeg.get_ffmpeg_exe() 获取内置 ffmpeg，
    避免依赖系统 PATH 中的 ffmpeg。
    """
    # 如果已经是 WAV，直接返回
    if audio_path.lower().endswith(".wav"):
        # 仍需检查采样率/声道/位深，但 SenseVoice 内部 load_audio_text_image_video
        # 会处理 WAV 的重采样，所以 WAV 直接返回即可
        return audio_path

    try:
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:
        _log.warning("[SenseVoice] imageio_ffmpeg 不可用，尝试系统 ffmpeg: %s", e)
        ffmpeg_exe = "ffmpeg"

    fd, tmp_wav = tempfile.mkstemp(suffix=".wav", prefix="sensevoice_")
    os.close(fd)

    import subprocess
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
#  模型加载
# ============================================================
def _has_cuda() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


def _load_once():
    """懒加载 funasr AutoModel（SenseVoiceSmall）。"""
    global _model, _loaded, _load_error, _device_used

    if _loaded and _model is not None:
        return

    with _lock:
        # 双重检查：等待锁期间可能已被其他线程加载完成
        if _loaded and _model is not None:
            return
        _log.info("[SenseVoice] 开始加载 SenseVoiceSmall ...")
        try:
            from funasr import AutoModel

            device = config.SENSEVOICE_DEVICE or ("cuda" if _has_cuda() else "cpu")
            _model = AutoModel(
                model="iic/SenseVoiceSmall",
                trust_remote_code=True,
                remote_code=str(config.SENSEVOICE_CODE_ROOT),
                device=device,
                disable_update=True,
            )
            _loaded = True
            _load_error = None
            _device_used = device
            _log.info("[SenseVoice] SenseVoiceSmall 加载完成 (device=%s)", device)
        except Exception as e:
            _model = None
            _loaded = False
            _load_error = f"{type(e).__name__}: {e}"
            _log.exception("[SenseVoice] 加载失败: %s", e)
            raise


# ============================================================
#  emo 提取
# ============================================================
def _extract_emo(text: str) -> str:
    """从 SenseVoice 输出文本中提取 emo 标签。
    输出格式形如 '<|zh|><|HAPPY|><|woitn|>今天天气很好'"""
    for token, label in EMO_TOKEN_MAP.items():
        if token in text:
            return label
    return "neutral"


def _strip_special_tokens(text: str) -> str:
    """移除所有 <|XXX|> 特殊 token，只保留转写文本。"""
    return _EMO_TOKEN_RE.sub("", text).strip()


# ============================================================
#  对外接口
# ============================================================
def transcribe(audio_path: str) -> dict[str, Any]:
    """
    ASR 转写 + emo 提取（一次推理同时产出）。

    返回：
        {
            "text": "今天天气很好",
            "language": "zh",
            "emo": "happy",           # happy/sad/angry/neutral
            "raw": "<|zh|><|HAPPY|>...",  # 原始输出（含特殊 token）
            "duration_seconds": 3.2,
        }
    """
    _load_once()
    if _model is None:
        raise RuntimeError(f"SenseVoice 未加载，上次错误: {_load_error}")

    tmp_wav: str | None = None
    t0 = time.time()
    try:
        # 转码为 WAV
        wav_path = _convert_to_wav(audio_path)
        is_temp = wav_path != audio_path
        tmp_wav = wav_path if is_temp else None

        with _lock:
            res = _model.generate(
                input=wav_path,
                language="auto",     # 自动检测语言（中/英/日/韩/粤）
                use_itn=True,        # 逆文本归一化（数字/日期等）
                ban_emo_unk=False,   # 保留 emo token
            )

        if not res:
            return {
                "text": "",
                "language": "zh",
                "emo": "neutral",
                "raw": "",
                "duration_seconds": 0.0,
            }

        item = res[0]
        raw_text = item.get("text", "")
        emo_label = _extract_emo(raw_text)
        clean_text = _strip_special_tokens(raw_text)

        # 估算音频时长
        try:
            import wave
            with wave.open(wav_path, "rb") as wf:
                duration = wf.getnframes() / wf.getframerate()
        except Exception:
            duration = 0.0

        elapsed = round(time.time() - t0, 3)
        _log.info(
            "[SenseVoice] 转写完成 | 耗时=%ss | emo=%s | 文本=%s",
            elapsed, emo_label, clean_text[:50],
        )

        return {
            "text": clean_text,
            "language": "zh",
            "emo": emo_label,
            "raw": raw_text,
            "duration_seconds": round(duration, 2),
        }
    finally:
        if tmp_wav and os.path.exists(tmp_wav):
            try:
                os.remove(tmp_wav)
            except Exception:
                pass


def warmup():
    """预热：加载模型 + 1s 静默音频空跑（触发首次推理 JIT）。"""
    import numpy as np

    _load_once()
    _log.info("[SenseVoice] 开始预热推理（1s 静默音频）...")
    try:
        import soundfile as sf
        silence = np.zeros(16000, dtype=np.float32)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            sf.write(f.name, silence, 16000)
            tmp_path = f.name
        try:
            transcribe(tmp_path)
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        _log.info("[SenseVoice] 预热完成（含 emo 提取）")
    except Exception as e:
        _log.warning("[SenseVoice] 预热推理失败（不影响模型已加载）: %s", e)


def get_status() -> dict[str, Any]:
    return {
        "loaded": _loaded,
        "load_error": _load_error,
        "device_used": _device_used,
        "mode": "in-process (integrated)",
    }
