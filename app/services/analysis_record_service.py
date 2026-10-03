"""多模态分析留痕服务。

把一次分析的结构化结果（三模态输出、融合权重、风险分级、耗时）落库，
使识别效果可以被统计、被复现、被复核。写入采用"尽力而为"策略：
留痕失败不得影响用户正在进行的对话，因此本模块对外从不抛异常。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Mapping

from sqlalchemy import select, update

from app.db.session import AsyncSessionLocal
from app.models.analysis import MultimodalAnalysisRecord

_log = logging.getLogger("analysis-record")

#: 来源标识
SOURCE_HTTP_ANALYZE = "HTTP_ANALYZE"
SOURCE_VIDEO_CALL = "VIDEO_CALL"

#: ASR 文本入库长度上限（保留足够复核上下文，避免超长写入）
_MAX_ASR_TEXT = 2000


@dataclass(slots=True)
class AnalysisSnapshot:
    """一次多模态分析的结构化快照。

    Attributes:
        source: 分析入口，取值见 ``SOURCE_*``。
        user_id: 触发分析的用户，未知时为 ``None``。
        session_id: SocketIO 会话标识。
        status: ``ok`` / ``partial_success`` / ``failed``。
        timings: 各阶段耗时（秒），字段名与接口返回保持一致。
    """

    source: str
    user_id: int | None = None
    session_id: str | None = None
    status: str = "ok"
    asr_text: str = ""
    asr_emotion: str | None = None
    text_emotion: str | None = None
    text_confidence: float | None = None
    voice_emotion: str | None = None
    voice_confidence: float | None = None
    facial_emotion: str | None = None
    facial_confidence: float | None = None
    facial_frames: int | None = None
    fusion_emotion: str | None = None
    fusion_confidence: float | None = None
    weights: dict[str, float] | None = None
    weight_adjustments: list[str] | None = None
    calibration: dict[str, Any] | None = None
    conflict: dict[str, Any] | None = None
    risk_level: str | None = None
    risk_score: int | None = None
    risk_reasons: list[str] | None = None
    dify_risk_level: str | None = None
    coach_stage: str | None = None
    goal_clear: bool | None = None
    action_ready: bool | None = None
    should_summarize: bool | None = None
    conversation_id: int | None = None
    timings: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_http_response(
        cls,
        body: Mapping[str, Any],
        *,
        user_id: int | None,
        session_id: str | None = None,
    ) -> "AnalysisSnapshot":
        """从 ``POST /api/analyze_audio`` 的响应体构造快照。

        接口响应已经是稳定契约，因此评测数据与线上返回严格一致。
        """
        transcription = body.get("transcription") or {}
        text_emotion = body.get("text_emotion") or {}
        voice_emotion = body.get("voice_emotion") or {}
        facial_emotion = body.get("facial_emotion") or {}
        fusion = body.get("fusion") or {}
        timings = _http_timings(body)
        risk = body.get("risk") or {}

        return cls(
            source=SOURCE_HTTP_ANALYZE,
            user_id=user_id,
            session_id=session_id or (body.get("server_info") or {}).get("sid"),
            status=str(body.get("status") or "ok"),
            asr_text=str(transcription.get("text") or ""),
            asr_emotion=_optional_str(
                (voice_emotion.get("sv_cross_check") or {}).get("emotion")
            ),
            text_emotion=_optional_str(text_emotion.get("emotion")),
            text_confidence=_optional_float(text_emotion.get("confidence")),
            voice_emotion=_optional_str(voice_emotion.get("emotion")),
            voice_confidence=_optional_float(voice_emotion.get("confidence")),
            facial_emotion=_optional_str(facial_emotion.get("dominant_emotion")),
            facial_confidence=_optional_float(facial_emotion.get("confidence")),
            facial_frames=_optional_int(facial_emotion.get("frame_count")),
            fusion_emotion=_optional_str(fusion.get("final_emotion")),
            fusion_confidence=_optional_float(fusion.get("overall_confidence")),
            weights=fusion.get("weights_used"),
            weight_adjustments=fusion.get("weight_adjustments"),
            calibration=fusion.get("calibration"),
            conflict=fusion.get("conflict"),
            risk_level=_optional_str(risk.get("level")),
            risk_score=_optional_int(risk.get("riskScore")),
            risk_reasons=risk.get("reasons"),
            dify_risk_level=_optional_str(body.get("dify_risk_level")),
            coach_stage=_optional_str(
                (body.get("stage") or {}).get("stage")
                if isinstance(body.get("stage"), Mapping) else body.get("coach_stage")
            ),
            timings=dict(timings),
        )

    @classmethod
    def from_video_call(
        cls,
        *,
        user_id: int | None,
        session_id: str,
        asr_text: str,
        asr_emotion: str | None = None,
        text_emotion: Mapping[str, Any] | None = None,
        voice_emotion: Mapping[str, Any] | None = None,
        fusion: Mapping[str, Any] | None = None,
        facial_emotion: Mapping[str, Any] | None = None,
        risk: Mapping[str, Any] | None = None,
        stage: Mapping[str, Any] | None = None,
        dify_risk_level: str | None = None,
        conversation_id: int | None = None,
        timings: Mapping[str, Any] | None = None,
        status: str = "ok",
    ) -> "AnalysisSnapshot":
        """从实时音视频管线构造快照。

        Args:
            stage: 五阶段判定结果（``coach_stage_service.StageDecision`` 或等价字典）。
            dify_risk_level: Dify 工作流自判的风险等级；工作流未暴露时为 ``None``，
                一致性统计会自动跳过该行，不会把"未记录"当成"不一致"。
            conversation_id: 关联的 ``ai_conversations.id``。
        """
        text_emotion = text_emotion or {}
        voice_emotion = voice_emotion or {}
        fusion = fusion or {}
        facial_emotion = facial_emotion or {}
        risk = risk or {}
        stage = stage or {}

        return cls(
            source=SOURCE_VIDEO_CALL,
            user_id=user_id,
            session_id=session_id,
            status=status,
            asr_text=asr_text,
            asr_emotion=_optional_str(asr_emotion),
            text_emotion=_optional_str(text_emotion.get("emotion")),
            text_confidence=_optional_float(text_emotion.get("confidence")),
            voice_emotion=_optional_str(voice_emotion.get("emotion")),
            voice_confidence=_optional_float(voice_emotion.get("confidence")),
            facial_emotion=_optional_str(facial_emotion.get("dominant_emotion")),
            facial_confidence=_optional_float(facial_emotion.get("confidence")),
            facial_frames=_optional_int(facial_emotion.get("frame_count")),
            fusion_emotion=_optional_str(fusion.get("final_emotion")),
            fusion_confidence=_optional_float(fusion.get("overall_confidence")),
            weights=fusion.get("weights_used"),
            weight_adjustments=fusion.get("weight_adjustments"),
            calibration=fusion.get("calibration"),
            conflict=fusion.get("conflict"),
            risk_level=_optional_str(risk.get("level")),
            risk_score=_optional_int(risk.get("riskScore")),
            risk_reasons=risk.get("reasons"),
            dify_risk_level=_optional_str(dify_risk_level),
            coach_stage=_optional_str(stage.get("stage")),
            goal_clear=_optional_bool(stage.get("goal_clear")),
            action_ready=_optional_bool(stage.get("action_ready")),
            should_summarize=_optional_bool(stage.get("should_summarize")),
            conversation_id=conversation_id,
            timings=dict(timings or {}),
        )

    def to_row(self) -> dict[str, Any]:
        """转换为 ORM 写入所需的普通字典。"""
        return {
            "user_id": self.user_id,
            "source": self.source,
            "session_id": self.session_id,
            "asr_text": (self.asr_text or "")[:_MAX_ASR_TEXT] or None,
            "asr_emotion": self.asr_emotion,
            "text_emotion": self.text_emotion,
            "text_confidence": self.text_confidence,
            "voice_emotion": self.voice_emotion,
            "voice_confidence": self.voice_confidence,
            "facial_emotion": self.facial_emotion,
            "facial_confidence": self.facial_confidence,
            "facial_frames": self.facial_frames,
            "fusion_emotion": self.fusion_emotion,
            "fusion_confidence": self.fusion_confidence,
            "weights": self.weights,
            "weight_adjustments": self.weight_adjustments,
            "calibration": self.calibration,
            "conflict": self.conflict,
            "risk_level": self.risk_level,
            "risk_score": self.risk_score,
            "risk_reasons": self.risk_reasons,
            "dify_risk_level": self.dify_risk_level,
            "coach_stage": self.coach_stage,
            "goal_clear": self.goal_clear,
            "action_ready": self.action_ready,
            "should_summarize": self.should_summarize,
            "conversation_id": self.conversation_id,
            "timings": self.timings or None,
            "status": self.status,
        }


async def save_snapshot(snapshot: AnalysisSnapshot) -> bool:
    """写入分析留痕（尽力而为，从不抛异常）。

    Args:
        snapshot: 待写入的快照。

    Returns:
        ``True`` 表示写入成功；``False`` 表示被跳过或写入失败（已记录日志）。
    """
    if not snapshot.asr_text and not snapshot.fusion_emotion:
        # 没有任何识别结果的空轮次不占用存储
        return False
    try:
        async with AsyncSessionLocal() as db:
            db.add(MultimodalAnalysisRecord(**snapshot.to_row()))
            await db.commit()
        return True
    except Exception as exc:  # noqa: BLE001 - 留痕失败绝不能影响主流程
        _log.warning("[AnalysisRecord] 留痕写入失败: %s", exc)
        return False


#: 风险等级列长度（``dify_risk_level`` / ``fallback_risk_level``，见 models/analysis.py）
_MAX_DIFY_RISK_LENGTH = 8

#: 允许补写的风险等级列白名单——列名会进入 UPDATE 语句，绝不能由外部输入拼接
_RISK_LEVEL_COLUMNS = frozenset({"dify_risk_level", "fallback_risk_level"})


async def _update_latest_risk_level(
    session_id: str,
    level: str | None,
    *,
    column: str,
    label: str,
    source: str = SOURCE_VIDEO_CALL,
) -> bool:
    """把生成侧自判的风险等级补写到该会话最近一轮留痕上。

    实时管线是"先落留痕、后拿生成侧判定"，所以这里按「该会话最近一行」
    定位本轮记录（同一会话同一时刻只跑一轮管线，不存在争用）。

    Args:
        session_id: SocketIO sid，与留痕行的 ``session_id`` 对应。
        level: 自判等级（high/medium/low/none，大小写不限）。
        column: 目标列名，只允许 ``dify_risk_level`` / ``fallback_risk_level``。
        label: 日志用的来源名（如 "Dify" / "兜底"）。
        source: 限定来源，避免误更新别的入口写的行。

    Returns:
        ``True`` 表示确有 1 行被更新；其余情况（无等级、找不到行、写库失败）
        返回 ``False``。本函数从不抛异常——留痕补写不得影响对话。
    """
    if not session_id:
        return False
    if column not in _RISK_LEVEL_COLUMNS:
        # 防御：列名绝不能来自外部输入，避免拼出任意 UPDATE
        _log.warning("[AnalysisRecord] 拒绝未知风险列名: %s", column)
        return False
    value = (level or "").strip().lower()[: _MAX_DIFY_RISK_LENGTH]
    if not value:
        return False
    try:
        async with AsyncSessionLocal() as db:
            # MySQL 不允许在 UPDATE 的子查询里引用被更新的表（错误 1093），
            # 因此先查出目标行 id，再按 id 更新。
            latest_id = (
                select(MultimodalAnalysisRecord.id)
                .where(
                    MultimodalAnalysisRecord.session_id == session_id,
                    MultimodalAnalysisRecord.source == source,
                )
                .order_by(MultimodalAnalysisRecord.id.desc())
                .limit(1)
            )
            target_id = await db.scalar(latest_id)
            if target_id is None:
                _log.debug("[AnalysisRecord] 没有可补写的留痕行 session=%s", session_id)
                return False
            result = await db.execute(
                update(MultimodalAnalysisRecord)
                .where(MultimodalAnalysisRecord.id == target_id)
                .values({column: value})
            )
            await db.commit()
            updated = bool(result.rowcount)
        if updated:
            _log.info("[AnalysisRecord] 补写%s风险等级 session=%s level=%s",
                      label, session_id, value)
        return updated
    except Exception as exc:  # noqa: BLE001 - 补写失败绝不能影响主流程
        _log.warning("[AnalysisRecord] %s风险等级补写失败: %s", label, exc)
        return False


async def update_dify_risk_level(
    session_id: str,
    level: str | None,
    *,
    source: str = SOURCE_VIDEO_CALL,
) -> bool:
    """补写 Dify 工作流自判的风险等级（主路径）。"""
    return await _update_latest_risk_level(
        session_id, level, column="dify_risk_level", label="Dify", source=source,
    )


async def update_fallback_risk_level(
    session_id: str,
    level: str | None,
    *,
    source: str = SOURCE_VIDEO_CALL,
) -> bool:
    """补写兜底模型自判的风险等级（Dify 不可用时的第二意见）。

    与 ``update_dify_risk_level`` 写入**不同的列**，避免把两个来源混成一列：
    否则报告里"平台 vs Dify"的一致性会悄悄变成"平台 vs 兜底"。
    """
    return await _update_latest_risk_level(
        session_id, level, column="fallback_risk_level", label="兜底", source=source,
    )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _http_timings(body: Mapping[str, Any]) -> dict[str, Any]:
    """把 ``/api/analyze_audio`` 的 ``timing`` 块转换为留痕表的统一耗时口径。

    接口返回的是 ``{total_seconds, asr_seconds, voice_emotion_seconds,
    text_emotion_seconds}``；留痕与统计使用 ``asr_seconds / multimodal_seconds /
    e2e_seconds``。两者都保留：统一字段用于聚合，原始字段用于复核来源。
    """
    timing = body.get("timing")
    timings: dict[str, Any] = {}
    if isinstance(timing, Mapping):
        asr_seconds = _optional_float(timing.get("asr_seconds"))
        total_seconds = _optional_float(timing.get("total_seconds"))
        voice_seconds = _optional_float(timing.get("voice_emotion_seconds"))
        text_seconds = _optional_float(timing.get("text_emotion_seconds"))
        if asr_seconds is not None:
            timings["asr_seconds"] = asr_seconds
        if voice_seconds is not None or text_seconds is not None:
            timings["multimodal_seconds"] = round(
                (voice_seconds or 0.0) + (text_seconds or 0.0), 3
            )
        if total_seconds is not None:
            timings["e2e_seconds"] = total_seconds
        timings.update(
            {
                f"http_{key}": value
                for key, value in timing.items()
                if isinstance(value, (int, float))
            }
        )
    provided = body.get("timings")
    if isinstance(provided, Mapping):
        timings.update(provided)
    return timings


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_bool(value: Any) -> bool | None:
    """布尔化：``None`` 保持为 ``None``，避免把"未判定"写成 False。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


__all__ = [
    "SOURCE_HTTP_ANALYZE",
    "SOURCE_VIDEO_CALL",
    "AnalysisSnapshot",
    "save_snapshot",
    "update_dify_risk_level",
    "update_fallback_risk_level",
]
