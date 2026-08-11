"""清理磁盘上未被 files 表引用的孤儿文件（手动运维脚本）。

用法：python scripts/cleanup_orphan_files.py [--dry-run]
"""

import argparse
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from sqlalchemy import select  # noqa: E402

from app.api.v1.files import UPLOAD_DIR  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.file import FileUpload  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="清理上传目录中的孤儿文件")
    parser.add_argument("--dry-run", action="store_true", help="仅列出，不删除")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        known = set(db.scalars(select(FileUpload.file_id)))
    finally:
        db.close()

    orphans = [p for p in UPLOAD_DIR.iterdir() if p.is_file() and p.name not in known]
    removed = 0
    for path in sorted(orphans):
        print(f"{'[dry-run] ' if args.dry_run else ''}删除孤儿文件: {path.name}")
        if not args.dry_run:
            path.unlink(missing_ok=True)
            removed += 1
    print(f"完成：{'发现' if args.dry_run else '删除'} {len(orphans) if args.dry_run else removed} 个孤儿文件")


if __name__ == "__main__":
    main()
