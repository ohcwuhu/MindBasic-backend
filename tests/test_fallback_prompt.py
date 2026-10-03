"""DeepSeek 兜底分支的提示词与上下文拼装测试。

锁定四类行为：
1. 教练方法论与 Dify「普通心理教练」节点同源（关键约束不能丢）；
2. 阶段判定转写：阶段口径以平台为准，收束条件能强制收束；
3. 知识注入：无命中的空串不产生空消息，命中时带上使用约束；
4. 管线接线：socket_events 确实把教练方法论与上下文拼进了 history。

第 4 条是回归护栏——管线端到端要跑 ASR/TTS，单测成本过高，
因此用源码级断言防止"补丁被后来的人删掉"。
"""

import inspect

from app.services.ai_lab import fallback_prompt as fb
from app.services.ai_lab import socket_events
from app.services import coach_stage_service as stage


# ============================================================
#  1) 教练方法论
# ============================================================
def test_coach_guide_keeps_dify_core_rules():
    """Dify 提示词里的硬约束必须留在兜底版本里。"""
    guide = fb.COACH_GUIDE
    for marker in (
        "倾听 → 聚焦 → 探索 → 澄清目标 → 发现选择 → 行动",
        "不要一上来就给解决方案",
        "假设性反映",
        "这不一定完全准确，你可以纠正我",
        "不诊断心理疾病",
        "不要向用户宣布或解释风险等级",
        "2～3 个可选择方向",
        "连续 2 轮主要以提问推进",
        "不知道怎么说",
    ):
        assert marker in guide, f"教练方法论缺少关键约束：{marker}"


def test_coach_guide_does_not_override_voice_length_rule():
    """字数口径只由 _VC_SYSTEM_PROMPT 规定，方法论不该再写一个数字打架。"""
    assert "180 字" not in fb.COACH_GUIDE
    assert "不要超过 100 字" not in fb.COACH_GUIDE
    assert "适合语音播放" in fb.COACH_GUIDE


# ============================================================
#  2) 阶段判定转写
# ============================================================
def test_stage_message_uses_platform_stage():
    msg = fb.stage_message(stage=stage.STAGE_ACTION_PLANNING)
    assert msg is not None
    assert "行动规划" in msg["content"]
    assert stage.STAGE_ACTION_PLANNING in msg["content"]
    assert "第一步" in msg["content"]


def test_stage_message_reports_progress_flags():
    msg = fb.stage_message(
        stage=stage.STAGE_GOAL_SETTING, goal_clear=True, action_ready=True,
    )
    assert msg is not None
    assert "希望达到的状态已经比较清楚" in msg["content"]
    assert "愿意尝试一个具体行动" in msg["content"]


def test_should_summarize_forces_closing_with_reason():
    msg = fb.stage_message(
        stage=stage.STAGE_CLOSING,
        should_summarize=True,
        summary_reason="目标清楚且行动已形成",
    )
    assert msg is not None
    assert "收束" in msg["content"]
    assert "目标清楚且行动已形成" in msg["content"]
    assert "不要再提出新的深层探索问题" in msg["content"]


def test_stage_message_empty_stage_returns_none():
    assert fb.stage_message(stage="") is None
    assert fb.stage_message(stage="   ") is None


def test_stage_message_unknown_stage_still_usable():
    """阶段枚举将来新增时，兜底不该直接失效。"""
    msg = fb.stage_message(stage="brand_new_stage")
    assert msg is not None
    assert "brand_new_stage" in msg["content"]


# ============================================================
#  3) 知识注入与线索冲突
# ============================================================
def test_knowledge_message_empty_returns_none():
    assert fb.knowledge_message("") is None
    assert fb.knowledge_message("   \n ") is None


def test_knowledge_message_carries_dify_usage_rules():
    msg = fb.knowledge_message("[资料1]《高效教练》· 目标协商\n先确认目标。")
    assert msg is not None
    assert "《高效教练》" in msg["content"]
    # Dify 侧的三条使用约束必须保留，否则模型会念出"根据资料"
    assert "根据资料" in msg["content"]
    assert "以用户说的为准" in msg["content"]
    assert "直接忽略" in msg["content"]


def test_modality_conflict_message_includes_reason():
    msg = fb.modality_conflict_message("文本积极但语调低落")
    assert "文本积极但语调低落" in msg["content"]
    assert "确认用户的真实感受" in msg["content"]


# ============================================================
#  4) 组装顺序与管线接线
# ============================================================
def test_build_context_messages_order():
    msgs = fb.build_context_messages(
        stage=stage.STAGE_EXPLORATION,
        knowledge_context="[资料1] 内容",
        modality_conflict=True,
        modality_conflict_reason="文字说没事、语气低落",
    )
    assert len(msgs) == 3
    assert "教练进程" in msgs[0]["content"]
    assert "线索不一致" in msgs[1]["content"]
    assert "书籍片段" in msgs[2]["content"]
    assert all(m["role"] == "system" for m in msgs)


def test_build_context_messages_minimal():
    """只有阶段可用时不该凭空造出消息。"""
    msgs = fb.build_context_messages(stage=stage.STAGE_OPENING)
    assert len(msgs) == 1
    assert "开始交流" in msgs[0]["content"]


def test_socket_events_wires_fallback_context():
    """护栏：管线必须把教练方法论与上下文拼进 history。"""
    src = inspect.getsource(socket_events)
    assert "fallback_prompt" in src, "兜底提示词模块没有被引入管线"
    assert "_fallback.guide_for" in src, "教练方法论没有接进 history"
    assert "_fallback.build_context_messages" in src, "阶段/知识上下文没有接进 history"


# ============================================================
#  5) 双档教练方法论（延迟优化）
# ============================================================
def test_trivial_round_uses_lite_guide():
    """寒暄/极短输入走精简版：这些轮次用不上完整方法论。"""
    for text in ("你好", "在吗？", "嗯", " 喂 ", "嗨"):
        assert fb.guide_for(text) is fb.COACH_GUIDE_LITE, text


def test_substantive_round_uses_full_guide():
    """有实际内容的输入必须走完整版，不能为了省 token 降档。"""
    for text in (
        "我最近压力特别大",
        "我不知道要不要考研",
        "一想到要面对那么多人我就心慌",
    ):
        assert fb.guide_for(text) is fb.COACH_GUIDE, text


def test_lite_guide_not_used_when_context_demands():
    """即使输入很短，只要要收束或线索冲突，就必须用完整版。"""
    assert fb.guide_for("嗯", should_summarize=True) is fb.COACH_GUIDE
    assert fb.guide_for("嗯", modality_conflict=True) is fb.COACH_GUIDE


def test_lite_guide_keeps_safety_boundaries():
    """精简版可以省方法论，但不能省安全与边界约束。"""
    lite = fb.COACH_GUIDE_LITE
    for marker in ("不诊断心理疾病", "不要向用户宣布或解释风险等级", "语音播放"):
        assert marker in lite, marker
    assert len(lite) < len(fb.COACH_GUIDE) / 2, "精简版没有真正变短"


def test_normalize_utterance_strips_punctuation():
    assert fb.normalize_utterance("你好，在吗？") == "你好在吗"
    assert fb.normalize_utterance("  ") == ""


# ============================================================
#  6) 风险自判提示词与解析
# ============================================================
def test_risk_judge_prompt_matches_dify_rubric():
    """口径必须与 Dify「安全风险识别」节点一致，否则一致性统计没有意义。"""
    prompt = fb.RISK_JUDGE_SYSTEM_PROMPT
    for marker in (
        "low：",
        "medium：",
        "high：",
        "普通负面情绪不等于安全风险",
        "无法确定时，优先 medium",
        "以语言表达为主要依据",
    ):
        assert marker in prompt, marker


def test_risk_judge_messages_carry_recent_context():
    msgs = fb.risk_judge_messages(
        "我现在挺好的",
        recent_user_lines=["最近特别累", "不太想活了"],
        emotion_line="融合情绪：悲伤",
    )
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    assert "不太想活了" in msgs[1]["content"]
    assert "融合情绪：悲伤" in msgs[1]["content"]


def test_parse_risk_judgement_accepts_json():
    level, reason = fb.parse_risk_judgement(
        '{"risk_level": "medium", "risk_reason": "表达模糊，需要进一步确认"}'
    )
    assert level == "medium"
    assert "进一步确认" in reason


def test_parse_risk_judgement_tolerates_fences_and_case():
    level, _ = fb.parse_risk_judgement(
        '```json\n{"risk_level": "HIGH", "risk_reason": "x"}\n```'
    )
    assert level == "high"


def test_parse_risk_judgement_falls_back_to_bare_word():
    assert fb.parse_risk_judgement("low")[0] == "low"


def test_parse_risk_judgement_rejects_ambiguous_output():
    """列了多个候选等级时不可判定——宁可留空，也不写错误数据。"""
    assert fb.parse_risk_judgement("low、medium 或 high 都有可能")[0] is None
    assert fb.parse_risk_judgement("我无法判断")[0] is None
    assert fb.parse_risk_judgement("")[0] is None
