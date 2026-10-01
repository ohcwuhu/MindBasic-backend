"""把卡片知识库从编辑目录同步到后端运行时目录，并重建索引。

卡片在 `项目主体/心理教练知识库-自建/` 里编辑（人看的地方），
运行时代码读的是 `MindBasic-backend/knowledge_base/`（随代码发布的地方）。
本脚本把前者同步到后者，两边不需要手工复制。

索引本身会在后端首次检索时自动构建，这里顺带重建一次便于当场验证。

用法：

    python scripts/sync_kb_cards.py
    python scripts/sync_kb_cards.py --src D:/somewhere/else
    python scripts/sync_kb_cards.py --dry-run
"""

from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_BACKEND_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))

#: 默认编辑目录：项目主体/心理教练知识库-自建
DEFAULT_SRC = _BACKEND_ROOT.parents[1] / "心理教练知识库-自建"
DEFAULT_DST = _BACKEND_ROOT / "knowledge_base"

#: 只同步内容目录，不搬运 _工具（开发工具，不进运行时）
CONTENT_DIRS = ("01-安全与边界", "02-技法卡", "03-阶段映射", "04-主题索引", "05-场景入口", "_规范")
CONTENT_FILES = ("README.md",)


def _iter_source_files(src: Path):
    for name in CONTENT_FILES:
        p = src / name
        if p.is_file():
            yield p
    for d in CONTENT_DIRS:
        base = src / d
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if p.is_file() and p.suffix == ".md":
                yield p


def sync(src: Path, dst: Path, *, dry_run: bool = False) -> dict:
    if not src.is_dir():
        raise FileNotFoundError(f"源目录不存在：{src}")

    added: list[str] = []
    updated: list[str] = []
    unchanged = 0
    for s in _iter_source_files(src):
        rel = s.relative_to(src)
        d = dst / rel
        if not d.exists():
            added.append(str(rel))
        elif not filecmp.cmp(s, d, shallow=False):
            updated.append(str(rel))
        else:
            unchanged += 1
        if not dry_run:
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, d)

    # 清理目标里源目录已删除的 .md，保持两边一致
    removed: list[str] = []
    if dst.is_dir():
        for d in sorted(dst.rglob("*.md")):
            rel = d.relative_to(dst)
            if rel.parts and rel.parts[0] == "_工具":
                continue
            if not (src / rel).is_file():
                removed.append(str(rel))
                if not dry_run:
                    d.unlink()

    return {
        "src": str(src),
        "dst": str(dst),
        "added": added,
        "updated": updated,
        "removed": removed,
        "unchanged": unchanged,
        "dry_run": dry_run,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="同步卡片知识库到后端运行时目录")
    parser.add_argument("--src", default=None, help=f"编辑目录，默认 {DEFAULT_SRC}")
    parser.add_argument("--dst", default=None, help=f"运行时目录，默认 {DEFAULT_DST}")
    parser.add_argument("--dry-run", action="store_true", help="只显示差异，不写文件")
    args = parser.parse_args(argv)

    src = Path(args.src) if args.src else DEFAULT_SRC
    dst = Path(args.dst) if args.dst else DEFAULT_DST

    try:
        result = sync(src, dst, dry_run=args.dry_run)
    except FileNotFoundError as exc:
        print(f"同步失败：{exc}")
        return 2

    print(f"源目录：{result['src']}")
    print(f"目标：  {result['dst']}")
    print(
        f"新增 {len(result['added'])} | 更新 {len(result['updated'])} | "
        f"删除 {len(result['removed'])} | 未变 {result['unchanged']}"
    )
    for label, items in (
        ("新增", result["added"]),
        ("更新", result["updated"]),
        ("删除", result["removed"]),
    ):
        for name in items[:20]:
            print(f"  [{label}] {name}")
        if len(items) > 20:
            print(f"  ... 另有 {len(items) - 20} 项")

    if args.dry_run:
        print("\n这是 dry-run，没有写入任何文件。")
        return 0

    from app.services.ai_lab import kb_cards  # noqa: E402

    kb_cards.reset_cache()
    stats = kb_cards.build()
    print(
        f"\n索引已重建：{stats.get('cards')} 张卡 / {stats.get('chunks')} 片段 "
        f"（{stats.get('seconds')}s，{stats.get('index_mb')}MB）"
    )
    if stats.get("problems"):
        print("以下文件有问题，未入库：")
        for p in stats["problems"]:
            print(f"  - {p}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
