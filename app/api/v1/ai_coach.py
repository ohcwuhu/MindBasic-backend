"""
AI 心理教练（DeepSeek Chat API）
==================================
接收前端上传的识别上下文（语音转写 + 文本/语调/面部情绪 + 融合情绪），
以心理教练角色引导用户进行成长导向的对话。

【合规边界】
  - 不诊断、不治疗、不贴标签，仅做成长导向的陪伴式引导；
  - 检测到自伤/自杀等危机信号时，立即转介心理援助热线；
  - 所有回复仅供自我探索参考，不替代专业心理服务。
"""
from __future__ import annotations

import os
from typing import Any

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.ai_lab import config

router = APIRouter(prefix="/api/ai_coach", tags=["ai-coach"])

SYSTEM_PROMPT = """你是一位专业、温暖、克制的「AI 心理教练」，用中文与用户对话。

【角色与风格】
- 你陪伴用户做自我探索和成长，语气真诚温和，不端着、不评判、不贴标签。
- 每次回复尽量简洁（一般不超过 150 字），通常只问一个问题，像真正的教练一样引导用户自己发现答案。
- 多用开放性问题（“这件事对你来说意味着什么？”“你希望发生什么样的变化？”），少给空洞建议。
- 先共情、后提问：先让用户感到被听见，再引导深入。

【使用识别信号】
- 如果系统提供了“识别上下文”（语音转写、情绪标签等），请温柔地反映它，但保持谦逊：
  用“我观察到/听起来你……”这类措辞，明确这只是参考信号，避免把机器识别当作绝对结论。
- 若用户表达的内容与识别信号不一致，以用户说的话为准，不要固执于识别结果。

【安全边界（必须遵守）】
- 你不对用户做心理/精神疾病诊断，也不提供医疗、药物或诊断性建议。
- 若用户明确表达自伤、自杀、严重伤害他人等危机信号：先表达关心与接纳，然后明确建议
  立即拨打全国心理援助热线 12356（24 小时），或前往当地医院心理科/急诊求助，并鼓励其联系信任的人陪伴。
- 涉及创伤、幻觉、妄想等超出普通陪伴范围的内容时，温和建议其寻求线下专业心理服务。

【回复要求】
- 始终用中文回复。
- 一次只问一个问题，避免连珠炮式提问。
- 不要输出大段理论或说教。"""


class ChatMessage(BaseModel):
    role: str = Field(..., description="user | assistant")
    content: str = Field(..., description="消息内容")


class CoachContext(BaseModel):
    """设备自动采集的识别信号（仅供参考，可能不准确）。"""
    transcription: str = ""
    text_emotion: str | None = None
    text_emotion_confidence: float | None = None
    voice_emotion: str | None = None
    voice_emotion_confidence: float | None = None
    facial_emotion: str | None = None
    fusion_emotion: str | None = None
    fusion_confidence: float | None = None
    live_score: int | None = None
    live_level: str | None = None


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(..., min_length=1)
    context: CoachContext | None = None


def _build_context_message(ctx: CoachContext) -> str | None:
    """把识别上下文转成一段客观描述（无则返回 None）。"""
    lines: list[str] = []
    if ctx.transcription.strip():
        lines.append(f"- 语音转写文本：{ctx.transcription.strip()}")
    if ctx.text_emotion:
        conf = f"（置信度 {ctx.text_emotion_confidence:.2f}）" if ctx.text_emotion_confidence else ""
        lines.append(f"- 文本情绪：{ctx.text_emotion}{conf}")
    if ctx.voice_emotion:
        conf = f"（置信度 {ctx.voice_emotion_confidence:.2f}）" if ctx.voice_emotion_confidence else ""
        lines.append(f"- 语调情绪：{ctx.voice_emotion}{conf}")
    if ctx.facial_emotion:
        lines.append(f"- 面部情绪：{ctx.facial_emotion}")
    if ctx.fusion_emotion:
        conf = f"（置信度 {ctx.fusion_confidence:.2f}）" if ctx.fusion_confidence else ""
        lines.append(f"- 融合情绪：{ctx.fusion_emotion}{conf}")
    if ctx.live_score is not None:
        lines.append(f"- 实时投入度：{ctx.live_score} 分（{ctx.live_level or '未知'}）")
    if not lines:
        return None
    return (
        "以下是设备自动采集到的客观识别信号，仅作参考、可能存在误差，"
        "请勿当作诊断或绝对依据，请温和地结合用户当前话语进行引导：\n" + "\n".join(lines)
    )


@router.post("/chat")
def chat(req: ChatRequest) -> dict[str, Any]:
    """与 AI 心理教练对话（携带可选识别上下文）。"""
    api_key = config.DEEPSEEK_API_KEY
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="AI 教练服务未配置 DEEPSEEK_API_KEY，请在 backend/.env 中设置后重启后端。",
        )

    history: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]

    ctx_msg = _build_context_message(req.context) if req.context else None
    if ctx_msg:
        history.append({"role": "system", "content": ctx_msg})

    # 只保留最近 12 条对话，控制 token 消耗
    history.extend(
        {"role": m.role, "content": m.content}
        for m in req.messages[-12:]
        if m.role in ("user", "assistant") and m.content.strip()
    )

    try:
        resp = requests.post(
            f"{config.DEEPSEEK_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": config.DEEPSEEK_MODEL,
                "messages": history,
                "temperature": 0.7,
                "max_tokens": 600,
                "stream": False,
            },
            timeout=config.DEEPSEEK_TIMEOUT,
        )
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"AI 服务请求失败：{e}") from e

    if resp.status_code != 200:
        detail = "AI 服务暂时不可用，请稍后再试。"
        try:
            err_body = resp.json()
            detail = err_body.get("error", {}).get("message") or detail
        except Exception:
            pass
        status = 429 if resp.status_code == 429 else 502
        raise HTTPException(status_code=status, detail=f"AI 服务返回错误：{detail}")

    data = resp.json()
    try:
        reply = data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as e:
        raise HTTPException(status_code=502, detail="AI 服务响应格式异常") from e

    return {
        "reply": reply,
        "model": data.get("model", config.DEEPSEEK_MODEL),
        "usage": data.get("usage", {}),
    }
