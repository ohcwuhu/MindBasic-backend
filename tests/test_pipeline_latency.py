"""实时管线延迟优化的回归测试。

这一批改动全是"执行顺序"层面的：把本可以并行的东西从串行改成并行、把阻塞调用
挪出事件循环。这类改动的特点是没有新功能、也不容易从返回值上看出问题，
但一旦被后来的改动回退，延迟会悄悄变差且很难发现。

因此这里锁两类不变量：

1. **行为**：Dify 预热的前置判断（关掉/没配 Key 就不能发请求）；
2. **结构**：管线里几处关键顺序（源码级断言）。

第 2 类用源码断言，而不是跑整条管线——完整管线要加载 SenseVoice / emotion2vec /
DeepFace 等重模型，单测代价过高；这一点与 ``test_runtime_guards`` /
``test_fallback_prompt`` 的处理方式一致。
"""

import asyncio
import inspect

import pytest

from app.services.ai_lab import dify_service
from app.services.ai_lab import socket_events


_SRC = inspect.getsource(socket_events)


def _pos(needle: str) -> int:
    """返回片段在源码中的位置；不存在则断言失败（比 -1 更容易定位）。"""
    index = _SRC.find(needle)
    assert index >= 0, f"源码中找不到：{needle!r}"
    return index


# ============================================================
#  1) Dify 入参声明预热
# ============================================================
def test_warm_skipped_when_dify_disabled(monkeypatch):
    """没配 Dify 时不该发这次网络请求。"""
    monkeypatch.setattr(dify_service, "is_enabled", lambda: False)
    called = {"n": 0}
    monkeypatch.setattr(
        dify_service, "fetch_input_types",
        lambda **kw: called.__setitem__("n", called["n"] + 1),
    )
    asyncio.run(socket_events._warm_dify_input_types())
    assert called["n"] == 0


def test_warm_calls_fetch_when_enabled(monkeypatch):
    """配了 Dify 就预热一次（结果进缓存，第一轮不必再等）。"""
    monkeypatch.setattr(dify_service, "is_enabled", lambda: True)
    called = {"n": 0}

    def fake_fetch(**kw):  # noqa: ARG001
        called["n"] += 1
        return {}

    monkeypatch.setattr(dify_service, "fetch_input_types", fake_fetch)
    asyncio.run(socket_events._warm_dify_input_types())
    assert called["n"] == 1


def test_warm_is_wired_into_call_start():
    """护栏：预热必须挂在通话开始，否则第一轮仍要等那一次往返。"""
    assert "_warm_dify_input_types()" in _SRC, "预热函数没有被调用"
    start = _pos("async def handle_vc_start")
    assert _SRC.find("_warm_dify_input_types()", start) > -1, "预热没有放在 vc_start 里"


# ============================================================
#  2) 语调情感与 ASR 并行
# ============================================================
def test_voice_emotion_starts_before_asr():
    """语调情感分析只依赖音频，必须早于 await ASR 启动，才能与其重叠。"""
    voice_start = _pos("_voice_future = (")
    asr_call = _pos("asr_result = await loop.run_in_executor(None, _sv.transcribe")
    assert voice_start < asr_call, (
        "语调情感仍在 ASR 之后启动——两者本可并行，回退会直接增加首字延迟"
    )


def test_emotion_functions_defined_before_asr():
    """两个分析函数必须定义在 ASR 之前，否则无法提前启动。"""
    assert _pos("def _run_voice_emotion") < _pos("_voice_future = (")
    assert _pos("def _run_text_emotion") < _pos("_voice_future = (")


def test_gather_awaits_preexisting_voice_future():
    """取结果时要等那个已启动的 future，而不是重新提交一次。"""
    assert "_voice_future,\n" in _SRC, "gather 没有等待预先启动的语调分析"
    # 语调分析只应提交一次：既提前启动、又在下面重复提交会白跑一遍重模型
    submitted = _SRC.count("run_in_executor(None, _run_voice_emotion)")
    assert submitted == 1, f"语调分析被提交了 {submitted} 次，应恰好 1 次"


# ============================================================
#  3) VLM 与多模态并行
# ============================================================
def test_vlm_starts_before_multimodal_section():
    """VLM 依赖 ASR 文本但不依赖情感结果，应提前启动，与多模态并行。"""
    vlm_start = _pos("_vlm_future = None")
    multimodal = _pos("── 1.5) 多模态情感分析")
    assert vlm_start < multimodal, "VLM 仍串行排在多模态之后，会整段加到关键路径上"
    # 结果必须在构造 LLM 请求之前取回
    assert _pos("vlm_result = await _vlm_future") < _pos("── 3) 构造 LLM 请求")


# ============================================================
#  4) 阻塞调用不得留在事件循环里
# ============================================================
def test_normalize_inputs_runs_in_executor():
    """``normalize_inputs`` 内部是同步 requests.get，必须放进线程池。

    否则缓存过期的那一轮会把整个事件循环冻结到请求返回为止——实测云端往返
    1.7~6.8s，期间所有连接都被卡住，不只是当前这通电话。
    """
    index = _pos("_dify.normalize_inputs")
    window = _SRC[max(0, index - 400): index]
    assert "run_in_executor" in window, (
        "normalize_inputs 直接跑在事件循环里（同步网络调用），会冻结整个服务"
    )
    assert "await loop.run_in_executor" in window


def test_no_bare_blocking_dify_call():
    """防御：不能再出现不带 executor 的直接调用写法。"""
    assert "dify_inputs = _dify.normalize_inputs(dify_inputs)\n" not in _SRC
