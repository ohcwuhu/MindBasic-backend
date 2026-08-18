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
DEEPSEEK_MODEL: str = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_TIMEOUT: int = int(os.environ.get("DEEPSEEK_TIMEOUT", "90"))

# Dify 智能体（可选）：配置 DIFY_API_KEY 后，视频通话 LLM 走 Dify 对话接口，否则回退 DeepSeek
DIFY_API_BASE: str = os.environ.get("DIFY_API_BASE", "https://api.dify.ai/v1")
DIFY_API_KEY: str = os.environ.get("DIFY_API_KEY", "")
DIFY_TIMEOUT: int = int(os.environ.get("DIFY_TIMEOUT", "90"))

# TTS 语音合成（edge-tts，免费无需 API Key）
TTS_VOICE: str = os.environ.get("TTS_VOICE", "zh-CN-XiaoxiaoNeural")
TTS_RATE: str = os.environ.get("TTS_RATE", "+20%")

# VLM 视觉理解（OpenAI 兼容 Vision API，可选）
# 未配置时视频通话跳过视觉理解，不影响其他功能
VLM_API_KEY: str = os.environ.get("VLM_API_KEY", "")
VLM_BASE_URL: str = os.environ.get("VLM_BASE_URL", "")
VLM_MODEL: str = os.environ.get("VLM_MODEL", "")
