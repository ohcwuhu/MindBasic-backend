"""兜底轮次"第二意见"风险自判的测试。

背景：``dify_risk_level`` 只有 Dify 会产出，Dify 抖动触发兜底时那一轮会从
一致性统计里消失。新增的 ``fallback_risk_level`` 让兜底轮次补一次同口径判定。

本文件覆盖四段：
1. 开关与前置条件（没开/没 Key 时绝不发请求）；
2. 判定调用的请求构造与解析（打桩 HTTP，不打真实 API）；
3. 补写落库：写进 ``fallback_risk_level``，不得污染 ``dify_risk_level``；
4. 统计聚合：兜底判定进入 ``secondOpinion``，且不改变原有 ``consistency`` 口径。
"""

import asyncio
import time

import pytest
from sqlalchemy import delete, select

from app.db.session import SessionLocal
from app.models.analysis import MultimodalAnalysisRecord
from app.services import analysis_stats_service as stats
from app.services.ai_lab import config as ai_cfg
from app.services.ai_lab import risk_judge
from app.services.analysis_record_service import (
    AnalysisSnapshot,
    _update_latest_risk_level,
    save_snapshot,
    update_fallback_risk_level,
)


# ============================================================
#  1) 开关与前置条件
# ============================================================
def test_disabled_when_flag_off(monkeypatch):
    monkeypatch.setattr(ai_cfg, "FALLBACK_RISK_JUDGE", False)
    monkeypatch.setattr(ai_cfg, "DEEPSEEK_API_KEY", "k")
    assert risk_judge.is_enabled() is False
    assert risk_judge.judge_risk("我不想活了") == (None, "")


def test_disabled_without_api_key(monkeypatch):
    monkeypatch.setattr(ai_cfg, "FALLBACK_RISK_JUDGE", True)
    monkeypatch.setattr(ai_cfg, "DEEPSEEK_API_KEY", "")
    assert risk_judge.is_enabled() is False


def test_empty_text_short_circuits(monkeypatch):
    monkeypatch.setattr(ai_cfg, "FALLBACK_RISK_JUDGE", True)
    monkeypatch.setattr(ai_cfg, "DEEPSEEK_API_KEY", "k")
    assert risk_judge.judge_risk("   ") == (None, "")


# ============================================================
#  2) 判定调用（打桩，不打真实 API）
# ============================================================
class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


@pytest.fixture
def judge_env(monkeypatch):
    monkeypatch.setattr(ai_cfg, "FALLBACK_RISK_JUDGE", True)
    monkeypatch.setattr(ai_cfg, "DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(ai_cfg, "DEEPSEEK_DISABLE_REASONING", True)
    captured: dict = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["payload"] = kwargs.get("json")
        return _FakeResponse(
            payload={
                "choices": [
                    {"message": {"content": '{"risk_level": "medium", "risk_reason": "模糊表达"}'}}
                ]
            }
        )

    monkeypatch.setattr(risk_judge.requests, "post", fake_post)
    return captured


def test_judge_risk_parses_level(judge_env):
    level, reason = risk_judge.judge_risk("我不太想撑下去了")
    assert level == "medium"
    assert reason == "模糊表达"


def test_judge_request_disables_reasoning_and_streams_off(judge_env):
    """判定要的是结论：必须关推理、非流式，否则又慢又容易被推理挤空。"""
    risk_judge.judge_risk("我最近挺好的")
    payload = judge_env["payload"]
    assert payload["reasoning_effort"] == "none"
    assert payload["stream"] is False
    assert payload["temperature"] == 0
    assert "/chat/completions" in judge_env["url"]
    assert payload["messages"][0]["role"] == "system"


def test_judge_risk_survives_http_error(monkeypatch):
    monkeypatch.setattr(ai_cfg, "FALLBACK_RISK_JUDGE", True)
    monkeypatch.setattr(ai_cfg, "DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(
        risk_judge.requests, "post",
        lambda url, **kw: _FakeResponse(status_code=401, text="unauthorized"),
    )
    assert risk_judge.judge_risk("随便说点什么") == (None, "")


def test_judge_risk_survives_exception(monkeypatch):
    monkeypatch.setattr(ai_cfg, "FALLBACK_RISK_JUDGE", True)
    monkeypatch.setattr(ai_cfg, "DEEPSEEK_API_KEY", "test-key")

    def boom(url, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(risk_judge.requests, "post", boom)
    assert risk_judge.judge_risk("随便说点什么") == (None, "")


# ============================================================
#  3) 补写落库
# ============================================================
def test_update_fallback_risk_level_does_not_touch_dify_column():
    """两个来源必须分列存放，否则报告口径会被悄悄污染。"""
    marker = f"pytest-fb-risk-{int(time.time() * 1000)}"
    snapshot = AnalysisSnapshot.from_video_call(
        user_id=None,
        session_id=marker,
        asr_text="我最近总是提不起劲",
        risk={"level": "LOW", "riskScore": 10},
        status="ok",
    )
    assert asyncio.run(save_snapshot(snapshot)) is True
    assert asyncio.run(update_fallback_risk_level(marker, "MEDIUM")) is True

    db = SessionLocal()
    try:
        row = db.scalar(
            select(MultimodalAnalysisRecord).where(
                MultimodalAnalysisRecord.session_id == marker
            )
        )
        assert row is not None
        assert row.fallback_risk_level == "medium", row.fallback_risk_level
        assert row.dify_risk_level is None, "兜底判定不得写进 Dify 的列"
        assert row.risk_level == "LOW", "平台侧等级不应被覆盖"

        db.execute(
            delete(MultimodalAnalysisRecord).where(
                MultimodalAnalysisRecord.session_id == marker
            )
        )
        db.commit()
    finally:
        db.close()


def test_update_fallback_risk_level_is_safe_without_row():
    assert asyncio.run(update_fallback_risk_level("pytest-no-such-session", "low")) is False
    assert asyncio.run(update_fallback_risk_level("", "low")) is False
    assert asyncio.run(update_fallback_risk_level("pytest-no-such-session", None)) is False


def test_unknown_risk_column_is_rejected():
    """列名会进入 UPDATE 语句，白名单之外的必须拒绝。"""
    assert asyncio.run(
        _update_latest_risk_level("x", "high", column="status", label="攻击")
    ) is False


# ============================================================
#  4) 统计聚合
# ============================================================
def _row(**kwargs) -> stats.AnalysisRow:
    kwargs.setdefault("status", "ok")
    return stats.AnalysisRow(**kwargs)


def test_stats_keep_dify_consistency_unchanged():
    """原有"平台 vs Dify"口径不能因为新增兜底判定而变化。"""
    rows = [
        _row(risk_level="HIGH", dify_risk_level="high"),
        _row(risk_level="LOW", dify_risk_level="medium"),
        # 只有兜底判定的一轮：不应进入严格口径
        _row(risk_level="LOW", fallback_risk_level="low"),
    ]
    result = stats.aggregate_rows(rows)
    assert result["risk"]["consistency"]["comparable"] == 2
    assert result["risk"]["consistency"]["matched"] == 1
    assert result["risk"]["consistency"]["rate"] == 0.5


def test_stats_second_opinion_counts_fallback_rounds():
    """兜底判定要补进第二意见口径，否则那些轮次就白丢了。"""
    rows = [
        _row(risk_level="HIGH", dify_risk_level="high"),
        _row(risk_level="LOW", fallback_risk_level="low"),
        _row(risk_level="MEDIUM", fallback_risk_level="medium"),
        _row(risk_level="LOW"),  # 两侧都没有：仍不可比
    ]
    result = stats.aggregate_rows(rows)
    second = result["risk"]["secondOpinion"]
    assert second["sources"] == {"dify": 1, "fallback": 2}
    assert second["comparable"] == 3
    assert second["matched"] == 3
    assert second["rate"] == 1.0


def test_stats_reports_fallback_distribution():
    rows = [
        _row(fallback_risk_level="low"),
        _row(fallback_risk_level="medium"),
        _row(fallback_risk_level="MEDIUM"),
    ]
    result = stats.aggregate_rows(rows)
    assert result["risk"]["fallback"]["distribution"] == {"LOW": 1, "MEDIUM": 2}
    assert result["risk"]["fallback"]["flagged"] == 2
