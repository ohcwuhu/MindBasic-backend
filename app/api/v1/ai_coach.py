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

import logging
from typing import Any

import anyio
import requests
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.core.exceptions import AppError
from app.core.rate_limit import rate_limit
from app.models.user import User
from app.services import crisis_service
from app.services.ai_lab import config

log = logging.getLogger("ai-coach")

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

#: 风险判定达到 MEDIUM/HIGH 时追加的处置指令（优先级高于风格要求）
CRISIS_DIRECTIVE = """【本轮安全处置要求（优先级高于其他风格要求）】
系统检测到用户本次表达可能存在自伤/自杀风险信号。请按以下顺序回应：
1. 先表达关心与接纳，不评判、不追问方式细节，不渲染悲剧感；
2. 明确建议用户拨打全国心理援助热线 12356（24 小时），或前往就近医院急诊，
   并鼓励其联系信任的人陪伴；
3. 不进行任何心理/精神疾病诊断，不提供医疗建议，不讨论自伤方式。"""


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


def _latest_user_text(req: ChatRequest) -> str:
    """取最近一条用户消息；没有则回退到识别上下文中的转写文本。"""
    for message in reversed(req.messages):
        if message.role == "user" and message.content.strip():
            return message.content.strip()
    if req.context and req.context.transcription:
        return req.context.transcription.strip()
    return ""


def _risk_signals(ctx: CoachContext | None) -> dict[str, Any]:
    """把识别上下文转换为风险分级所需的模态信号。"""
    if ctx is None:
        return {}
    return {
        "voice_emotion": ctx.voice_emotion,
        "voice_confidence": ctx.voice_emotion_confidence or 0.0,
        "facial_emotion": ctx.facial_emotion,
        "facial_confidence": 0.0,
        "facial_frames": 0,
    }


def _request_completion(api_key: str, history: list[dict[str, str]]) -> requests.Response:
    """同步调用上游模型（由线程池执行，避免阻塞事件循环）。"""
    url = f"{config.DEEPSEEK_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "model": config.DEEPSEEK_MODEL,
        "messages": history,
        "temperature": 0.7,
        # 文本教练的回复比语音轮次长，沿用原有 600 上限（不受 DEEPSEEK_MAX_TOKENS 影响）
        "max_tokens": 600,
        "stream": False,
    }
    # 关闭推理：开启时正文可能被推理 token 挤空，表现为"AI 老师不回复"。
    payload.update(config.deepseek_extra_params())
    resp = requests.post(url, headers=headers, json=payload, timeout=config.DEEPSEEK_TIMEOUT)
    if resp.status_code == 400 and "reasoning_effort" in payload:
        # 账号不认该参数时去掉重试，避免整条链路直接失败
        payload.pop("reasoning_effort", None)
        resp = requests.post(url, headers=headers, json=payload, timeout=config.DEEPSEEK_TIMEOUT)
    return resp


@router.post("/chat")
async def chat(
    req: ChatRequest,
    user: User = Depends(get_current_user),
    _limiter: None = Depends(rate_limit("ai_coach_chat", 30, 60)),
) -> dict[str, Any]:
    """与 AI 心理教练对话（携带可选识别上下文）。

    每次请求都会先做一次风险分级：命中 MEDIUM/HIGH 时建立危机工单、
    向模型注入安全处置指令，并在响应中返回 ``risk`` 字段供前端展示。
    """
    api_key = config.DEEPSEEK_API_KEY
    if not api_key:
        # 走统一业务异常：前端只认 {code, message, data} 包络，HTTPException
        # 会产生 {"detail": ...}，前端只能退化成"请求失败"
        raise AppError(
            503,
            "AI_NOT_CONFIGURED",
            "AI 教练服务未配置，请在后台设置 DEEPSEEK_API_KEY 后重启服务。",
        )

    user_text = _latest_user_text(req)
    risk = crisis_service.assess(user_text, _risk_signals(req.context))
    if risk.flagged:
        await crisis_service.flag_crisis_safely(
            user.id,
            crisis_service.SOURCE_AI_COACH,
            user_text,
            assessment=risk,
        )

    history: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]

    ctx_msg = _build_context_message(req.context) if req.context else None
    if ctx_msg:
        history.append({"role": "system", "content": ctx_msg})

    if risk.flagged:
        history.append({"role": "system", "content": CRISIS_DIRECTIVE})

    # 只保留最近 12 条对话，控制 token 消耗
    history.extend(
        {"role": m.role, "content": m.content}
        for m in req.messages[-12:]
        if m.role in ("user", "assistant") and m.content.strip()
    )

    try:
        resp = await anyio.to_thread.run_sync(_request_completion, api_key, history)
    except requests.RequestException as e:
        # 上游异常细节只进日志，不回传给客户端（避免泄露内部信息）
        log.warning("AI 教练上游请求失败: %s", e)
        raise AppError(502, "AI_UPSTREAM_ERROR", "AI 服务暂时不可用，请稍后再试。") from e

    if resp.status_code != 200:
        try:
            err_body = resp.json()
            upstream_detail = err_body.get("error", {}).get("message")
        except Exception:
            upstream_detail = None
        log.warning("AI 教练上游返回 %s: %s", resp.status_code, upstream_detail or resp.text[:200])
        status = 429 if resp.status_code == 429 else 502
        code = "AI_RATE_LIMITED" if status == 429 else "AI_UPSTREAM_ERROR"
        message = "AI 服务繁忙，请稍后再试。" if status == 429 else "AI 服务暂时不可用，请稍后再试。"
        raise AppError(status, code, message)

    data = resp.json()
    try:
        reply = data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as e:
        log.warning("AI 教练响应格式异常: %s", data)
        raise AppError(502, "AI_BAD_RESPONSE", "AI 服务响应异常，请稍后再试。") from e

    return {
        "reply": reply,
        "model": data.get("model", config.DEEPSEEK_MODEL),
        "usage": data.get("usage", {}),
        "risk": risk.to_dict(),
    }
