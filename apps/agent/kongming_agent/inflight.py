"""在途流式执行登记:把帧生成器的执行放进自有任务并按 trace_id 登记,供中止端点取消。

登记表是进程内存结构,不跨进程;取消是协作式的——任务在下一个等待点收到取消。"""

import asyncio
import logging
from collections.abc import AsyncIterator

log = logging.getLogger(__name__)


class InflightRegistry:
    """按 trace_id 登记在途的图执行任务(同一标识可有多处)。"""

    def __init__(self) -> None:
        self._tasks: dict[str, set[asyncio.Task[None]]] = {}

    def register(self, trace_id: str, task: asyncio.Task[None]) -> None:
        """登记一个在途执行。"""
        self._tasks.setdefault(trace_id, set()).add(task)

    def discard(self, trace_id: str, task: asyncio.Task[None]) -> None:
        """注销一个在途执行;该标识下已无在途时清掉键。"""
        tasks = self._tasks.get(trace_id)
        if tasks is None:
            return
        tasks.discard(task)
        if not tasks:
            del self._tasks[trace_id]

    def abort(self, trace_id: str) -> int:
        """请求取消该标识下全部在途执行,返回实际发出取消的个数。"""
        count = 0
        for task in tuple(self._tasks.get(trace_id, ())):
            # 已结束的不算在途;已请求过取消的不重复请求(重复取消会打断图内的清理)
            if task.done() or task.cancelling():
                continue
            task.cancel()
            count += 1
        return count


_registry = InflightRegistry()


def abort_inflight(trace_id: str) -> int:
    """中止该 trace_id 下全部在途流式执行,返回实际中止的个数。"""
    return _registry.abort(trace_id)


async def in_owned_task(frames: AsyncIterator[str], trace_id: str) -> AsyncIterator[str]:
    """在自有任务里驱动 frames 并按 trace_id 登记;调用方只排空帧队列。

    调用方被取消或关闭(等同连接断开)时自有任务一并取消,图执行不因换了任务
    而失去「连接断开即停」的效果。"""

    queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def _run() -> None:
        try:
            async for frame in frames:
                queue.put_nowait(frame)
        finally:
            queue.put_nowait(None)

    task = asyncio.create_task(_run())
    _registry.register(trace_id, task)
    task.add_done_callback(_log_unexpected_failure)
    try:
        while True:
            frame = await queue.get()
            if frame is None:
                return
            yield frame
    finally:
        _registry.discard(trace_id, task)
        task.cancel()


def _log_unexpected_failure(task: asyncio.Task[None]) -> None:
    """自有任务的异常若无人接收,在此记日志(可预期的异常都已在生成器内收敛为终态帧)。"""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.error("流式问答执行任务异常结束", exc_info=exc)
