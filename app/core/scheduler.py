"""生产调度器：订单超时释放 / 导出过期清理 / 孤儿文件扫描（APScheduler）。"""

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
            from app.services.maintenance_service import sweep_orphan_uploads

            expired = await cleanup_expired_exports(db)
            orphans = await sweep_orphan_uploads(db)
            if expired or orphans:
                logger.info("[SCHEDULER] 清理导出 %s 个、孤儿文件 %s 个", expired, orphans)
    except Exception:  # noqa: BLE001
        logger.exception("[SCHEDULER] 清理任务异常")


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
    scheduler.start()
    logger.info("[SCHEDULER] 定时任务已启动（订单超时 1min / 清理 1h）")


def shutdown_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
