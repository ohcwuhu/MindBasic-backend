"""维护任务：孤儿上传文件扫描 + 异常中断的 AI 教练会话收尾（定时任务调用）。"""

from datetime import datetime, timedelta
import time

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.files import UPLOAD_DIR
from app.models.ai_conversation import AiConversation
from app.models.file import FileUpload
from app.services.ai_lab import upload_store

_ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}

#: 会话超过该时长仍停留在 ACTIVE，视为客户端异常断开
STALE_SESSION_HOURS = 6
VC_UPLOAD_MAX_AGE_MINUTES = 30


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


def sweep_orphan_vc_uploads(
    *,
    max_age_minutes: int = VC_UPLOAD_MAX_AGE_MINUTES,
    now_ts: float | None = None,
) -> int:
    """清理超时且不在处理中的视频通话音频。

    文件名必须符合上传接口的 32 位十六进制规则；在途文件只按内存标记保护，
    不扫描系统临时目录的其他内容。
    """
    removed = 0
    cutoff = (time.time() if now_ts is None else now_ts) - max_age_minutes * 60
    try:
        entries = list(upload_store.UPLOAD_DIR.iterdir())
    except OSError:
        return 0
    for path in entries:
        if not path.is_file() or path.suffix.lower() not in upload_store.AUDIO_SUFFIXES:
            continue
        file_id = path.stem.lower()
        if len(file_id) != 32 or any(c not in "0123456789abcdef" for c in file_id):
            continue
        if upload_store.is_active(file_id):
            continue
        try:
            if path.stat().st_mtime > cutoff:
                continue
        except OSError:
            continue
        if upload_store.drop(file_id):
            removed += 1
    return removed


async def sweep_stale_coaching_sessions(
    db: AsyncSession,
    *,
    stale_hours: int = STALE_SESSION_HOURS,
) -> int:
    """把长时间停留在 ACTIVE 的 AI 教练会话标记为 ABANDONED。

    客户端异常断开时不会触发 ``vc_stop``，会话会一直停留在 ACTIVE。
    这里按时长收尾，保证会话状态可统计（活跃/正常结束/异常中断三分）。
    只改状态、不删数据，保留轮次与授权存证以便复核。
    """
    # 以数据库时钟为准：会话的 created_at 由 CURRENT_TIMESTAMP 写入，
    # 用应用进程的本地时间做比较会在跨时区部署时误判。
    now = await db.scalar(select(func.now())) or datetime.now()
    cutoff = now - timedelta(hours=max(1, stale_hours))
    rows = await db.scalars(
        select(AiConversation).where(
            AiConversation.status == "ACTIVE",
            AiConversation.created_at < cutoff,
        )
    )
    swept = 0
    for session in rows:
        session.status = "ABANDONED"
        session.ended_at = session.ended_at or session.created_at
        swept += 1
    if swept:
        await db.commit()
    return swept
