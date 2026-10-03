"""多模态与 AI 教练留痕的统计聚合。

用途：把留痕表从"只写不读"变成可交付的成效证据。输出面向三件事：

1. **应用成效**：真实分析次数、成功率、降级比例、风险分布；
2. **系统性能**：端到端与分段耗时分位数（P50 / P95）；
3. **机制验证**：阶段分布、平台与 Dify 风险判定的一致性。

设计取舍
--------
耗时与降级原因存放在 JSON 列里，MySQL 端聚合会依赖方言函数且不便复核，
因此这里采用"COUNT 精确 + 明细抽样"的方式：总量用 SQL 精确统计，
明细按时间倒序抽样 ``SAMPLE_CAP`` 条后在 Python 端聚合。
返回值中始终带 ``sampled`` 字段，说明分位数基于多少条样本，避免误读为全量统计。

聚合部分是纯函数（``aggregate_rows``），可脱离数据库单测。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_conversation import AiConversation, AiMessage
from app.models.analysis import MultimodalAnalysisRecord

#: 明细抽样上限（分位数基于该数量计算，返回结果中会说明）
SAMPLE_CAP = 5000

#: 参与统计的耗时字段（与实时管线写入的 timings 键保持一致）
LATENCY_KEYS: tuple[str, ...] = (
    "asr_seconds",
    "multimodal_seconds",
    "llm_first_token_seconds",
    "llm_total_seconds",
    "tts_first_audio_seconds",
    "e2e_seconds",
)

#: 短文本判定阈值（与融合层的"文本过短"规则一致）
SHORT_TEXT_LENGTH = 5

#: Dify 风险等级 → 平台四级口径的映射
_DIFY_TO_PLATFORM: dict[str, str] = {
    "high": "HIGH",
    "medium": "MEDIUM",
    "low": "LOW",
    "none": "NONE",
}

#: 需要建立工单的等级（与 crisis_rules 的处置口径一致）
_FLAGGED_LEVELS = {"MEDIUM", "HIGH"}


@dataclass(slots=True)
class AnalysisRow:
    """统计所需的单条留痕字段（便于脱离 ORM 单测）。"""

    status: str
    facial_frames: int | None = None
    voice_emotion: str | None = None
    asr_text: str | None = None
    weight_adjustments: list[str] | None = None
    timings: dict[str, Any] | None = None
    risk_level: str | None = None
    dify_risk_level: str | None = None
    fallback_risk_level: str | None = None
    coach_stage: str | None = None


def normalize_dify_risk(level: str | None) -> str | None:
    """把 Dify 的风险等级映射到平台四级口径，便于两侧一致性对比。"""
    if not level:
        return None
    return _DIFY_TO_PLATFORM.get(str(level).strip().lower())


def percentile(values: Sequence[float], quantile: float) -> float | None:
    """线性插值分位数；空序列返回 None。"""
    if not values:
        return None
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return round(ordered[0], 4)
    position = max(0.0, min(1.0, quantile)) * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 4)


def aggregate_rows(rows: Sequence[AnalysisRow], *, total: int | None = None) -> dict[str, Any]:
    """聚合留痕明细。

    Args:
        rows: 抽样得到的留痕明细（建议按时间倒序取最近 N 条）。
        total: 窗口内的精确总数；缺省时用抽样条数。
    """
    analyses_total = len(rows) if total is None else int(total)
    sampled = len(rows)

    status_counts: dict[str, int] = {}
    stage_counts: dict[str, int] = {}
    risk_counts: dict[str, int] = {}
    dify_risk_counts: dict[str, int] = {}
    fallback_risk_counts: dict[str, int] = {}
    adjustment_counts: dict[str, int] = {}
    latencies: dict[str, list[float]] = {key: [] for key in LATENCY_KEYS}

    missing_facial = 0
    missing_voice = 0
    short_text = 0
    with_adjustment = 0
    comparable = 0
    matched = 0
    platform_flagged = 0
    dify_flagged = 0
    fallback_flagged = 0
    comparable_any = 0
    matched_any = 0
    second_opinion_dify = 0
    second_opinion_fallback = 0

    for row in rows:
        status = str(row.status or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1

        if not row.facial_frames:
            missing_facial += 1
        if not row.voice_emotion:
            missing_voice += 1
        if len(str(row.asr_text or "").strip()) < SHORT_TEXT_LENGTH:
            short_text += 1

        adjustments = row.weight_adjustments or []
        if adjustments:
            with_adjustment += 1
            for reason in adjustments:
                key = str(reason).split("(")[0].strip() or "unknown"
                adjustment_counts[key] = adjustment_counts.get(key, 0) + 1

        if row.coach_stage:
            stage_counts[str(row.coach_stage)] = stage_counts.get(str(row.coach_stage), 0) + 1

        platform_level = str(row.risk_level).upper() if row.risk_level else None
        if platform_level:
            risk_counts[platform_level] = risk_counts.get(platform_level, 0) + 1
            if platform_level in _FLAGGED_LEVELS:
                platform_flagged += 1

        dify_level = normalize_dify_risk(row.dify_risk_level)
        if dify_level:
            dify_risk_counts[dify_level] = dify_risk_counts.get(dify_level, 0) + 1
            if dify_level in _FLAGGED_LEVELS:
                dify_flagged += 1

        # 一致性只在两侧都有判定时统计，避免把"未记录"当成"不一致"
        if platform_level and dify_level:
            comparable += 1
            if platform_level == dify_level:
                matched += 1

        fallback_level = normalize_dify_risk(row.fallback_risk_level)
        if fallback_level:
            fallback_risk_counts[fallback_level] = (
                fallback_risk_counts.get(fallback_level, 0) + 1
            )
            if fallback_level in _FLAGGED_LEVELS:
                fallback_flagged += 1

        # 第二意见口径：Dify 判定优先，缺失时用兜底判定补齐。
        # 这是给"主路径抖动导致样本缩水"兜底的覆盖率指标，
        # 与上面严格的 dify 一致性分开报告，避免两种口径互相污染。
        second_level = dify_level or fallback_level
        if second_level:
            if dify_level:
                second_opinion_dify += 1
            else:
                second_opinion_fallback += 1
            if platform_level:
                comparable_any += 1
                if platform_level == second_level:
                    matched_any += 1

        timings = row.timings or {}
        for key in LATENCY_KEYS:
            value = timings.get(key)
            if isinstance(value, (int, float)):
                latencies[key].append(float(value))

    def _rate(count: int) -> float:
        return round(count / sampled, 4) if sampled else 0.0

    return {
        "totals": {
            "analyses": analyses_total,
            "sampled": sampled,
            "status": status_counts,
            "okRate": _rate(status_counts.get("ok", 0)),
            "partialSuccessRate": _rate(status_counts.get("partial_success", 0)),
            "failedRate": _rate(status_counts.get("failed", 0)),
        },
        "degradation": {
            "analysesWithAdjustment": with_adjustment,
            "rate": _rate(with_adjustment),
            "topReasons": [
                {"reason": reason, "count": count}
                for reason, count in sorted(
                    adjustment_counts.items(), key=lambda item: item[1], reverse=True,
                )[:10]
            ],
        },
        "modalities": {
            "missingFacialRate": _rate(missing_facial),
            "missingVoiceRate": _rate(missing_voice),
            "shortTextRate": _rate(short_text),
        },
        "latency": {
            "sampled": max((len(values) for values in latencies.values()), default=0),
            "metrics": {
                key: {
                    "samples": len(values),
                    "p50": percentile(values, 0.5),
                    "p95": percentile(values, 0.95),
                }
                for key, values in latencies.items()
            },
        },
        "risk": {
            "platform": {
                "distribution": risk_counts,
                "flagged": platform_flagged,
                "flaggedRate": _rate(platform_flagged),
            },
            "dify": {
                "distribution": dify_risk_counts,
                "flagged": dify_flagged,
                "flaggedRate": _rate(dify_flagged),
            },
            "fallback": {
                "distribution": fallback_risk_counts,
                "flagged": fallback_flagged,
                "flaggedRate": _rate(fallback_flagged),
            },
            "consistency": {
                "comparable": comparable,
                "matched": matched,
                "rate": round(matched / comparable, 4) if comparable else None,
            },
            # 第二意见覆盖率：Dify 判定优先、兜底判定补位。
            # 用来回答"主路径不可用的那几轮有没有被统计丢掉"。
            "secondOpinion": {
                "sources": {
                    "dify": second_opinion_dify,
                    "fallback": second_opinion_fallback,
                },
                "comparable": comparable_any,
                "matched": matched_any,
                "rate": round(matched_any / comparable_any, 4) if comparable_any else None,
            },
        },
        "stages": {
            "distribution": stage_counts,
            "rate": {
                stage: round(count / sampled, 4) if sampled else 0.0
                for stage, count in stage_counts.items()
            },
        },
    }


async def multimodal_overview(
    db: AsyncSession,
    *,
    days: int = 30,
    source: str | None = None,
    sample_cap: int = SAMPLE_CAP,
) -> dict[str, Any]:
    """统计窗口内的多模态留痕与 AI 教练会话。"""
    start = datetime.now() - timedelta(days=days)

    filters = [MultimodalAnalysisRecord.created_at >= start]
    if source:
        filters.append(MultimodalAnalysisRecord.source == source)

    total = await db.scalar(
        select(func.count()).select_from(MultimodalAnalysisRecord).where(*filters)
    ) or 0

    detail_stmt = (
        select(
            MultimodalAnalysisRecord.status,
            MultimodalAnalysisRecord.facial_frames,
            MultimodalAnalysisRecord.voice_emotion,
            MultimodalAnalysisRecord.asr_text,
            MultimodalAnalysisRecord.weight_adjustments,
            MultimodalAnalysisRecord.timings,
            MultimodalAnalysisRecord.risk_level,
            MultimodalAnalysisRecord.dify_risk_level,
            MultimodalAnalysisRecord.fallback_risk_level,
            MultimodalAnalysisRecord.coach_stage,
        )
        .where(*filters)
        .order_by(MultimodalAnalysisRecord.created_at.desc())
        .limit(sample_cap)
    )
    detail_rows = (await db.execute(detail_stmt)).all()
    rows = [
        AnalysisRow(
            status=row[0],
            facial_frames=row[1],
            voice_emotion=row[2],
            asr_text=row[3],
            weight_adjustments=row[4],
            timings=row[5],
            risk_level=row[6],
            dify_risk_level=row[7],
            fallback_risk_level=row[8],
            coach_stage=row[9],
        )
        for row in detail_rows
    ]

    overview = aggregate_rows(rows, total=total)

    session_count = await db.scalar(
        select(func.count()).select_from(AiConversation).where(
            AiConversation.created_at >= start
        )
    ) or 0
    message_count = await db.scalar(
        select(func.count()).select_from(AiMessage).where(
            AiMessage.created_at >= start
        )
    ) or 0
    user_count = await db.scalar(
        select(func.count(func.distinct(AiConversation.user_id))).where(
            AiConversation.created_at >= start,
            AiConversation.user_id.is_not(None),
        )
    ) or 0

    overview["coaching"] = {
        "sessions": int(session_count),
        "messages": int(message_count),
        "distinctUsers": int(user_count),
    }

    day_col = func.date(MultimodalAnalysisRecord.created_at)
    daily_rows = (
        await db.execute(
            select(day_col.label("day"), func.count().label("cnt"))
            .where(*filters)
            .group_by(day_col)
            .order_by(day_col)
        )
    ).all()
    overview["daily"] = [
        {"date": str(day), "analyses": int(count)} for day, count in daily_rows
    ]
    overview["range"] = {
        "days": days,
        "from": start.date().isoformat(),
        "to": datetime.now().date().isoformat(),
        "source": source,
    }
    return overview


__all__ = [
    "LATENCY_KEYS",
    "SAMPLE_CAP",
    "SHORT_TEXT_LENGTH",
    "AnalysisRow",
    "aggregate_rows",
    "multimodal_overview",
    "normalize_dify_risk",
    "percentile",
]
