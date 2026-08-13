"""多模态音频分析引擎（SenseVoice ASR + openSMILE 情感，惰性加载 + 模拟降级）。"""

import os
import subprocess
import tempfile
from pathlib import Path

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

ASR_AVAILABLE = False
VOICE_AVAILABLE = False
_asr_model = None
_opensmile_analyzer = None


def _load_asr() -> bool:
    global ASR_AVAILABLE, _asr_model
    try:
        from funasr import AutoModel

        _asr_model = AutoModel(model="iic/SenseVoiceSmall", trust_remote_code=True, device="cpu")
        ASR_AVAILABLE = True
    except Exception:  # noqa: BLE001 - 模型缺失时降级
        ASR_AVAILABLE = False
    return ASR_AVAILABLE


def _load_voice() -> bool:
    global VOICE_AVAILABLE, _opensmile_analyzer
    try:
        import opensmile

        _opensmile_analyzer = opensmile.Smile(
            feature_set=opensmile.FeatureSet.eGeMAPSv02,
            feature_level=opensmile.FeatureLevel.Functionals,
        )
        VOICE_AVAILABLE = True
    except Exception:  # noqa: BLE001
        VOICE_AVAILABLE = False
    return VOICE_AVAILABLE


def _to_wav(data: bytes) -> Path | None:
    """webm → wav（依赖 imageio-ffmpeg 自带 ffmpeg；失败返回 None）。"""
    try:
        import imageio_ffmpeg

        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        tmp = Path(tempfile.mkdtemp(prefix="relmind_audio_"))
        webm = tmp / "input.webm"
        wav = tmp / "output.wav"
        webm.write_bytes(data)
        subprocess.run(
            [ffmpeg, "-y", "-i", str(webm), "-ar", "16000", "-ac", "1", str(wav)],
            capture_output=True,
            timeout=30,
        )
        return wav if wav.exists() else None
    except Exception:  # noqa: BLE001
        return None


def _mock_result(data: bytes) -> dict:
    seed = len(data) % 7
    texts = [
        "今天状态还不错，想继续把这件事做完。",
        "最近有点累，但也觉得在慢慢进步。",
        "谢谢你听我说这些，我心里舒服多了。",
        "我正在调整自己的节奏，一步一步来。",
        "这件事让我有点紧张，不过我愿意试试。",
        "听到你的反馈，我很受鼓励。",
        "我想先理清楚目标，再开始行动。",
    ]
    emotions = {"neutral": 0.62, "happy": 0.18, "sad": 0.12, "surprise": 0.08}
    primary = "neutral"
    return {
        "status": "mock",
        "transcription": {"text": texts[seed % len(texts)], "segments": None},
        "voice_features": {
            "primary_emotion": primary,
            "primary_emotion_cn": "中性",
            "primary_emotion_emoji": "😐",
            "primary_emotion_desc": "语气平稳，保持着自己的节奏。",
            "emotion_scores": emotions,
            "feature_summary": {"pitch_mean": 33.8, "loudness_mean": -8.0, "jitter": 0.03, "speaking_rate": 3.2},
            "key_metrics": [
                {"key": "pitch", "label": "平均基频", "value": 33.8, "unit": "semitone"},
                {"key": "loudness", "label": "平均响度", "value": -8.0, "unit": "dB"},
                {"key": "rate", "label": "语速", "value": 3.2, "unit": "字/秒"},
            ],
        },
    }


def analyze_audio(data: bytes) -> dict:
    """分析音频：优先真实模型，缺失时返回确定性模拟结果。"""
    if not (ASR_AVAILABLE or VOICE_AVAILABLE):
        _load_asr()
        _load_voice()
    if not (ASR_AVAILABLE or VOICE_AVAILABLE):
        return _mock_result(data)

    wav = _to_wav(data)
    transcription = None
    voice = None
    try:
        if wav is not None and ASR_AVAILABLE:
            result = _asr_model.generate(input=wav.resolve(), language="zh", use_itn=True)
            text = result[0].get("text", "") if result else ""
            transcription = {"text": text, "segments": None}
    except Exception:  # noqa: BLE001
        transcription = None
    try:
        if wav is not None and VOICE_AVAILABLE:
            frame = _opensmile_analyzer.process_file(str(wav))
            voice = {"status": "model", "raw": frame.to_dict("records")}
    except Exception:  # noqa: BLE001
        voice = None

    if transcription or voice:
        mock = _mock_result(data)
        return {
            "status": "partial_success",
            "transcription": transcription or mock["transcription"],
            "voice_features": voice or mock["voice_features"],
        }
    return _mock_result(data)
