"""实时管线 SSE 消费的回归测试。

这一段是通话主链路里最容易悄悄坏掉的地方——它原先藏在
``register_socket_events`` 的闭包里，闭包内 `_cfg` / `_b64` 少了 import
也没人发现，表现是"分句 TTS 一直失败、只播最后一句"，日志里只有一条
被吞掉的 NameError。这里用假的 SSE 响应把两条分支都钉住：

1. Dify 事件流（含 message_end → workflow_finished 的顺序）；
2. DeepSeek 事件流。
"""

import asyncio
import base64
import json
import logging
from types import SimpleNamespace

from app.services.ai_lab import socket_events, tts_service
from app.services.ai_lab import config as ai_config

_log = logging.getLogger("test-llm-stream")


class _FakeSio:
    """记录服务端要推给前端的事件。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def emit(self, event, data=None, room=None, **kwargs):  # noqa: ANN001
        self.events.append((event, data or {}))
        return None

    def event_names(self) -> list[str]:
        return [name for name, _ in self.events]


class _FakeResp:
    """把预置的 SSE 行按 ``iter_lines`` 吐出来（模拟 requests 的流式响应）。"""

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def iter_lines(self, decode_unicode: bool = True):  # noqa: ARG002
        return iter(self._lines)


def _sse(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False)


def _session() -> SimpleNamespace:
    return SimpleNamespace(dify_conversation_id=None, llm_cancelled=False)


def _consume(lines: list[str], *, is_dify: bool, monkeypatch, session=None):
    """跑一遍流消费；TTS 用替身，避免真的去调 edge-tts。"""
    synthesized: list[tuple[str, str | None, str | None]] = []

    async def fake_synthesize(text, voice=None, rate=None):  # noqa: ANN001
        synthesized.append((text, voice, rate))
        yield b"\x00\x01\x02"

    monkeypatch.setattr(tts_service, "synthesize", fake_synthesize)

    sio = _FakeSio()
    outcome = asyncio.run(
        socket_events._consume_llm_stream(
            sio, _log, _FakeResp(lines),
            is_dify=is_dify, sid="sid-test", session=session or _session(),
        )
    )
    return outcome, sio, synthesized


def test_dify_stream_takes_risk_from_workflow_finished(monkeypatch):
    """message_end 之后再读 workflow_finished，才能拿到工作流自判风险等级。"""
    lines = [
        _sse({"event": "message", "answer": "我在听，"}),
        _sse({"event": "message", "answer": "你愿意多说一点吗？"}),
        _sse({"event": "message_end", "conversation_id": "conv-42"}),
        _sse({"event": "workflow_finished", "data": {"outputs": {"risk_level": "medium"}}}),
    ]
    session = _session()
    outcome, _sio, _synth = _consume(lines, is_dify=True, monkeypatch=monkeypatch, session=session)

    assert outcome["full_response"] == "我在听，你愿意多说一点吗？"
    assert session.dify_conversation_id == "conv-42"
    assert outcome["dify_risk_level"] == "medium"


def test_dify_sentence_tts_actually_synthesizes(monkeypatch):
    """分句 TTS 必须真的合成音频（原先闭包里 _cfg 未定义 → 静默失败）。"""
    lines = [
        _sse({"event": "message", "answer": "我在听，你愿意多说一点吗？"}),
        _sse({"event": "message", "answer": "这件事对你意味着什么？"}),
        _sse({"event": "workflow_finished", "data": {"outputs": {}}}),
    ]
    outcome, sio, synthesized = _consume(lines, is_dify=True, monkeypatch=monkeypatch)

    assert outcome["full_response"] == "我在听，你愿意多说一点吗？这件事对你意味着什么？"
    # 第二句到达时报出第一句 → 触发分句 TTS
    assert synthesized, "分句 TTS 未被触发，说明分句逻辑失效"
    text, voice, rate = synthesized[0]
    assert text == "我在听，你愿意多说一点吗？"
    assert voice == ai_config.TTS_VOICE, "TTS 语音参数取错（原 bug 就发生在这里）"
    assert rate == ai_config.TTS_RATE

    names = sio.event_names()
    assert "vc_tts_start" in names and "vc_tts_chunk" in names and "vc_tts_done" in names
    # 关键回归点：不能再因为内部 NameError 往外抛 vc_error
    assert not [n for n in names if n == "vc_error"], [
        e for e in sio.events if e[0] == "vc_error"
    ]
    # 前端拿到的音频分片是 base64
    chunk = next(d for n, d in sio.events if n == "vc_tts_chunk")
    assert chunk["data"] == base64.b64encode(b"\x00\x01\x02").decode("ascii")
    assert chunk["format"] == "mp3"


def test_deepseek_stream_assembles_tokens(monkeypatch):
    """DeepSeek 分支：delta.content 逐 token 拼接并推送。"""
    lines = [
        _sse({"choices": [{"delta": {"content": "你好"}}]}),
        _sse({"choices": [{"delta": {"content": "，我在。"}}]}),
        "data: [DONE]",
    ]
    outcome, sio, _synth = _consume(lines, is_dify=False, monkeypatch=monkeypatch)

    assert outcome["full_response"] == "你好，我在。"
    assert outcome["token_count"] == 2
    assert outcome["dify_risk_level"] is None
    assert sio.event_names().count("vc_llm_token") == 2


def test_broken_json_line_is_skipped(monkeypatch):
    """SSE 里出现半截 JSON 时跳过该行，不影响后续内容。"""
    lines = [
        "data: {不是合法 JSON",
        _sse({"choices": [{"delta": {"content": "继续"}}]}),
    ]
    outcome, _sio, _synth = _consume(lines, is_dify=False, monkeypatch=monkeypatch)
    assert outcome["full_response"] == "继续"


def test_deepseek_reasoning_content_is_never_spoken(monkeypatch):
    """推理模型的思考过程必须丢弃：它既不能进正文，更不能被 TTS 朗读。

    背景：deepseek-v4-flash 默认先吐 reasoning_content。若把推理混进正文，
    用户会听到模型的内心独白；若把它当正文计数，首 token 延迟也会被算错。
    """
    lines = [
        _sse({"choices": [{"delta": {"reasoning_content": "用户在问好，"}}]}),
        _sse({"choices": [{"delta": {"reasoning_content": "我该回一句问候。"}}]}),
        _sse({"choices": [{"delta": {"content": "你好"}}]}),
        _sse({"choices": [{"delta": {"content": "，我在。"}}]}),
        "data: [DONE]",
    ]
    outcome, sio, synthesized = _consume(lines, is_dify=False, monkeypatch=monkeypatch)

    assert outcome["full_response"] == "你好，我在。"
    assert "用户" not in outcome["full_response"], "推理内容混进了正文"
    assert outcome["token_count"] == 2, "推理分片不应计入 token 数"
    assert all("用户" not in text for text, _v, _r in synthesized), "推理内容被朗读了"


def test_deepseek_pure_reasoning_round_yields_empty(monkeypatch):
    """整轮只有推理、没有正文时判为"无产出"，交由上层降级——不能当成有效回复。"""
    lines = [
        _sse({"choices": [{"delta": {"reasoning_content": "想了很久"}}]}),
        "data: [DONE]",
    ]
    outcome, _sio, _synth = _consume(lines, is_dify=False, monkeypatch=monkeypatch)
    assert outcome["full_response"] == ""
