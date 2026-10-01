"""卡片知识库检索（平台侧，自动构建，零外部依赖）。

与已下线的整本书检索方案的关系
------------------------------

2026-10-01 之前用的是 `kb_service.py`：索引"整本书的正文"，切块单位是 500 字正文，
一次检索要调两次 DeepSeek（查询扩展 + 重排），慢且贵，实测召回里 18.8% 是目录与 OCR 噪声。
该方案已于同日整体下线（模块、构建脚本、单测与索引文件均已移出仓库）。

本模块索引的是"卡片"：`knowledge_base/` 下的每个 `.md` 是一张卡，
一张卡 = 一个对话场景 → 一个动作 → 一组话术。切块单位是卡片的一个 `##` 小节，
命中即可用；每个片段自带元数据（适用阶段、风险上限、主题、口语触发词、出处）。

检索只用本地 jieba + BM25，**不调用任何模型**，所以不需要并发隐藏耗时。

三点设计
--------

1. **自动构建**：索引缺失或卡片有改动时自动重建，使用者不需要跑任何命令，
   也不需要把知识库导入 Dify。
2. **风险是硬约束，阶段是软约束**：风险 ≥ MEDIUM 时只放行 L0 安全卡（安全不能让），
   阶段不匹配只降权 0.6（卡片 stage 标注带主观性，硬过滤会把好卡刷掉）。
3. **允许不注入**：纯寒暄直接返回空；分数低于门限也返回空。
   宁可不给资料，也不要塞一张不合时机的卡。

用法
----

    from app.services.ai_lab import kb_cards
    ctx = kb_cards.context_block("我明天要面试，很焦虑", stage="探索", risk="LOW")
    info = kb_cards.stats()

命令行自检：

    python -m app.services.ai_lab.kb_cards build
    python -m app.services.ai_lab.kb_cards query "我明天要面试，很焦虑"
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import pickle
import re
import threading
import time
from array import array
from collections import Counter
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_HERE = Path(__file__).resolve()
_BACKEND_ROOT = _HERE.parents[3]

#: 卡片目录：`knowledge_base/` 随代码一起发布，不依赖仓库外的路径
DEFAULT_KB_DIR = _BACKEND_ROOT / "knowledge_base"
DEFAULT_INDEX_PATH = _BACKEND_ROOT / "data" / "kb_cards_index.pkl"

BM25_K1 = 1.5
BM25_B = 0.75

#: 每轮注入的卡片数。取 2 而非 4：实测 top-1 精准，top-2 之后开始明显稀释。
DEFAULT_TOP_K = int(os.environ.get("KB_CARDS_TOP_K", "2"))
#: 相关性门限。低于该值不注入。5.0 是在 50 条评测集上标定的
#: （有效命中最低分 6.05），换语料需要重新标定。
DEFAULT_MIN_SCORE = float(os.environ.get("KB_CARDS_MIN_SCORE", "5.0"))
#: 阶段不匹配时的降权系数
STAGE_PENALTY = float(os.environ.get("KB_CARDS_STAGE_PENALTY", "0.6"))

LEVEL_ORDER = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}

#: 阶段归一化。平台的 coach_stage_service 用英文枚举
#: （opening / exploration / goal_setting / action_planning / closing），
#: 卡片为了给人读用中文。两边都必须能对上，否则阶段过滤会整体失效。
_STAGE_ALIASES = {
    "opening": "建立",
    "exploration": "探索",
    "goal_setting": "目标",
    "action_planning": "行动",
    "closing": "收束",
    "建立": "建立",
    "探索": "探索",
    "目标": "目标",
    "行动": "行动",
    "收束": "收束",
}


def normalize_stage(value: Any) -> str:
    """把英文枚举或中文标签统一成卡片用的中文阶段名。"""
    v = str(value or "").strip()
    return _STAGE_ALIASES.get(v.lower(), v)

#: 纯寒暄 / 纯应答：没有检索价值，直接不注入
_SMALLTALK_RE = re.compile(
    r"^[\s，。！？、,.!?~～…]*(?:"
    r"你好|您好|在吗|在不在|在么|hi|hello|嗨|哈喽|"
    r"谢谢|谢谢你|多谢|感谢|"
    r"好的|好|嗯|哦|噢|呃|额|啊|"
    r"ok|哈哈|呵呵|嘻嘻|"
    r"拜拜|再见|晚安|早安|早|"
    r"没事|没事了|知道了|收到"
    r")[\s啊呀吗呢吧了哦噢~～。！？!?,…]*$",
    re.I,
)

_STOPWORDS = set(
    "的 了 是 在 和 与 也 就 都 而 及 或 一个 我们 你们 他们 这 那 有 我 你 他 她 它 "
    "不 没 很 会 要 把 被 让 给 对 从 到 为 上 下 里 中 后 前 之 其 等 可以 这样 那样 "
    "什么 怎么 因为 所以 但是 如果 已经 还是 就是 只是 一些 这些 那些 the a an is are "
    "of to in and or for on with that this it be as at by".split()
)

_tokenizer = None


def kb_dir() -> Path:
    return Path(os.environ.get("KB_CARDS_DIR") or DEFAULT_KB_DIR)


def index_path() -> Path:
    return Path(os.environ.get("KB_CARDS_INDEX") or DEFAULT_INDEX_PATH)


def _get_tokenizer():
    """惰性加载 jieba（首次约 1 秒，避免拖慢后端启动）。"""
    global _tokenizer
    if _tokenizer is None:
        import jieba

        jieba.setLogLevel(logging.WARNING)
        _tokenizer = jieba
    return _tokenizer


def tokenize(text: str) -> list[str]:
    """中文分词 + 归一化：去停用词、去纯标点、英文转小写。"""
    tokens: list[str] = []
    for raw in _get_tokenizer().lcut(text or ""):
        t = raw.strip().lower()
        if not t or t in _STOPWORDS:
            continue
        if not any(ch.isalnum() or "\u4e00" <= ch <= "\u9fff" for ch in t):
            continue
        tokens.append(t)
    return tokens


def is_smalltalk(text: str) -> bool:
    """纯寒暄 / 纯应答不应触发任何资料注入。"""
    t = (text or "").strip()
    if not t:
        return True
    return bool(_SMALLTALK_RE.match(t))


# ────────────────────────────────────────────────────────────
#  front matter 解析（不依赖 PyYAML：运行时依赖里没有它）
# ────────────────────────────────────────────────────────────
def _scalar(value: str) -> Any:
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
    low = v.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    return v


def parse_card(raw: str) -> tuple[dict[str, Any], str]:
    """拆出 YAML front matter 与正文。格式受本仓库控制，故用轻量解析。"""
    lines = raw.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, raw
    close = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            close = i
            break
    if close is None:
        return {}, raw

    meta: dict[str, Any] = {}
    key: str | None = None
    for line in lines[1:close]:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("- ") and key:
            if not isinstance(meta.get(key), list):
                meta[key] = []
            meta[key].append(_scalar(s[2:]))
            continue
        if ":" not in s:
            continue
        k, v = s.split(":", 1)
        key = k.strip()
        v = v.strip()
        if not v:
            meta[key] = []
        elif v.startswith("[") and v.endswith("]"):
            inner = v[1:-1].strip()
            meta[key] = [_scalar(x) for x in inner.split(",") if x.strip()] if inner else []
        else:
            meta[key] = _scalar(v)
    return meta, "\n".join(lines[close + 1 :])


def iter_sections(body: str):
    """按 `## 小节` 切分，产出 (小节名, 正文)。"""
    parts = re.split(r"^##\s+", body, flags=re.M)
    for part in parts[1:]:
        lines = part.splitlines()
        if not lines:
            continue
        section = lines[0].strip()
        text = "\n".join(lines[1:]).strip()
        if text:
            yield section, text


def _card_files(root: Path | None = None) -> list[Path]:
    root = Path(root or kb_dir())
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.rglob("*.md")):
        if "_工具" in p.parts or p.name == "README.md":
            continue
        out.append(p)
    return out


def _signature(files: list[Path], root: Path) -> str:
    """卡片集合的指纹：文件数 + 大小 + mtime。用于判断索引是否过期。"""
    h = hashlib.md5()
    for p in files:
        try:
            st = p.stat()
        except OSError:
            continue
        h.update(f"{p.relative_to(root)}|{st.st_size}|{st.st_mtime_ns}\n".encode())
    return h.hexdigest()


# ────────────────────────────────────────────────────────────
#  构建
# ────────────────────────────────────────────────────────────
def build(root: Path | None = None, out: Path | None = None) -> dict[str, Any]:
    """扫描卡片目录并写索引。返回构建统计。"""
    root = Path(root or kb_dir())
    out = Path(out or index_path())
    files = _card_files(root)
    if not files:
        return {"cards": 0, "chunks": 0, "skipped": True, "reason": f"目录为空：{root}"}

    t0 = time.time()
    chunks: list[dict[str, Any]] = []
    postings: dict[str, array] = {}
    doc_len: list[int] = []
    problems: list[str] = []

    for path in files:
        try:
            meta, body = parse_card(path.read_text(encoding="utf-8", errors="replace"))
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{path.name}: {exc}")
            continue
        # 没有 front matter 的是说明文档，不是卡片
        if not meta:
            continue
        # 导航类文档只供人看，入库会用大量触发词盖过真正的卡片
        if meta.get("indexed") is False:
            continue
        if not meta.get("id"):
            problems.append(f"{path.name}: 缺少 id")
            continue

        trig = " ".join(meta.get("triggers") or [])
        for section, text in iter_sections(body):
            surface = f"《{meta.get('title', path.stem)}》· {section}\n{trig}\n{text}"
            toks = tokenize(surface)
            if not toks:
                continue
            doc_id = len(chunks)
            chunks.append(
                {
                    "text": text,
                    "section": section,
                    "id": meta["id"],
                    "title": meta.get("title", path.stem),
                    "layer": meta.get("layer", ""),
                    "family": meta.get("family", ""),
                    "stage": [normalize_stage(s) for s in (meta.get("stage") or [])],
                    "risk_max": meta.get("risk_max", "LOW"),
                    "themes": meta.get("themes") or [],
                    "source_book": meta.get("source_book", ""),
                }
            )
            doc_len.append(len(toks))
            for term, tf in Counter(toks).items():
                arr = postings.get(term)
                if arr is None:
                    postings[term] = array("i", [doc_id, tf])
                else:
                    arr.append(doc_id)
                    arr.append(tf)

    if not chunks:
        return {"cards": 0, "chunks": 0, "skipped": True, "reason": "没有可索引的卡片"}

    payload = {
        "version": 3,
        "built_at": t0,
        "sig": _signature(files, root),
        "root": str(root),
        "chunks": chunks,
        "postings": postings,
        "doc_len": doc_len,
        "avgdl": sum(doc_len) / len(doc_len),
        "n_docs": len(chunks),
        "n_cards": len({c["id"] for c in chunks}),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    with tmp.open("wb") as f:
        pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(out)  # 原子替换，避免并发读到半个文件

    return {
        "cards": payload["n_cards"],
        "chunks": len(chunks),
        "terms": len(postings),
        "seconds": round(time.time() - t0, 2),
        "problems": problems,
        "index_mb": round(out.stat().st_size / 1024 / 1024, 2),
    }


# ────────────────────────────────────────────────────────────
#  加载（自动检测过期）
# ────────────────────────────────────────────────────────────
_cache: dict[str, Any] = {}
_cache_lock = threading.Lock()


def reset_cache() -> None:
    """清空进程内缓存（测试或重建索引后调用）。"""
    with _cache_lock:
        _cache.clear()


def load(*, force: bool = False) -> dict[str, Any] | None:
    """加载索引；缺失或已过期时自动重建。任何失败都返回 None，不抛异常。"""
    root = kb_dir()
    p = index_path()
    key = f"{root}|{p}"
    with _cache_lock:
        if not force and key in _cache:
            return _cache[key]

        payload = None
        try:
            if p.is_file():
                with p.open("rb") as f:
                    payload = pickle.load(f)
        except Exception as exc:  # noqa: BLE001
            log.warning("卡片索引读取失败，将重建：%s", exc)
            payload = None

        try:
            files = _card_files(root)
            if not files:
                log.warning("卡片知识库目录为空：%s（检索将不注入资料）", root)
                _cache[key] = None
                return None
            sig = _signature(files, root)
        except Exception as exc:  # noqa: BLE001
            log.warning("无法扫描卡片目录：%s", exc)
            sig = None

        if payload is None or (sig is not None and payload.get("sig") != sig):
            stats = build(root, p)
            if stats.get("skipped"):
                log.warning("卡片索引重建跳过：%s", stats.get("reason"))
                _cache[key] = None
                return None
            log.info(
                "卡片知识库索引已重建：%d 张卡 / %d 片段（%.2fs）",
                stats["cards"], stats["chunks"], stats["seconds"],
            )
            try:
                with p.open("rb") as f:
                    payload = pickle.load(f)
            except Exception as exc:  # noqa: BLE001
                log.error("重建后仍无法加载索引：%s", exc)
                _cache[key] = None
                return None

        _cache[key] = payload
        return payload


def is_ready() -> bool:
    return load() is not None


# ────────────────────────────────────────────────────────────
#  检索
# ────────────────────────────────────────────────────────────
def _risk_allowed(meta: dict[str, Any], risk: str | None) -> bool:
    """风险过滤是硬约束：安全问题上没有"将就一下"的余地。"""
    if not risk:
        return True
    rq = LEVEL_ORDER.get(str(risk).upper(), 0)
    if LEVEL_ORDER.get(str(meta.get("risk_max", "LOW")).upper(), 0) < rq:
        return False
    if rq >= LEVEL_ORDER["MEDIUM"] and meta.get("layer") != "L0":
        return False
    return True


def retrieve(
    query: str,
    top_k: int | None = None,
    *,
    stage: str | None = None,
    risk: str | None = None,
    min_score: float | None = None,
    idx: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """返回命中的卡片小节，按有效分降序。无命中返回空列表。"""
    if is_smalltalk(query):
        return []
    terms = tokenize(query)
    if not terms:
        return []
    data = idx if idx is not None else load()
    if data is None:
        return []

    top_k = DEFAULT_TOP_K if top_k is None else top_k
    min_score = DEFAULT_MIN_SCORE if min_score is None else min_score
    stage = normalize_stage(stage) if stage else None
    chunks = data["chunks"]
    k1, b, avgdl, n = BM25_K1, BM25_B, data["avgdl"], data["n_docs"]

    scores: dict[int, float] = {}
    for term in set(terms):
        arr = data["postings"].get(term)
        if not arr:
            continue
        df = len(arr) // 2
        idf = math.log(1.0 + max(((n - df + 0.5) / (df + 0.5)), 1e-9))
        for i in range(0, len(arr), 2):
            doc_id, tf = arr[i], arr[i + 1]
            denom = tf + k1 * (1 - b + b * data["doc_len"][doc_id] / avgdl)
            scores[doc_id] = scores.get(doc_id, 0.0) + idf * tf * (k1 + 1) / denom

    candidates: list[tuple[float, int]] = []
    for doc_id, raw in scores.items():
        chunk = chunks[doc_id]
        if not _risk_allowed(chunk, risk):
            continue
        stages = chunk.get("stage") or []
        eff = raw if (not stage or stage in stages) else raw * STAGE_PENALTY
        if eff >= min_score:
            candidates.append((eff, doc_id))
    candidates.sort(key=lambda kv: kv[0], reverse=True)

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for eff, doc_id in candidates:
        chunk = chunks[doc_id]
        if chunk["id"] in seen:  # 同一张卡只保留最高分小节
            continue
        seen.add(chunk["id"])
        item = dict(chunk)
        item["score"] = round(eff, 4)
        out.append(item)
        if len(out) >= top_k:
            break
    return out


def context_block(
    query: str,
    top_k: int | None = None,
    *,
    stage: str | None = None,
    risk: str | None = None,
    min_score: float | None = None,
) -> str:
    """把命中卡片拼成可直接塞进提示词的参考资料；无命中返回空串。"""
    try:
        hits = retrieve(
            query, top_k, stage=stage, risk=risk, min_score=min_score
        )
    except Exception as exc:  # noqa: BLE001
        # 检索失败绝不能中断一轮通话
        log.warning("卡片检索异常，本轮不注入资料：%s", exc)
        return ""
    if not hits:
        return ""
    parts = []
    for i, h in enumerate(hits, 1):
        parts.append(
            f"[资料{i}]《{h['title']}》· {h['section']}"
            f"（适用阶段：{'/'.join(h['stage']) or '不限'}）\n{h['text']}"
        )
    return "\n\n".join(parts)


def stats() -> dict[str, Any]:
    """索引概况，供运维接口与自检脚本使用。"""
    data = load()
    if data is None:
        return {"ready": False, "kb_dir": str(kb_dir())}
    return {
        "ready": True,
        "cards": data["n_cards"],
        "chunks": data["n_docs"],
        "terms": len(data["postings"]),
        "kb_dir": data.get("root"),
        "built_at": data.get("built_at"),
        "top_k": DEFAULT_TOP_K,
        "min_score": DEFAULT_MIN_SCORE,
    }


def _main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if len(argv) >= 2 and argv[1] == "build":
        print(json.dumps(build(), ensure_ascii=False, indent=2))
        return 0
    if len(argv) >= 3 and argv[1] == "query":
        hits = retrieve(argv[2], top_k=5, min_score=0.0)
        if not hits:
            print("（无命中）")
            return 0
        for h in hits:
            print(f"[{h['score']:>7.2f}] 《{h['title']}》 · {h['section']}")
            print(f"         {h['family']} | {'/'.join(h['stage'])} | {h['risk_max']}")
            print(f"         {h['text'][:80]}")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv))
