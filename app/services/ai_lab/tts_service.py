"""
TTS 语音合成服务（edge-tts）
============================
【功能】
  - 使用 Microsoft Edge 在线 TTS 合成中文语音
  - 流式输出音频分片，适配实时视频通话场景
  - 无需 API Key，免费使用

【依赖】
  pip install edge-tts

【语音选择】
  - zh-CN-XiaoxiaoNeural：女声，温暖亲切（默认，适合心理教练场景）
  - zh-CN-YunxiNeural：男声，沉稳自然
  - zh-CN-XiaoyiNeural：女声，活泼年轻
"""
from __future__ import annotations

import logging
import asyncio
from typing import AsyncGenerator

_log = logging.getLogger("tts-service")

# 默认语音
DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"

# 可用语音列表（前端可选）
AVAILABLE_VOICES = {
    "xiaoxiao": {"id": "zh-CN-XiaoxiaoNeural", "name": "晓晓（女·温暖）"},
    "yunxi": {"id": "zh-CN-YunxiNeural", "name": "云希（男·沉稳）"},
    "xiaoyi": {"id": "zh-CN-XiaoyiNeural", "name": "晓伊（女·活泼）"},
    "yunjian": {"id": "zh-CN-YunjianNeural", "name": "云健（男·有力）"},
}


async def synthesize(
    text: str,
    voice: str = DEFAULT_VOICE,
    rate: str = "+0%",
    volume: str = "+0%",
) -> AsyncGenerator[bytes, None]:
    """
    流式合成语音，逐块yield音频数据（MP3格式）。

    参数：
        text:   要合成的文本
        voice:  语音ID（如 zh-CN-XiaoxiaoNeural）
        rate:   语速调节（如 "+10%" 加快，"-10%" 减慢）
        volume: 音量调节

    yields:
        MP3音频二进制数据块
    """
    import edge_tts

    communicate = edge_tts.Communicate(
        text=text,
        voice=voice,
        rate=rate,
        volume=volume,
    )

    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            yield chunk["data"]


async def synthesize_to_bytes(
    text: str,
    voice: str = DEFAULT_VOICE,
) -> bytes:
    """合成完整语音，返回完整MP3二进制数据。"""
    chunks: list[bytes] = []
    async for chunk in synthesize(text, voice):
        chunks.append(chunk)
    return b"".join(chunks)


def warmup() -> None:
    """预热：验证 edge-tts 可导入。"""
    try:
        import edge_tts  # noqa: F401
        _log.info("[TTS] edge-tts 可用，语音=%s", DEFAULT_VOICE)
    except ImportError:
        _log.warning("[TTS] edge-tts 未安装，请运行: pip install edge-tts")
    except Exception as e:
        _log.warning("[TTS] 预热失败: %s", e)
