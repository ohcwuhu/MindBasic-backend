"""五阶段状态引擎测试：保证阶段、目标、行动与收束判定符合产品定义。

用例只使用 ``assert``，便于在无 pytest 环境下直接调用。
"""

from app.services import coach_stage_service as stage


def _history(*user_texts: str) -> list[dict[str, str]]:
    """构造只含用户表达的对话历史。"""
    return [{"role": "user", "content": text} for text in user_texts]


# ============================================================
#  基础阶段推进
# ============================================================
def test_first_turn_vague_input_is_opening():
    """第一轮没有明确线索时停在开始交流，不猜测目标。"""
    decision = stage.decide_stage([], "最近有点烦", turn_index=1)
    assert decision.stage == stage.STAGE_OPENING
    assert decision.goal_clear is False
    assert decision.action_ready is False
    assert decision.should_summarize is False


def test_unclear_goal_stays_in_exploration():
    """多轮仍说不清目标时保持探索，不允许过早收束。"""
    history = _history("我最近挺纠结的", "说不上来，就是觉得没方向")
    decision = stage.decide_stage(history, "我也说不清想要什么", turn_index=3)
    assert decision.stage == stage.STAGE_EXPLORATION
    assert decision.should_summarize is False


def test_goal_cue_moves_to_goal_setting():
    """出现目标线索后进入目标形成阶段。"""
    history = _history("我不知道要不要考研")
    decision = stage.decide_stage(history, "我希望先把这件事想清楚", turn_index=2)
    assert decision.stage == stage.STAGE_GOAL_SETTING
    assert decision.goal_clear is True
    assert decision.action_ready is False
    assert any("goal_cue" in item for item in decision.evidence)


def test_action_cue_implies_goal_when_no_goal_stated():
    """只有具体行动、没有明确目标时，行动本身即隐含方向已清楚。"""
    decision = stage.decide_stage([], "我明天先列一下可选方向", turn_index=1)
    assert decision.action_ready is True
    assert decision.goal_clear is True
    assert "goal_implied_by_action" in decision.evidence
    assert decision.stage == stage.STAGE_ACTION_PLANNING


def test_goal_then_action_reaches_closing():
    """目标已明确，随后给出具体下一步即进入收束。"""
    history = _history("我希望先确定方向")
    decision = stage.decide_stage(history, "我明天先列一下可选方向", turn_index=2)
    assert decision.stage == stage.STAGE_CLOSING
    assert decision.should_summarize is True


def test_assistant_proposal_moves_to_action_planning():
    """AI 已给出行动建议、用户尚未确认时，阶段为行动规划。"""
    history = [
        {"role": "user", "content": "我希望把作息调整过来"},
        {"role": "assistant", "content": "可以先从固定起床时间开始，第一周只做这一件事。"},
    ]
    decision = stage.decide_stage(history, "听起来有点难", turn_index=3)
    assert decision.stage == stage.STAGE_ACTION_PLANNING
    assert decision.action_ready is False
    assert "assistant_action_proposed" in decision.evidence
    assert decision.should_summarize is False


def test_goal_and_action_reach_closing():
    """目标清楚且行动形成后进入收束。"""
    history = _history("我希望先把方向定下来", "我想试着先做一件小事")
    decision = stage.decide_stage(history, "我打算先写一版方案", turn_index=3)
    assert decision.stage == stage.STAGE_CLOSING
    assert decision.should_summarize is True
    assert decision.summary_reason


def test_first_turn_action_does_not_summarize():
    """第一轮即使说了行动也不收束（至少需要两轮交流）。"""
    decision = stage.decide_stage([], "我明天先列个清单", turn_index=1)
    assert decision.action_ready is True
    assert decision.should_summarize is False
    assert decision.stage == stage.STAGE_ACTION_PLANNING


# ============================================================
#  不确定表述与接受线索
# ============================================================
def test_uncertainty_cue_blocks_goal():
    """出现"还没想好"这类表述时，同一轮的目标线索不计入。"""
    decision = stage.decide_stage(
        [], "我希望变得更好，但我还没想好具体要什么", turn_index=2,
    )
    assert decision.goal_clear is False
    assert decision.stage == stage.STAGE_EXPLORATION


def test_acceptance_requires_prior_goal():
    """第一轮的"好的"不能算作已形成行动，避免误收束。"""
    decision = stage.decide_stage([], "好的", turn_index=1)
    assert decision.action_ready is False
    assert decision.stage == stage.STAGE_OPENING


def test_acceptance_after_goal_sets_action_ready():
    """已有目标时，用户接受建议视为行动已形成。"""
    history = _history("我希望把作息调整过来")
    decision = stage.decide_stage(history, "好的，我试试", turn_index=2)
    assert decision.action_ready is True
    assert "action_accepted" in decision.evidence
    assert decision.stage == stage.STAGE_CLOSING


def test_long_text_with_acceptance_word_is_not_acceptance():
    """长段落里出现"可以"不算接受，避免误判。"""
    long_text = (
        "我可以说很多方面的问题，比如最近作息不好、和室友关系一般、"
        "还有课程压力也比较大，我想先想想从哪件事开始比较好"
    )
    decision = stage.decide_stage([], long_text, turn_index=2)
    assert "action_accepted" not in decision.evidence


# ============================================================
#  阶段回退
# ============================================================
def test_new_concern_revokes_action_and_allows_fallback():
    """新一轮出现顾虑时撤销行动判定，回到目标形成阶段。"""
    history = _history(
        "我希望先把方向定下来",
        "我打算先写一版方案",
    )
    decision = stage.decide_stage(history, "但是我还有个问题，我担心时间不够", turn_index=4)
    assert decision.action_ready is False
    assert "action_revoked_by_new_concern" in decision.evidence
    assert decision.stage == stage.STAGE_GOAL_SETTING
    assert "继续澄清" in decision.summary_reason


def test_new_problem_without_connector_also_revokes_action():
    """用户直接抛出新困扰（不带"但是/不过"）时，同样要撤销行动判定。

    回归用例：实测中第 1 轮由"我明天要去面试"确立了行动，
    第 2 轮用户改说"我最近总是睡不着"，旧逻辑因为新困扰不含转折词，
    判成"目标清楚 + 行动就绪"而立刻收束，输出一段总结收尾语——
    用户才说了两句话，对话就被总结掉了。
    """
    history = _history("我明天要去参加一个特别重要的面试，一想到要面对那么多人就心慌")
    decision = stage.decide_stage(
        history, "我最近总是睡不着，一躺下就开始想工作上的事情", turn_index=2,
    )
    assert decision.action_ready is False
    assert "action_revoked_by_new_concern" in decision.evidence
    assert decision.should_summarize is False
    assert decision.stage == stage.STAGE_GOAL_SETTING


def test_window_limits_history_influence():
    """超出窗口的早期线索不再影响判定。"""
    history = _history("我希望早点定下来") + _history(
        "还是没头绪", "说不清楚", "再想想", "不知道从哪开始", "有点乱",
    )
    decision = stage.decide_stage(history, "还是说不清楚", turn_index=8, window=3)
    assert decision.goal_clear is False
    assert decision.stage == stage.STAGE_EXPLORATION


# ============================================================
#  输出结构
# ============================================================
def test_decision_to_dict_is_json_ready():
    """判定结果可直接落库与回传，字段齐全。"""
    decision = stage.decide_stage([], "我希望先想清楚", turn_index=1)
    payload = decision.to_dict()
    for key in ("stage", "stage_label", "goal_clear", "action_ready",
                "should_summarize", "summary_reason", "evidence"):
        assert key in payload
    assert isinstance(payload["evidence"], list)
    assert payload["stage_label"] == stage.STAGE_LABELS_CN[payload["stage"]]


def test_summary_reason_within_column_limit():
    """summary_reason 写入 VARCHAR(255)，必须受限。"""
    decision = stage.decide_stage([], "我希望改变，但是我有顾虑", turn_index=3)
    assert len(decision.summary_reason) <= 255


def test_dominant_stage_prefers_more_advanced_stage_on_tie():
    """出现次数相同时取更接近收束的阶段。"""
    assert stage.dominant_stage([]) is None
    assert stage.dominant_stage(["exploration", "exploration", "closing"]) == "exploration"
    assert stage.dominant_stage(["exploration", "closing"]) == "closing"
    assert stage.dominant_stage(["unknown-stage"]) is None
