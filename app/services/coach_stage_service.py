"""成长教练五阶段状态引擎（平台侧确定性实现）。

职责边界
--------
Dify 工作流负责**措辞与生成**；本模块负责**阶段判定**，并把结果作为
``current_stage`` / ``goal_clear`` / ``action_ready`` / ``should_summarize_hint``
回传给工作流。这样做的原因有三：

1. 判定逻辑留在本仓库，可单测、可复现、可做错误分析（对阶段一致性实验是前提）；
2. 判定结果能直接落库，成为"阶段判断与人工标签一致性"的实验数据；
3. 生成模型的版本更换不会改变阶段口径，评测结论可跨版本比较。

阶段定义（与产品说明一致）
--------------------------

    opening          开始交流，确定本次讨论的主题
    exploration      问题探索，理解事实、感受、顾虑与资源
    goal_setting     目标形成，明确本阶段希望发生的变化
    action_planning  行动规划，把目标缩小为可以开始的一步
    closing          阶段收束，整理问题、目标与下一步

判定规则
--------
规则是可解释的线索匹配，不做隐式推断，全部命中项都会写入 ``evidence``：

1. **目标线索**：用户表达希望发生的变化（"我希望""我的目标是""我打算"…）；
   出现不确定表述（"我不知道""还没想好"）时本次不计入。
2. **行动线索**：用户自己说出具体下一步（"我明天""我先""第一步"…）。
   明确的下一步行动本身即隐含方向已清楚，因此同时置 ``goal_clear``。
3. **接受线索**：用户对 AI 提出的行动表示接受（"好的""我试试"），
   仅在已有目标或已有行动线索时计入，避免"好的"被误判为已形成行动。
4. **AI 提议线索**：上一条 AI 回复里已给出具体行动建议（"可以先…""建议先…"），
   而用户尚未确认时，阶段记 ``action_planning``，表示"球在用户这边"。
5. **新顾虑线索**：最新一轮出现"但是""另外""其实我"等表述**且本轮回合没有给出新的行动**，
   则撤销 ``action_ready``，允许流程回退到探索或目标澄清（阶段可回退是产品设计的一部分）。
   注意判断只看**最新一轮**：更早轮次的行动线索不足以抵消新的顾虑。
6. **收束条件**：目标清楚且行动已形成，且已完成至少 2 轮交流。

只统计最近 ``DEFAULT_WINDOW`` 轮用户表达，避免早期线索让对话被过早收束。

阶段可达性
----------
按上述规则，一次完整的对话会依次经过
``opening → exploration → goal_setting → action_planning → closing``：
AI 提出行动建议后（``action_planning``），用户在下一轮说出的具体下一步或确认才会触发收束。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

# ── 阶段常量 ──────────────────────────────────────────────────────
STAGE_OPENING = "opening"
STAGE_EXPLORATION = "exploration"
STAGE_GOAL_SETTING = "goal_setting"
STAGE_ACTION_PLANNING = "action_planning"
STAGE_CLOSING = "closing"

STAGES: tuple[str, ...] = (
    STAGE_OPENING,
    STAGE_EXPLORATION,
    STAGE_GOAL_SETTING,
    STAGE_ACTION_PLANNING,
    STAGE_CLOSING,
)

STAGE_LABELS_CN: dict[str, str] = {
    STAGE_OPENING: "开始交流",
    STAGE_EXPLORATION: "问题探索",
    STAGE_GOAL_SETTING: "目标形成",
    STAGE_ACTION_PLANNING: "行动规划",
    STAGE_CLOSING: "阶段收束",
}

#: 参与判定的最近轮次窗口
DEFAULT_WINDOW = 6

#: 目标线索：用户表达希望发生的变化
GOAL_CUES: tuple[str, ...] = (
    "我希望",
    "我希望能够",
    "我想要",
    "我想让",
    "我想变得",
    "我想成为",
    "我想先",
    "我想尝试",
    "我的目标是",
    "我打算",
    "我决定",
    "我需要先",
    "我真正想要",
)

#: 不确定表述：出现时本轮目标线索不计入
UNCERTAINTY_CUES: tuple[str, ...] = (
    "不知道",
    "还没想好",
    "没想清楚",
    "想不清楚",
    "不确定",
    "说不好",
    "不好说",
    "没有头绪",
)

#: 行动线索：用户自己说出的具体下一步
ACTION_CUES: tuple[str, ...] = (
    "我明天",
    "我今天",
    "我今晚",
    "我这周",
    "我这个月",
    "我准备",
    "我会先",
    "我先",
    "我打算先",
    "我试着",
    "我这就",
    "接下来我",
    "第一步",
    "先做",
)

#: 接受线索：对 AI 提出行动的确认（需配合前文已有目标或行动）
ACCEPT_CUES: tuple[str, ...] = (
    "好的",
    "好啊",
    "可以",
    "我试试",
    "我试一下",
    "没问题",
    "就这么办",
    "嗯嗯",
    "我接受",
    "这个可以",
    "行吧",
)

#: 新顾虑线索：出现时允许阶段回退
#: 新顾虑线索：**只看最新一轮**，命中即撤销"行动已就绪"的判定。
#:
#: 除了转折词与追加提问，还包含用户直接抛出的新困扰。
#: 原因：``action_ready`` 由最近若干轮聚合而来、带有粘性，
#: 如果只认转折词，用户刚说出一个新问题（如"我最近总是睡不着"）
#: 会被判成"目标清楚 + 行动就绪"而直接进入收束——
#: 实测第 2 轮就触发了总结收尾，观感上像"还没聊就结束了"。
CONCERN_CUES: tuple[str, ...] = (
    # 转折与追加提问
    "但是",
    "可是",
    "不过",
    "另外",
    "还有一点",
    "还有个问题",
    "还有一个问题",
    "其实我",
    "我担心",
    # 新抛出的困扰：睡眠
    "睡不着",
    "失眠",
    "睡不好",
    "睡不够",
    "半夜醒",
    # 情绪与压力
    "很烦",
    "好烦",
    "烦躁",
    "心烦",
    "难受",
    "撑不住",
    "撑不下去",
    "扛不住",
    "熬不住",
    "压力很大",
    "压力大",
    "压力好大",
    "焦虑",
    "很累",
    "好累",
    "崩溃",
    # 动力与方向
    "提不起劲",
    "没兴趣",
    "什么都不想干",
    "不想上课",
    "不想上班",
    "没方向",
    "迷茫",
    # 人际
    "吵架",
    "不敢说",
    "不理我",
)

#: AI 行动提议线索：用于识别"已经给出建议、等待用户确认"的状态
ASSISTANT_ACTION_CUES: tuple[str, ...] = (
    "可以先",
    "建议先",
    "建议你",
    "可以试着",
    "可以试试",
    "要不要试试",
    "不妨",
    "试着",
    "第一步",
    "先试试",
)

#: 接受线索只在短句里生效，避免长段落中的"可以"被误判
_ACCEPT_MAX_LEN = 24


@dataclass(frozen=True, slots=True)
class StageDecision:
    """一次阶段判定的完整结果，可审计、可落库。"""

    stage: str
    goal_clear: bool
    action_ready: bool
    should_summarize: bool
    summary_reason: str
    evidence: list[str] = field(default_factory=list)

    @property
    def stage_label_cn(self) -> str:
        return STAGE_LABELS_CN.get(self.stage, self.stage)

    def to_dict(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "stage_label": self.stage_label_cn,
            "goal_clear": self.goal_clear,
            "action_ready": self.action_ready,
            "should_summarize": self.should_summarize,
            "summary_reason": self.summary_reason,
            "evidence": list(self.evidence),
        }


def _normalize(text: str) -> str:
    """去掉空白，便于线索匹配（保留标点，因为线索本身不含标点）。"""
    return "".join(str(text or "").split())


def _hits(text: str, cues: Sequence[str]) -> list[str]:
    return [cue for cue in cues if cue in text]


def _is_acceptance(text: str) -> bool:
    """短句接受判定：长度受限，避免长段落误命中。"""
    if not text or len(text) > _ACCEPT_MAX_LEN:
        return False
    return bool(_hits(text, ACCEPT_CUES))


def _recent_user_texts(
    history: Sequence[Mapping[str, str]],
    current_user_text: str,
    window: int,
) -> list[str]:
    """取最近 window 轮的**用户**表达（含当前这一轮）。"""
    users: list[str] = []
    if history:
        for message in history:
            role = str(message.get("role", "")).lower()
            if role == "user":
                users.append(str(message.get("content", "")))
    users.append(current_user_text or "")
    return users[-window:]


def _last_assistant_text(history: Sequence[Mapping[str, str]] | None) -> str:
    """取最近一条 AI 回复（可能为空）。"""
    if not history:
        return ""
    for message in reversed(list(history)):
        if str(message.get("role", "")).lower() == "assistant":
            return _normalize(str(message.get("content", "")))
    return ""


def decide_stage(
    history: Sequence[Mapping[str, str]] | None,
    current_user_text: str,
    *,
    turn_index: int,
    window: int = DEFAULT_WINDOW,
) -> StageDecision:
    """根据对话历史与最新用户表达判定当前阶段。

    Args:
        history: 之前的对话消息（``{"role": "user"/"assistant", "content": ...}``）。
        current_user_text: 本轮用户表达。
        turn_index: 当前轮次序号，从 1 开始。
        window: 参与判定的最近轮次数量。

    Returns:
        :class:`StageDecision`
    """
    evidence: list[str] = []
    recent = _recent_user_texts(history or [], current_user_text, window)
    latest = _normalize(recent[-1]) if recent else ""

    # 1) 目标线索（本轮出现不确定表述时不计入）
    goal_hits: list[str] = []
    for text in recent:
        normalized = _normalize(text)
        if not normalized:
            continue
        if _hits(normalized, UNCERTAINTY_CUES):
            continue
        matched = _hits(normalized, GOAL_CUES)
        if matched:
            goal_hits.extend(matched)
    goal_clear = bool(goal_hits)
    if goal_hits:
        evidence.append("goal_cue:" + ",".join(dict.fromkeys(goal_hits)))

    # 2) 行动线索（用户自己说出的具体下一步）
    action_hits: list[str] = []
    for text in recent:
        normalized = _normalize(text)
        if not normalized:
            continue
        matched = _hits(normalized, ACTION_CUES)
        if matched:
            action_hits.extend(matched)
    action_direct = bool(action_hits)
    if action_direct:
        evidence.append("action_cue:" + ",".join(dict.fromkeys(action_hits)))
        # 明确的下一步行动本身即隐含方向已经清楚
        if not goal_clear:
            goal_clear = True
            evidence.append("goal_implied_by_action")

    # 3) 接受线索（需已有目标或行动，且限短句）
    action_accepted = False
    if turn_index >= 2 and _is_acceptance(latest) and (goal_clear or action_direct):
        action_accepted = True
        evidence.append("action_accepted")

    action_ready = action_direct or action_accepted

    # 4) 新顾虑线索：撤销行动，允许回退到探索或目标澄清
    #    只依据**最新一轮**判断：更早轮次的行动线索不能抵消新出现的顾虑。
    latest_action_hit = bool(_hits(latest, ACTION_CUES))
    concern_only = bool(_hits(latest, CONCERN_CUES)) and not latest_action_hit
    if concern_only:
        if action_ready:
            action_ready = False
            evidence.append("action_revoked_by_new_concern")
        else:
            evidence.append("new_concern_raised")

    # 5) AI 已提出行动建议、用户尚未确认：球在用户这边
    assistant_proposed = bool(_hits(_last_assistant_text(history), ASSISTANT_ACTION_CUES))
    if assistant_proposed and not action_ready:
        evidence.append("assistant_action_proposed")

    # 6) 收束条件
    should_summarize = bool(goal_clear and action_ready and turn_index >= 2)

    # 7) 阶段映射
    if should_summarize:
        stage = STAGE_CLOSING
    elif action_ready or (goal_clear and assistant_proposed):
        stage = STAGE_ACTION_PLANNING
    elif goal_clear:
        stage = STAGE_GOAL_SETTING
    elif turn_index <= 1:
        stage = STAGE_OPENING
    else:
        stage = STAGE_EXPLORATION

    return StageDecision(
        stage=stage,
        goal_clear=goal_clear,
        action_ready=action_ready,
        should_summarize=should_summarize,
        summary_reason=_build_reason(stage, goal_clear, action_ready, should_summarize, concern_only),
        evidence=evidence,
    )


def _build_reason(
    stage: str,
    goal_clear: bool,
    action_ready: bool,
    should_summarize: bool,
    concern_only: bool,
) -> str:
    """生成一句可读的判定依据（写入 ``summary_reason``，长度受字段限制）。"""
    if should_summarize:
        reason = "目标已明确且已形成具体下一步，可以进入阶段收束"
    elif stage == STAGE_ACTION_PLANNING:
        reason = "目标已明确，下一步尚在形成中"
    elif stage == STAGE_GOAL_SETTING:
        reason = "核心需求逐渐清楚，正在确认希望发生的变化"
    elif stage == STAGE_OPENING:
        reason = "交流刚开始，先确定本次想讨论的主题"
    else:
        reason = "问题仍在探索阶段，目标与顾虑尚未理清"
    if concern_only:
        reason += "；本轮出现新的顾虑，继续澄清"
    return reason[:255]


def dominant_stage(stages: Sequence[str]) -> str | None:
    """统计一组阶段中出现次数最多的阶段（会话级汇总用）。"""
    if not stages:
        return None
    counts: dict[str, int] = {}
    for stage in stages:
        if stage in STAGES:
            counts[stage] = counts.get(stage, 0) + 1
    if not counts:
        return None
    # 出现次数相同时取阶段顺序更靠后的（更接近收束）
    return max(counts, key=lambda s: (counts[s], STAGES.index(s)))


__all__ = [
    "ACCEPT_CUES",
    "ACTION_CUES",
    "ASSISTANT_ACTION_CUES",
    "CONCERN_CUES",
    "DEFAULT_WINDOW",
    "GOAL_CUES",
    "STAGES",
    "STAGE_ACTION_PLANNING",
    "STAGE_CLOSING",
    "STAGE_EXPLORATION",
    "STAGE_GOAL_SETTING",
    "STAGE_LABELS_CN",
    "STAGE_OPENING",
    "StageDecision",
    "UNCERTAINTY_CUES",
    "decide_stage",
    "dominant_stage",
]
