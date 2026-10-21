"""生产调度器：订单超时释放 / 导出过期清理 / 孤儿文件扫描 / 异常会话收尾（APScheduler）。"""

import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal

logger = get_logger("mindbasic.scheduler")
scheduler = AsyncIOScheduler()


async def _run_expire_orders() -> None:
    try:
        async with AsyncSessionLocal() as db:
            from app.services.order_service import expire_pending_orders

            closed = await expire_pending_orders(db)
            if closed:
                logger.info("[SCHEDULER] 超时订单关闭 %s 单", closed)
    except Exception:  # noqa: BLE001
        logger.exception("[SCHEDULER] 订单超时任务异常")


async def _run_cleanup() -> None:
    try:
        async with AsyncSessionLocal() as db:
            from app.services.data_export_service import cleanup_expired_exports
            from app.services.maintenance_service import (
                sweep_orphan_uploads,
                sweep_orphan_vc_uploads,
                sweep_stale_coaching_sessions,
            )

            expired = await cleanup_expired_exports(db)
            orphans = await sweep_orphan_uploads(db)
            vc_orphans = sweep_orphan_vc_uploads()
            stale = await sweep_stale_coaching_sessions(db)
            if expired or orphans or vc_orphans or stale:
                logger.info(
                    "[SCHEDULER] 清理导出 %s 个、孤儿文件 %s 个、音频 %s 个、异常中断会话 %s 个",
                    expired, orphans, vc_orphans, stale,
                )
    except Exception:  # noqa: BLE001
        logger.exception("[SCHEDULER] 清理任务异常")


async def _run_sweep_idle_calls() -> None:
    """结束长时间没有语音活动的通话（前端挂起时的服务端兜底）。"""
    try:
        from app.services.ai_lab import realtime_session

        ended = await realtime_session.sweep_idle_calls()
        if ended:
            logger.info("[SCHEDULER] 无语音超时自动结束通话 %s 个", len(ended))
    except Exception:  # noqa: BLE001
        logger.exception("[SCHEDULER] 空闲通话清理异常")


def start_scheduler() -> None:
    """注册并启动定时任务（可经 SCHEDULER_ENABLED 关闭，测试默认关闭）。"""
    if os.environ.get("SCHEDULER_ENABLED", "true").lower() == "false":
        logger.info("[SCHEDULER] 已禁用（SCHEDULER_ENABLED=false）")
        return
    if scheduler.running:
        return
    scheduler.add_job(
        _run_expire_orders,
        IntervalTrigger(minutes=1),
        id="expire_orders",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _run_cleanup,
        IntervalTrigger(hours=1),
        id="cleanup_exports_orphans",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _run_sweep_idle_calls,
        IntervalTrigger(seconds=60),
        id="sweep_idle_calls",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.start()
    logger.info("[SCHEDULER] 定时任务已启动（订单超时 1min / 清理 1h / 空闲通话 1min）")


def shutdown_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
