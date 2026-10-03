"""
兜底轮次的"第二意见"风险自判（AI 实验室）
==========================================

为什么需要
----------

``multimodal_analysis_records.dify_risk_level`` 记录的是**生成侧**对安全风险的独立
判断，用来和平台规则引擎的四级判定做一致性统计。这个字段过去只有 Dify 工作流的
「安全风险识别」节点会产出：

- Dify 正常 → 有值，进入一致性统计；
- Dify 抖动触发兜底 → 永远为空，那一轮从统计里消失。

于是"Dify 挂了多久"会悄悄影响统计样本量，而这正是比赛/报告里那张一致性表的输入。

本模块让兜底轮次补上一次**同口径**的风险自判：提示词逐条对齐 Dify 的
「安全风险识别」节点（见 ``fallback_prompt.RISK_JUDGE_SYSTEM_PROMPT``），
所以两侧判的是同一套标准，比较才有意义。

三条设计约束
------------

1. **不占用用户这一轮的响应时间**：由后台任务调用，判定结果事后补写留痕，
   与 Dify 路径的 ``_backfill_dify_risk`` 完全同构。
2. **不污染语音流**：走独立的非流式请求，绝不在正文里塞机器可读标记
   （他们的 `代码改造清单` 已经把"标记进流式文本"列为不推荐做法，会出脏音）。
3. **失败即放弃**：任何异常都返回 ``(None, "")``，绝不写入不确定的等级，
   也绝不影响对话主流程。

存储位置
--------

判定结果写入 ``multimodal_analysis_records.fallback_risk_level``，
与 ``dify_risk_level`` 分列存放。刻意不合并成一列：合并会让报告里
"平台 vs Dify"的口径悄悄变成"平台 vs 平台兜底"，属于数据失真。
"""

from __future__ import annotations

import logging

import requests

from app.services.ai_lab import config as _cfg
from app.services.ai_lab import fallback_prompt as _prompt

log = logging.getLogger(__name__)

#: 判定输出很短（一个 JSON 对象），给 200 足够；温度 0 保证可复现。
_JUDGE_MAX_TOKENS = 200


def is_enabled() -> bool:
    """是否有条件做兜底自判（开关打开且 API Key 已配置）。"""
    return bool(_cfg.FALLBACK_RISK_JUDGE and (_cfg.DEEPSEEK_API_KEY or "").strip())


def judge_risk(
    user_text: str,
    *,
    recent_user_lines: list[str] | None = None,
    emotion_line: str = "",
    timeout: int | None = None,
) -> tuple[str | None, str]:
    """对一轮用户表达做风险分级，返回 ``(level, reason)``。

    ``level`` 为 ``low`` / ``medium`` / ``high``；判定失败或不可解析时返回
    ``(None, "")``。本函数从不抛异常——它跑在后台任务里，日志即全部反馈。
    """
    if not is_enabled():
        return None, ""
    text = str(user_text or "").strip()
    if not text:
        return None, ""

    payload: dict = {
        "model": _cfg.DEEPSEEK_MODEL,
        "messages": _prompt.risk_judge_messages(
            text,
            recent_user_lines=recent_user_lines,
            emotion_line=emotion_line,
        ),
        "temperature": 0,
        "max_tokens": _JUDGE_MAX_TOKENS,
        "stream": False,
    }
    # 与主对话一致地关闭推理：判定只要结论，不需要思考 token 占预算
    payload.update(_cfg.deepseek_extra_params())

    try:
        resp = requests.post(
            f"{_cfg.DEEPSEEK_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {(_cfg.DEEPSEEK_API_KEY or '').strip()}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout or _cfg.DEEPSEEK_TIMEOUT,
        )
        if resp.status_code == 400 and "reasoning_effort" in payload:
            # 账号不认该参数时去掉重试，与主链路保持同一套降级策略
            payload.pop("reasoning_effort", None)
            resp = requests.post(
                f"{_cfg.DEEPSEEK_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {(_cfg.DEEPSEEK_API_KEY or '').strip()}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=timeout or _cfg.DEEPSEEK_TIMEOUT,
            )
        if resp.status_code != 200:
            log.warning("[RiskJudge] 兜底自判失败：HTTP %s %s",
                        resp.status_code, resp.text[:200])
            return None, ""
        body = resp.json()
        content = (
            (body.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        )
        level, reason = _prompt.parse_risk_judgement(content)
        if level is None:
            log.warning("[RiskJudge] 兜底自判输出无法解析：%s", str(content)[:200])
        return level, reason
    except Exception as exc:  # noqa: BLE001 - 后台判定失败不得影响任何主流程
        log.warning("[RiskJudge] 兜底自判异常：%s", exc)
        return None, ""


__all__ = ["is_enabled", "judge_risk"]
