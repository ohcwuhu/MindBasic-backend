"""后台任务登记表（保持强引用，避免任务被垃圾回收）。

``asyncio.create_task`` 只保存任务的弱引用：事件循环之外若没有别的引用，
任务可能在跑完之前就被 GC 回收，表现为"留痕时有时无"这类偶发丢数据。
本模块统一收口后台任务的创建，持有强引用直到任务结束。

用法::

    from app.core.tasks import spawn_task

    spawn_task(record_something_safely(...), name="record-message")

任务内部异常一律记录日志、不向上冒泡——后台任务没有等待方，
抛出异常只会产生 "Task exception was never retrieved" 噪声。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

log = logging.getLogger(__name__)

# 持有强引用；任务结束后由回调移除
_tasks: set[asyncio.Task] = set()


def spawn_task(
    coro: Coroutine[Any, Any, Any],
    *,
    name: str | None = None,
) -> asyncio.Task | None:
    """在当前事件循环中创建后台任务并持有强引用。

    Args:
        coro: 待执行的协程。
        name: 任务名（排障时便于在日志里区分）。

    Returns:
        创建出的 ``Task``；当前没有运行中的事件循环时返回 ``None``
        （协程会被关闭，不会产生 "coroutine was never awaited" 警告）。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        coro.close()
        log.warning("后台任务 %s 未创建：当前没有运行中的事件循环", name or "<anonymous>")
        return None

    task = loop.create_task(coro, name=name)
    _tasks.add(task)
    task.add_done_callback(_on_task_done)
    return task


def _on_task_done(task: asyncio.Task) -> None:
    """任务结束：释放引用，并把异常记进日志（避免静默失败）。"""
    _tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.warning(
            "后台任务 %s 异常（已忽略，不影响主流程）：%s",
            task.get_name(),
            exc,
            exc_info=exc,
        )


def pending_count() -> int:
    """当前在跑的后台任务数（健康检查 / 测试用）。"""
    return len(_tasks)


def reset() -> None:
    """清空登记表（测试隔离用；不会取消任务）。"""
    _tasks.clear()


async def drain_tasks(max_wait_seconds: float = 5.0) -> int:
    """关停时等待已登记任务，返回超时后仍未完成的数量。"""
    pending = {task for task in _tasks if not task.done()}
    if not pending:
        return 0
    _, unfinished = await asyncio.wait(pending, timeout=max(0.0, max_wait_seconds))
    if unfinished:
        log.warning(
            "关停排空超时，仍有 %d 个后台任务未完成：%s",
            len(unfinished),
            ", ".join(sorted(task.get_name() for task in unfinished)),
        )
    return len(unfinished)
