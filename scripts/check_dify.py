"""
Dify 接入自检脚本
=================

用后端 `.env` 里的 `DIFY_API_BASE` / `DIFY_API_KEY` 做一次完整链路自检，
不走数据库、不启动服务，适合排障与作为「AI 技术应用」的取证材料。

用法（在 `MindBasic-backend/` 目录下执行）::

    python scripts/check_dify.py
    python scripts/check_dify.py --query "我最近总是睡不着" --stage goal_setting

检查项：

1. ``GET /parameters`` 能不能通、应用声明了哪些入参、分别是什么类型；
2. 后端每轮通话实际发送的那一组入参能不能被接受（类型是否匹配）；
3. ``POST /chat-messages`` 全链路是否跑通，逐个节点打印状态与耗时；
4. 知识检索有没有命中（对应 DSL 里 `知识检索 → 普通心理教练` 那条改动）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

import requests  # noqa: E402

from app.services.ai_lab import config as _cfg  # noqa: E402
from app.services.ai_lab import dify_service as _dify  # noqa: E402

DEFAULT_QUERY = "我最近总是睡不着，一躺下就想工作上的事，心里特别慌。"

_OK = "[ OK ]"
_BAD = "[FAIL]"
_WARN = "[WARN]"


def build_sample_inputs(query: str, stage: str) -> dict:
    """复刻 `socket_events.py` 每轮真实发送的那一组入参。"""
    return {
        "user_utterance": query,
        "fusion_emotion_cn": "焦虑",
        "fusion_confidence": 0.66,
        "facial_emotion_cn": "焦虑",
        "facial_confidence": 0.60,
        "voice_emotion_cn": "焦虑",
        "voice_confidence": 0.71,
        "text_emotion_cn": "焦虑",
        "text_confidence": 0.82,
        "live_score": 0.60,
        "live_level": "焦虑",
        "asr_text": query,
        "visual_description": "",
        "current_stage": stage,
        "goal_clear": False,
        "action_ready": False,
        # 与运行时一致：开始节点声明为 text-input，条件分支按小写 'true' 匹配
        "should_summarize_hint": "false",
        "platform_risk_level": "NONE",
        "modality_conflict": False,
        "modality_conflict_reason": "",
        # 平台侧卡片知识库检索的结果（本地 jieba + BM25，按阶段与风险过滤）。
        # 开始节点把它声明成了必填，所以这里必须带上（可以是空串）。
        "knowledge_context": "",
    }


def attach_knowledge_context(
    inputs: dict, query: str, *, stage: str | None = None, risk: str = "NONE"
) -> str:
    """用卡片知识库填充 knowledge_context，返回参考资料文本。"""
    try:
        from app.services.ai_lab import kb_cards

        block = kb_cards.context_block(query, stage=stage, risk=risk)
    except Exception as exc:
        print(f"{_WARN} 卡片知识库检索不可用：{exc}")
        return ""
    inputs["knowledge_context"] = block
    if block:
        hits = block.count("[资料")
        print(f"{_OK} 卡片知识库命中 {hits} 条，共 {len(block)} 字")
    else:
        info = kb_cards.stats()
        print(
            f"{_WARN} 卡片知识库 0 命中，本轮不注入"
            f"（ready={info.get('ready')} cards={info.get('cards')}）"
        )
    return block


def check_parameters() -> tuple[bool, dict[str, str]]:
    print("\n[1/3] 应用入参声明  GET /parameters")
    if not _dify.is_enabled():
        print(f"{_BAD} 未配置 DIFY_API_KEY，请先在 backend/.env 中填写应用密钥（app- 开头）")
        return False, {}
    try:
        result = _dify.probe()
    except Exception as exc:
        print(f"{_BAD} 请求失败：{exc}")
        print("       检查 DIFY_API_BASE 是否正确（云端 https://api.dify.ai/v1）")
        return False, {}
    declared = result["declared_inputs"]
    print(f"{_OK} base={result['api_base']}  key={result['key_prefix']}  入参 {len(declared)} 个")
    print(f"       知识检索元数据回传：{'开启' if result['retriever_enabled'] else '关闭'}")
    for name in sorted(declared):
        print(f"       - {name:<26} {declared[name]}")
    return True, declared


def check_input_types(declared: dict[str, str], inputs: dict) -> bool:
    print("\n[2/3] 入参类型匹配")
    coerced = _dify.normalize_inputs(inputs, input_types=declared)
    problems: list[str] = []
    converted = 0
    for key, value in inputs.items():
        declared_type = declared.get(key)
        if declared_type is None:
            problems.append(f"{key} 未在工作流声明（后端发了但工作流取不到）")
            continue
        fixed = coerced[key]
        if fixed != value:
            converted += 1
            note = "布尔值被转成小写字符串" if isinstance(value, bool) else "已按声明类型转换"
            print(f"{_WARN} {key}: {value!r} -> {fixed!r}（{note}）")
    for line in problems:
        print(f"{_WARN} {line}")
    if not problems and converted == 0:
        print(f"{_OK} {len(inputs)} 个入参的类型与工作流声明完全一致，无需转换")
    else:
        print(f"{_OK} 已生成可直接发送的入参（上列为自动转换项 {converted} 个）")
    return True


def check_chat(query: str, inputs: dict) -> bool:
    print("\n[3/3] 对话链路  POST /chat-messages（streaming）")
    resp = requests.post(
        f"{_dify.api_base()}/chat-messages",
        headers={
            "Authorization": f"Bearer {_dify.api_key()}",
            "Content-Type": "application/json",
        },
        json={
            "inputs": _dify.normalize_inputs(inputs),
            "query": query,
            "response_mode": "streaming",
            "user": "check-dify",
            "conversation_id": "",
        },
        timeout=_cfg.DIFY_TIMEOUT,
        stream=True,
    )
    print(f"       HTTP {resp.status_code}")
    if resp.status_code != 200:
        print(f"{_BAD} 请求被拒：{resp.text[:500]}")
        return False

    answer = ""
    hits = 0
    failed_at: str | None = None
    for line in resp.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: "):
            continue
        payload = line[6:]
        if payload.strip() == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        event = chunk.get("event")
        data = chunk.get("data") or {}
        if event == "node_finished":
            status = data.get("status")
            mark = _OK if status == "succeeded" else _BAD
            print(f"       {mark} {data.get('title')}  {round(data.get('elapsed_time') or 0, 2)}s")
            if status != "succeeded":
                failed_at = f"{data.get('title')}: {data.get('error')}"
        elif event == "workflow_finished":
            status = data.get("status")
            print(f"       工作流：{status}  步骤 {data.get('total_steps')}  token {data.get('total_tokens')}")
            if status != "succeeded":
                failed_at = str(data.get("error"))
        elif event in ("message", "agent_message"):
            answer += chunk.get("answer") or ""
        elif event == "message_end":
            hits = len((chunk.get("metadata") or {}).get("retriever_resources") or [])
        elif event == "error":
            failed_at = chunk.get("message")

    if failed_at:
        print(f"{_BAD} 工作流中断：{failed_at}")
        print("       多为 Dify 侧配置问题（见 docs/dify-工作流改动说明.md 第六节）")
        return False
    print(f"{_OK if answer.strip() else _BAD} 回复 {len(answer)} 字 | Dify 检索元数据 {hits} 条")
    print("       --- 回复预览 ---")
    print("       " + answer.strip()[:300].replace("\n", "\n       "))
    if not answer.strip():
        return False
    # 检索已改到平台侧（jieba + BM25 + DeepSeek 重排），Dify 侧元数据为 0 属正常；
    # 参考资料是否真进了提示词，看上面 [1/3] 的「本地知识库命中」以及回复有没有体现资料内容。
    print("       （Dify 检索元数据为 0 属正常：检索已改到平台侧，见 docs/知识库检索方案.md）")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Dify 接入自检")
    parser.add_argument("--query", default=DEFAULT_QUERY, help="自检用的用户话语")
    parser.add_argument("--stage", default="exploration", help="回传给工作流的平台阶段")
    parser.add_argument(
        "--risk", default="NONE", help="平台风险等级 NONE/LOW/MEDIUM/HIGH"
    )
    args = parser.parse_args()

    print("=" * 62)
    print("Dify 接入自检")
    print("=" * 62)
    inputs = build_sample_inputs(args.query, args.stage)
    reachable, declared = check_parameters()
    if not reachable:
        return 1
    attach_knowledge_context(inputs, args.query, stage=args.stage, risk=args.risk)
    check_input_types(declared, inputs)
    ok = check_chat(args.query, inputs)
    print("\n" + ("全部通过" if ok else "存在问题，见上方 FAIL 行"))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
