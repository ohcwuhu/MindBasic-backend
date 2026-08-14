"""
VLM 视觉理解服务
================
【功能】
  - 接收视频帧（base64 图片），调用视觉大模型进行场景/物体/文字识别
  - 支持 OpenAI 兼容的 Vision API 格式
  - 可配置使用豆包/通义千问/智谱等国内 VLM API

【配置】
  VLM_API_KEY=xxx        # API密钥
  VLM_BASE_URL=xxx       # API地址
  VLM_MODEL=xxx          # 模型名称

【未配置时】
  返回空结果，不影响视频通话主流程（仅跳过视觉理解）
"""
from __future__ import annotations

import base64
import logging
import os
from typing import Any

import requests

_log = logging.getLogger("vlm-service")

# 从环境变量读取配置
VLM_API_KEY: str = os.environ.get("VLM_API_KEY", "")
VLM_BASE_URL: str = os.environ.get("VLM_BASE_URL", "")
VLM_MODEL: str = os.environ.get("VLM_MODEL", "")


def is_available() -> bool:
    """VLM 服务是否可用（已配置 API Key）。"""
    return bool(VLM_API_KEY and VLM_BASE_URL and VLM_MODEL)


def analyze_frame(
    img_base64: str,
    question: str = "请描述这个画面中的内容。",
    conversation_history: list[dict] | None = None,
) -> dict[str, Any]:
    """
    调用 VLM 分析视频帧。

    参数：
        img_base64: base64 编码的图片（支持 data:image/jpeg;base64,xxx 前缀）
        question:   用户关于画面的提问
        conversation_history: 之前的对话历史（可选）

    返回：
        {
            "description": str,    # VLM 描述文本
            "model": str,          # 使用的模型
            "error": str | None,   # 错误信息
        }
    """
    if not is_available():
        return {
            "description": "",
            "model": "",
            "error": "VLM 未配置（缺少 VLM_API_KEY/VLM_BASE_URL/VLM_MODEL）",
        }

    # 统一图片格式
    if not img_base64.startswith("data:image"):
        img_base64 = f"data:image/jpeg;base64,{img_base64}"

    # 构造消息
    messages: list[dict] = []

    # 添加历史上下文（最近3轮）
    if conversation_history:
        for msg in conversation_history[-6:]:
            messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})

    # 当前轮次：图片 + 问题
    messages.append({
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": img_base64}},
            {"type": "text", "text": question},
        ],
    })

    try:
        resp = requests.post(
            f"{VLM_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {VLM_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": VLM_MODEL,
                "messages": messages,
                "max_tokens": 500,
                "temperature": 0.3,
            },
            timeout=30,
        )

        if resp.status_code != 200:
            err_msg = f"VLM API 返回 {resp.status_code}"
            try:
                err_body = resp.json()
                err_msg = err_body.get("error", {}).get("message", err_msg)
            except Exception:
                pass
            return {"description": "", "model": VLM_MODEL, "error": err_msg}

        data = resp.json()
        description = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()

        _log.info("[VLM] 分析成功，描述长度=%d", len(description))
        return {
            "description": description,
            "model": data.get("model", VLM_MODEL),
            "error": None,
        }

    except requests.RequestException as e:
        _log.error("[VLM] 请求失败: %s", e)
        return {"description": "", "model": VLM_MODEL, "error": f"网络请求失败: {e}"}
    except Exception as e:
        _log.error("[VLM] 未知错误: %s", e, exc_info=True)
        return {"description": "", "model": VLM_MODEL, "error": f"未知错误: {e}"}


def warmup() -> None:
    """预热：检查配置。"""
    if is_available():
        _log.info("[VLM] 已配置: model=%s, base_url=%s", VLM_MODEL, VLM_BASE_URL)
    else:
        _log.warning("[VLM] 未配置 VLM_API_KEY/VLM_BASE_URL/VLM_MODEL，视觉理解功能将跳过")
