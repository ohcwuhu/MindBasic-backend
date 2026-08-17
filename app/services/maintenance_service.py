"""维护任务：孤儿上传文件扫描（定时任务调用）。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.files import UPLOAD_DIR
from app.models.file import FileUpload

_ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}


async def sweep_orphan_uploads(db: AsyncSession) -> int:
    """删除磁盘上无数据库记录且符合上传命名规则的孤儿文件（防误删）。"""
    known = set(await db.scalars(select(FileUpload.file_id)))
    removed = 0
    try:
        entries = list(UPLOAD_DIR.iterdir())
    except OSError:
        return 0
    for path in entries:
        if not path.is_file() or path.name in known:
            continue
        if path.suffix.lower() not in _ALLOWED_SUFFIXES:
            continue
        stem = path.stem.lower()
        if len(stem) != 32 or any(c not in "0123456789abcdef" for c in stem):
            continue
        try:
            path.unlink(missing_ok=True)
            removed += 1
        except OSError:
            continue
    return removed
