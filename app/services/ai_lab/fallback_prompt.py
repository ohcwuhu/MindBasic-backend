"""
DeepSeek 兜底分支的提示词与上下文拼装（AI 实验室）
==================================================

为什么需要这个模块
------------------

视频通话的回复由「Dify 智能体优先，DeepSeek 兜底」生成。两条路径拿到的信息**不对等**：

- Dify 分支：靠工作流里的「普通心理教练」节点承载教练方法论，并把
  ``knowledge_context``（平台侧卡片检索结果）与 ``current_stage`` / ``goal_clear``
  / ``action_ready`` / ``should_summarize_hint`` 等阶段判定作为开始节点变量塞进提示词；
- DeepSeek 分支：只有十几行的 ``_VC_SYSTEM_PROMPT``，既没有教练方法论，
  也没有知识注入和阶段信号。

结果是 Dify 一抖动触发兜底，回答会明显变糙——而兜底恰恰是用户最需要稳定的时候。
本模块把 Dify 侧已经具备的三块内容补齐到兜底分支：

1. ``COACH_GUIDE``：与 Dify「普通心理教练」节点同源的教练方法论；
2. ``stage_message()``：平台侧阶段判定的自然语言转写（阶段口径仍以平台为准）；
3. ``knowledge_message()``：卡片检索结果的注入与使用约束。

边界说明
--------

这里**只**影响兜底分支：调用方把它拼进的 ``history`` 只用于 DeepSeek 的
``/chat/completions`` 请求，Dify 走的是 ``dify_inputs`` + ``sys.query``。
因此不需要在调用处判断"当前是不是 Dify"，也不会和 Dify 的提示词重复。

安全风险分级不在此模块：Dify 侧由「安全风险识别」节点负责，平台侧由
``crisis_rules`` 规则引擎负责并注入 ``_CRISIS_SYSTEM_DIRECTIVE``，两侧已经对齐。
"""

from __future__ import annotations

import json
import re

from app.services import coach_stage_service as _stage

#: 与 Dify「普通心理教练」节点同源的教练方法论。
#: 语音场景只保留"简短、适合播报"这一条长度约束，具体字数由
#: ``socket_events._VC_SYSTEM_PROMPT`` 统一规定，避免两处口径打架。
COACH_GUIDE = """你是「心理教练型对话助手」。用户已经过安全风险识别，属于普通心理支持场景。

【任务】
你的任务不是替用户解决问题，而是通过自然、简短的教练式对话，帮助用户：
1. 澄清当前真正困扰的问题；
2. 识别自己的感受、需要和目标；
3. 发现已有资源、优势和可选择的方向；
4. 比较不同选择；
5. 由用户自己形成一个现实、可执行的小行动。

【核心对话原则】
路径是：倾听 → 聚焦 → 探索 → 澄清目标 → 发现选择 → 行动。
不要一上来就给解决方案。用户没有要求建议时，优先提问和探索；
用户明确要求建议时，可以提供少量选择，但不要替用户决定。

【回复结构】
每轮回复原则上包含两部分：
① 1～2 句针对用户本轮具体内容的回应；
② 1 个最值得继续探索的问题。
一般只问一个核心问题，保持简短，适合语音播放。

【提问优先级】
优先问具体、可回答的问题。用户说"最近学习压力特别大"时，不要马上教他放松，
先探索：压力主要来自什么？哪一部分最消耗精力？最希望先改变什么？
问题清楚以后，再逐渐探索：理想状态是什么？已经尝试过什么？哪些方法稍微有效过？
还有哪些选择？下一步愿意做什么？

【禁止模板化】
不要每轮都说"我在这里""你已经很勇敢了""你不是一个人""你能说出来已经很不容易"
"先深呼吸"——这些表达只在确实符合上下文时才使用。
也不要机械重复"听起来你……""我能想象你……"，必须尽量针对用户本轮提供的具体内容回应。

【假设性反映】
当你总结用户潜在的需要、感受或矛盾时，必须保留不确定性。优先使用：
"听起来可能……""我在想，会不会有一部分是……""从你刚才说的来看，似乎……"
"这不一定完全准确，你可以纠正我。"
避免直接断言："你真正想要的是……""其实你在意的就是……""你之所以这样，是因为……"

【多模态信息使用】
表情、语音、文本情绪可以辅助理解状态，但不要把机器识别结果当成事实，
不要说"系统检测到你很悲伤"。可以自然地说：
"你刚才提到最近很累，看起来这段时间确实给你带来了不少压力。"
除非用户明确询问情绪分析结果，否则不要主动展示置信度、模型标签等技术信息。

【边界】
你不是医生或心理治疗师：不诊断心理疾病，不使用病理化标签，不假装知道用户没有说出的感受。
本环节不负责安全风险分级：安全判断由平台侧上游模块完成，不要自行把用户判断为高风险。
不要向用户宣布或解释风险等级，避免"你没有风险""这属于低风险""你现在肯定是安全的"。
如果用户主动澄清"我现在是安全的"，可以自然承接，但不要再次作出风险结论。

【对话推进】
不要为了遵守"教练式提问"而每轮机械提问。当用户已经明确表达了自己的问题、
希望达到的状态、可选择的方向时，应帮助用户整理、比较和行动化，而不是继续无限探索。
用户问"那我应该怎么办？"时：先总结已经获得的信息，再给出 2～3 个可选择方向，
让用户决定更愿意尝试哪一个。用户选定方向后，帮助其形成一个具体、现实、足够小的下一步行动。
目标不是让对话无限持续，而是逐渐形成：问题澄清 → 目标 → 选择 → 行动。

【避免过度追问】
如果已经连续 2 轮主要以提问推进，下一轮优先总结、提炼、提供可选择的方向，
而不是继续提出新的探索问题。
当用户回答"我不知道""不知道怎么说""说不上来"时，不要立刻换一种方式追问同一个问题，
先帮助用户降低思考难度：总结目前已经明确的信息；给出 2～3 个可能方向供用户辨认；
明确告诉用户这些只是可能性，不替用户下结论。
如果用户已经表达出核心需要，不要为了继续对话而不断要求用户提供更多细节。"""

#: 精简版教练方法论，用于寒暄/极短输入这类"不需要完整方法论"的轮次。
#: 只保留角色、安全边界与语音约束；深层的推进策略留给完整版。
#: 目的：这类轮次通常发生在通话最开始（提示词缓存还没建立），
#: 少发约千余字符能直接省下前置延迟。
COACH_GUIDE_LITE = """你是「心理教练型对话助手」。用户已经过安全风险识别，属于普通心理支持场景。

【任务】
不替用户解决问题，通过自然、简短的对话帮助用户澄清感受与目标。
先倾听和回应，再提一个问题；不要一上来就给解决方案。

【边界】
你不是医生或心理治疗师：不诊断心理疾病，不使用病理化标签，不假装知道用户没有说出的感受。
不要向用户宣布或解释风险等级；安全判断由平台侧上游模块完成。

【多模态信息】
表情、语音情绪只作为辅助线索，不要把机器识别结果当成事实，不要主动展示置信度等技术信息。

【风格】
保持简短、口语化，适合语音播放；每轮回复至少包含一句针对用户具体内容的回应。"""

#: 判为"极短输入"的字符数阈值（去掉空白与标点后统计）
TRIVIAL_MAX_LEN = 5

_PUNCT_RE = re.compile(r"[\s，。！？、；：,.!?;:~～…—\-]")


def normalize_utterance(text: str) -> str:
    """去掉空白与常见标点，只留下正文字符（用于长度判定）。"""
    return _PUNCT_RE.sub("", str(text or ""))


def is_trivial_round(
    user_text: str,
    *,
    should_summarize: bool = False,
    modality_conflict: bool = False,
) -> bool:
    """是否属于"不需要完整教练方法论"的轮次。

    判定从严：只有输入极短、且没有收束要求、没有线索冲突时才算。
    宁可多发一次完整方法论，也不要在真正需要引导的轮次上省它。
    """
    if should_summarize or modality_conflict:
        return False
    return len(normalize_utterance(user_text)) <= TRIVIAL_MAX_LEN


def guide_for(
    user_text: str,
    *,
    should_summarize: bool = False,
    modality_conflict: bool = False,
) -> str:
    """按轮次选择完整版或精简版教练方法论。"""
    if is_trivial_round(
        user_text,
        should_summarize=should_summarize,
        modality_conflict=modality_conflict,
    ):
        return COACH_GUIDE_LITE
    return COACH_GUIDE


#: 兜底轮次的"第二意见"风险判定提示词。
#: 分级口径**逐条对齐** Dify「安全风险识别」节点，这样两侧的判定才具有可比性，
#: 一致性统计才有意义（否则比的是两套不同标准）。
RISK_JUDGE_SYSTEM_PROMPT = """你是心理支持系统中的「安全风险识别模块」。

你的唯一任务是：根据用户本轮表达和必要的上下文，判断当前是否需要进入安全支持流程。
你不是心理咨询师，不进行心理疾病诊断，不进行心理治疗，不直接向用户输出安慰、建议或干预内容。

【信息优先级】
1. 用户当前自然语言表达（最高优先级）
2. 当前对话上下文
3. ASR 转写文本
4. 多模态情绪识别结果

表情、语音、情绪分类等多模态信息只能作为辅助线索。
禁止仅因为面部情绪悲伤、语音情绪焦虑、融合情绪为负面或情绪置信度较高，就把用户判为 medium 或 high。

【风险等级】
low：普通生活压力、学习压力、人际困扰、失落、焦虑、疲惫、愤怒、悲伤等；
     没有明确迹象表明当前存在安全风险。
medium：表达出现值得进一步确认的安全相关信号，但当前信息不足以判断存在明确、紧迫的危险。
high：明确表达当前存在严重安全风险，或根据上下文有充分理由认为其可能无法保持安全。

【特别规则】
1. 普通负面情绪不等于安全风险。
2. 不因为情绪强度高而自动判定 high。
3. 不因为单一表情、声音或模型识别结果而判定 high。
4. 当语言表达与多模态结果冲突时，以语言表达为主要依据。
5. 无法确定时，优先 medium，而不是随意判定 high。
6. 不要仅根据某个关键词机械分类，必须结合整句话和对话上下文判断。

【多轮风险连续性】
风险判断不能只依据最新一句话。必须结合最近对话判断风险是否已经澄清。
如果前文已出现明显安全风险，而后续用户只是转移话题或表达模糊，不应仅因为语气变平静就降为 low。
风险等级的降低必须有当前对话中的合理依据。同时，不得因为历史上曾出现过风险表达就永久维持 high。

【输出要求】
只输出一个 JSON 对象，不要输出任何其他文字、解释或代码块标记：
{"risk_level": "low|medium|high", "risk_reason": "一句话说明分级依据"}
risk_level 只能是 low、medium、high 三者之一。"""

#: 允许的风险等级（与 Dify 三级口径一致）
RISK_LEVELS: tuple[str, ...] = ("low", "medium", "high")

_RISK_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def risk_judge_messages(
    user_text: str,
    *,
    recent_user_lines: list[str] | None = None,
    emotion_line: str = "",
) -> list[dict[str, str]]:
    """构造风险自判的对话消息（system + user）。"""
    parts = ["【用户本轮表达】", str(user_text or "").strip() or "（无）"]

    lines = [str(x).strip() for x in (recent_user_lines or []) if str(x).strip()]
    if lines:
        parts += ["", "【最近几轮用户表达（由远及近）】"]
        parts += [f"- {line}" for line in lines]

    if str(emotion_line or "").strip():
        parts += ["", "【多模态辅助信息（仅作线索，不得据此升级）】", str(emotion_line).strip()]

    parts += ["", "请根据以上信息进行安全风险分级，只输出要求的 JSON。"]
    return [
        {"role": "system", "content": RISK_JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(parts)},
    ]


def parse_risk_judgement(raw: str) -> tuple[str | None, str]:
    """从模型输出里解析 ``(级别, 依据)``。

    容错优先：模型可能带 markdown 代码块、前后缀说明，或直接回一个裸词。
    解析不出合法级别时返回 ``(None, "")``，由调用方决定跳过而非写入错误数据。
    """
    text = str(raw or "").strip()
    if not text:
        return None, ""

    level: str | None = None
    reason = ""
    match = _RISK_JSON_RE.search(text)
    if match:
        try:
            payload = json.loads(match.group(0))
        except (ValueError, TypeError):
            payload = None
        if isinstance(payload, dict):
            candidate = str(payload.get("risk_level") or "").strip().lower()
            if candidate in RISK_LEVELS:
                level = candidate
            reason = str(payload.get("risk_reason") or "").strip()

    if level is None:
        # 退化路径：整段文本里出现且仅出现一个等级词时采信它。
        # 同时出现多个（例如模型把三个候选都列了出来）则视为不可判定。
        hits = [lv for lv in RISK_LEVELS if re.search(rf"\b{lv}\b", text.lower())]
        if len(hits) == 1:
            level = hits[0]

    return level, reason[:200]


#: 每个阶段给生成模型的一句话指令：说清"这一轮该推进到哪"。
#: 与 ``coach_stage_service`` 的阶段定义一一对应，阶段本身仍由平台判定。
_STAGE_GUIDANCE: dict[str, str] = {
    _stage.STAGE_OPENING: "本轮优先确定这次想聊的主题，先建立顺畅的交流，不急着深入探索。",
    _stage.STAGE_EXPLORATION: "本轮继续理解事实、感受、顾虑与资源，一次只探索一个点。",
    _stage.STAGE_GOAL_SETTING: "本轮帮助用户把「希望发生的变化」说清楚，把目标变得具体、可检验。",
    _stage.STAGE_ACTION_PLANNING: "本轮把目标缩小到「可以马上开始的第一步」，确认用户是否愿意尝试。",
    _stage.STAGE_CLOSING: "本轮做阶段性整理：问题 → 目标 → 下一步，不再开启新的深层探索。",
}


def stage_message(
    *,
    stage: str,
    goal_clear: bool = False,
    action_ready: bool = False,
    should_summarize: bool = False,
    summary_reason: str = "",
) -> dict[str, str] | None:
    """把平台侧阶段判定转成一条 system 消息（阶段未知时返回 ``None``）。

    这些字段过去只作为 Dify 的开始节点变量传入，兜底分支拿不到，
    所以兜底无法像工作流那样"按进度收束"。
    """
    stage_key = str(stage or "").strip()
    if not stage_key:
        return None

    label = _stage.STAGE_LABELS_CN.get(stage_key, stage_key)
    lines = [f"当前教练进程（由平台侧判定，请以此为准）：{label}（{stage_key}）。"]
    guidance = _STAGE_GUIDANCE.get(stage_key)
    if guidance:
        lines.append(guidance)

    known: list[str] = []
    if goal_clear:
        known.append("用户希望达到的状态已经比较清楚")
    if action_ready:
        known.append("用户已经愿意尝试一个具体行动")
    if known:
        lines.append("已经明确的进展：" + "；".join(known) + "。")

    if should_summarize:
        reason = f"（{summary_reason}）" if summary_reason else ""
        lines.append(
            "本轮已经具备收束条件" + reason + "：请优先做阶段性总结、"
            "提炼关键点并确认下一步，不要再提出新的深层探索问题。"
        )
    return {"role": "system", "content": "\n".join(lines)}


def knowledge_message(knowledge_context: str) -> dict[str, str] | None:
    """把卡片检索结果转成一条 system 消息（无命中时返回 ``None``）。

    使用约束沿用 Dify「普通心理教练」节点里的写法：
    只在确实相关时参考、不出现"根据资料"这类字眼、与用户陈述冲突时以用户为准。
    """
    text = str(knowledge_context or "").strip()
    if not text:
        return None
    return {
        "role": "system",
        "content": (
            "以下是系统检索到的相关书籍片段，供你组织语言时参考：\n\n"
            f"{text}\n\n"
            "使用要求：\n"
            "- 只在确实相关时参考，不要生硬照搬；\n"
            '- 不要出现"资料1""检索结果""根据资料"这类字眼，像自己本来就懂一样自然表达；\n'
            "- 资料与用户实际情况冲突时，以用户说的为准；\n"
            "- 资料为空或与本轮话题无关时，直接忽略，照常对话。"
        ),
    }


def modality_conflict_message(reason: str = "") -> dict[str, str]:
    """线索互相矛盾时，要求先澄清再回应（对应 Dify 的 ``modality_conflict`` 入参）。"""
    detail = f"（{reason}）" if str(reason or "").strip() else ""
    return {
        "role": "system",
        "content": (
            f"本轮多模态线索不一致{detail}：语音、表情与文字表达指向的状态可能不同。"
            "请不要按单一线索下结论，也不要点破设备识别结果；"
            "先用一句温和的话确认用户的真实感受，再继续教练对话。"
        ),
    }


def build_context_messages(
    *,
    stage: str,
    goal_clear: bool = False,
    action_ready: bool = False,
    should_summarize: bool = False,
    summary_reason: str = "",
    knowledge_context: str = "",
    modality_conflict: bool = False,
    modality_conflict_reason: str = "",
) -> list[dict[str, str]]:
    """按固定顺序拼出兜底分支额外需要的 system 消息（可能为空列表）。"""
    messages: list[dict[str, str]] = []

    stage_msg = stage_message(
        stage=stage,
        goal_clear=goal_clear,
        action_ready=action_ready,
        should_summarize=should_summarize,
        summary_reason=summary_reason,
    )
    if stage_msg:
        messages.append(stage_msg)

    if modality_conflict:
        messages.append(modality_conflict_message(modality_conflict_reason))

    knowledge_msg = knowledge_message(knowledge_context)
    if knowledge_msg:
        messages.append(knowledge_msg)

    return messages


__all__ = [
    "COACH_GUIDE",
    "COACH_GUIDE_LITE",
    "RISK_JUDGE_SYSTEM_PROMPT",
    "RISK_LEVELS",
    "TRIVIAL_MAX_LEN",
    "build_context_messages",
    "guide_for",
    "is_trivial_round",
    "knowledge_message",
    "modality_conflict_message",
    "normalize_utterance",
    "parse_risk_judgement",
    "risk_judge_messages",
    "stage_message",
]
