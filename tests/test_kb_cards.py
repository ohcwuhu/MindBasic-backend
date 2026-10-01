"""卡片知识库检索测试。

锁定四类行为：
1. front matter 解析与阶段归一化（平台用英文枚举，卡片用中文）；
2. 风险是硬约束、阶段是软约束；
3. 寒暄与低分不注入；
4. 卡片改动后索引自动重建，不需要人工跑构建命令。
"""

from pathlib import Path

import pytest

from app.services.ai_lab import kb_cards


_CARD_TMPL = """---
id: {cid}
layer: {layer}
family: 测试家族
title: {title}
source_book: 测试用书
source_author: 测试
themes: [测试]
stage: [{stage}]
risk_max: {risk}
when: 测试触发条件
avoid_when: 无
triggers: [{triggers}]
intensity: 低
---
## 一句话原理

{principle}

## 引导语（可直接说）

{script}
"""


def _write_card(root: Path, cid: str, title: str, **kw) -> Path:
    kw.setdefault("layer", "L1")
    kw.setdefault("stage", "探索")
    kw.setdefault("risk", "LOW")
    kw.setdefault("triggers", "睡不着")
    kw.setdefault("principle", "入睡困难常常来自睡前认知唤醒。")
    kw.setdefault("script", "我们先把今晚要做的事放到明天。")
    path = root / f"{cid}.md"
    path.write_text(_CARD_TMPL.format(cid=cid, title=title, **kw), encoding="utf-8")
    return path


@pytest.fixture
def kb(tmp_path, monkeypatch):
    """把卡片目录与索引指到临时位置，避免污染真实知识库。"""
    root = tmp_path / "kb"
    root.mkdir()
    monkeypatch.setenv("KB_CARDS_DIR", str(root))
    monkeypatch.setenv("KB_CARDS_INDEX", str(tmp_path / "idx.pkl"))
    kb_cards.reset_cache()
    yield root
    kb_cards.reset_cache()


def test_parse_card_splits_front_matter_and_body():
    raw = _CARD_TMPL.format(
        cid="x-1", layer="L1", title="测试卡", stage="探索", risk="LOW",
        triggers="睡不着", principle="原理", script="话术",
    )
    meta, body = kb_cards.parse_card(raw)
    assert meta["id"] == "x-1"
    assert meta["stage"] == ["探索"]
    assert meta["triggers"] == ["睡不着"]
    assert "一句话原理" in body
    assert not body.lstrip().startswith("---")


def test_front_matter_absent_returns_empty_meta():
    meta, body = kb_cards.parse_card("# 只是一篇说明文档\n正文")
    assert meta == {}
    assert "说明文档" in body


def test_normalize_stage_maps_platform_enum_to_card_labels():
    # 平台 coach_stage_service 用的是英文枚举
    assert kb_cards.normalize_stage("opening") == "建立"
    assert kb_cards.normalize_stage("exploration") == "探索"
    assert kb_cards.normalize_stage("goal_setting") == "目标"
    assert kb_cards.normalize_stage("action_planning") == "行动"
    assert kb_cards.normalize_stage("closing") == "收束"
    # 已经是中文时保持不变
    assert kb_cards.normalize_stage("探索") == "探索"


def test_build_indexes_cards_and_skips_docs(kb):
    _write_card(kb, "sleep-1", "睡眠节律调整", triggers="睡不着")
    (kb / "README.md").write_text("# 说明\n不是卡片", encoding="utf-8")

    stats = kb_cards.build()
    assert stats["cards"] == 1
    assert stats["problems"] == []


def test_retrieve_hits_on_topic_card(kb):
    _write_card(kb, "sleep-1", "睡眠节律调整", triggers="睡不着, 失眠")
    _write_card(kb, "team-1", "团队沟通反馈", triggers="给反馈, 跨部门",
                principle="先对齐目标。")

    hits = kb_cards.retrieve("我最近老是睡不着", min_score=0.0)
    assert hits
    assert hits[0]["id"] == "sleep-1"


def test_risk_is_hard_filter(kb):
    """风险达到 MEDIUM 时，只有 L0 安全卡能放行。"""
    _write_card(kb, "sleep-1", "睡眠节律调整", triggers="睡不着", layer="L1", risk="LOW")
    _write_card(kb, "safe-1", "危机信号识别", triggers="睡不着", layer="L0", risk="HIGH")

    low = kb_cards.retrieve("睡不着", risk="LOW", min_score=0.0)
    assert {h["id"] for h in low} == {"sleep-1", "safe-1"}

    mid = kb_cards.retrieve("睡不着", risk="MEDIUM", min_score=0.0)
    assert [h["id"] for h in mid] == ["safe-1"]


def test_stage_is_soft_filter_not_hard(kb):
    """阶段不匹配只降权：分高的卡仍应赢过阶段匹配的低分卡。"""
    _write_card(kb, "far-1", "高相关卡", stage="行动",
                triggers="睡不着, 失眠, 躺下, 工作",
                principle="一躺下就开始盘算明天的工作，越想越睡不着。")
    _write_card(kb, "near-1", "低相关卡", stage="探索", triggers="别的")

    hits = kb_cards.retrieve("一躺下就想工作，睡不着", stage="exploration", min_score=0.0)
    assert hits and hits[0]["id"] == "far-1"


def test_smalltalk_and_empty_query_inject_nothing(kb):
    _write_card(kb, "sleep-1", "睡眠节律调整", triggers="睡不着")
    for q in ["你好", "在吗", "谢谢", "嗯", "   ", ""]:
        assert kb_cards.retrieve(q, min_score=0.0) == []
    assert kb_cards.context_block("你好") == ""


def test_low_score_below_threshold_injects_nothing(kb):
    _write_card(kb, "sleep-1", "睡眠节律调整", triggers="睡不着")
    # 门限高于任何命中时应返回空，而不是硬塞一条
    assert kb_cards.retrieve("睡不着", min_score=999.0) == []


def test_index_rebuilds_automatically_when_cards_change(kb):
    _write_card(kb, "sleep-1", "睡眠节律调整", triggers="睡不着")
    kb_cards.reset_cache()
    assert kb_cards.load()["n_cards"] == 1

    _write_card(kb, "team-1", "团队沟通反馈", triggers="给反馈",
                principle="先对齐目标再讨论方案。")
    kb_cards.reset_cache()
    # 没有跑任何构建命令，索引应自己发现卡片变了并重建
    assert kb_cards.load()["n_cards"] == 2


def test_missing_kb_dir_degrades_to_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("KB_CARDS_DIR", str(tmp_path / "not-there"))
    monkeypatch.setenv("KB_CARDS_INDEX", str(tmp_path / "idx.pkl"))
    kb_cards.reset_cache()
    try:
        assert kb_cards.load() is None
        assert kb_cards.is_ready() is False
        assert kb_cards.context_block("睡不着") == ""
        assert kb_cards.stats()["ready"] is False
    finally:
        kb_cards.reset_cache()


def test_context_block_formats_sources(kb):
    _write_card(kb, "sleep-1", "睡眠节律调整", triggers="睡不着")
    block = kb_cards.context_block("睡不着", min_score=0.0)
    assert block.startswith("[资料1]")
    assert "睡眠节律调整" in block
    assert "适用阶段" in block
