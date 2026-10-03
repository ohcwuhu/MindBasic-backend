"""
AI 实验室子系统配置
====================
集中管理模型路径、推理超时与 DeepSeek API 配置，
统一从 app.core.config.settings 读取 .env 中的敏感配置。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from app.core.config import settings

_HERE = Path(__file__).resolve().parent

# SenseVoice 核心代码根目录（funasr remote_code 指向此处）
SENSEVOICE_CODE_ROOT: Path = _HERE / "sensevoice"

# 设备：优先环境变量 SENSEVOICE_DEVICE，否则自动检测
SENSEVOICE_DEVICE: str = os.environ.get("SENSEVOICE_DEVICE", "")

OPENSMILE_FEATURE_SET: str = "eGeMAPSv02"

# 临时音频文件保存目录（None 表示使用系统 tempfile 默认目录）
TEMP_AUDIO_DIR: Path | None = None

# 单次推理超时（秒）
INFERENCE_TIMEOUT_SENSEVOICE: int = int(os.environ.get("RELMIND_SENSEVOICE_TIMEOUT", "600"))
INFERENCE_TIMEOUT_OPENSMILE: int = int(os.environ.get("RELMIND_OPENSMILE_TIMEOUT", "300"))

# 当前运行的 Python 解释器（诊断日志用）
CURRENT_PYTHON: str = sys.executable

# DeepSeek API（AI 心理教练 + 视频通话 LLM）
DEEPSEEK_API_KEY: str = settings.deepseek_api_key
DEEPSEEK_BASE_URL: str = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
# 与 Dify 工作流节点绑定的模型保持一致（工作流导出为 deepseek-v4-flash）。
# 直连实测：deepseek-v4-flash 与 deepseek-chat 由同一后端模型 deepseek-flash 提供，
# 区别只在 v4-flash 默认开启推理。
DEEPSEEK_MODEL: str = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
DEEPSEEK_TIMEOUT: int = int(os.environ.get("DEEPSEEK_TIMEOUT", "90"))

# 关闭推理模式（默认开）。
# deepseek-v4-flash 默认先输出 reasoning_content 再输出正文，对实时语音管线意味着
# "首 token 之前先静默数秒"；而且推理 token 与正文共享 max_tokens——实测
# max_tokens=80 时 80 个 token 全被推理吃光、正文为空，直接触发"本轮未产出内容"
# 降级。发送 reasoning_effort="none" 可关闭（实测首 delta 即为正文）。
# 若你的账号不认这个参数，后端会自动去掉它重试一次（见 socket_events）。
DEEPSEEK_DISABLE_REASONING: bool = os.environ.get(
    "DEEPSEEK_DISABLE_REASONING", "true"
).strip().lower() not in {"0", "false", "no", "off", ""}

# 单轮最大生成 token。开启推理时推理与正文共享该上限，必须给足。
DEEPSEEK_MAX_TOKENS: int = int(os.environ.get("DEEPSEEK_MAX_TOKENS", "500"))

# Dify 智能体（可选）：配置 DIFY_API_KEY 后，视频通话 LLM 走 Dify 对话接口，否则回退 DeepSeek
DIFY_API_BASE: str = settings.dify_api_base or os.environ.get("DIFY_API_BASE", "https://api.dify.ai/v1")
DIFY_API_KEY: str = settings.dify_api_key or os.environ.get("DIFY_API_KEY", "")
DIFY_TIMEOUT: int = int(os.environ.get("DIFY_TIMEOUT", "90"))

# LLM 请求失败自动重试次数（连接异常如 SSL EOF / 超时）
LLM_RETRIES: int = int(os.environ.get("LLM_RETRIES", "3"))


def deepseek_extra_params() -> dict[str, str]:
    """调用 DeepSeek 时要额外合并进请求体的参数。

    目前只有一项：关闭推理。``deepseek-v4-flash`` 默认先输出 ``reasoning_content``
    再给正文，而本项目的调用点都是"要正文、要快"（实时语音、文本教练、日记摘要），
    推理既拖慢首字又和正文共享 ``max_tokens``，实测会把正文挤空。

    统一从这里取，避免各调用点各自手写、漏掉一处就出现"某个功能突然回空"。
    若账号不认该参数，各调用点应去掉它重试一次（见 socket_events / risk_judge）。
    """
    if DEEPSEEK_DISABLE_REASONING:
        return {"reasoning_effort": "none"}
    return {}

# 兜底轮次的"第二意见"风险自判（默认开）。
# Dify 不可用时，兜底模型额外做一次非流式风险判定并补写留痕，
# 使"平台四级 vs 生成侧自判"的一致性统计不会因为主路径抖动而丢掉样本。
# 判定以后台任务执行，不占用用户这一轮的响应时间。
FALLBACK_RISK_JUDGE: bool = os.environ.get(
    "FALLBACK_RISK_JUDGE", "true"
).strip().lower() not in {"0", "false", "no", "off", ""}

# TTS 语音合成（edge-tts，免费无需 API Key）
TTS_VOICE: str = os.environ.get("TTS_VOICE", "zh-CN-XiaoxiaoNeural")
TTS_RATE: str = os.environ.get("TTS_RATE", "+20%")

# VLM 视觉理解（OpenAI 兼容 Vision API，可选）
# 未配置时视频通话跳过视觉理解，不影响其他功能
VLM_API_KEY: str = os.environ.get("VLM_API_KEY", "")
VLM_BASE_URL: str = os.environ.get("VLM_BASE_URL", "")
VLM_MODEL: str = os.environ.get("VLM_MODEL", "")
