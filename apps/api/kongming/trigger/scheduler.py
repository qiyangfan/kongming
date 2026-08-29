"""cron 调度器(031):lifespan 薄壳(015 归档调度先例)——30s tick
扫描到期 CRON 触发器;最小间隔保护与进行中 SKIPPED 判定;
执行走独立会话任务(不占调度循环)。

单进程形态(多副本互斥挂账 O2);重启补窗:next_fire_at 持久化,
重启后首轮扫描即补到期(未跑窗口不补跑,语义=at-most-once)。
"""

import asyncio
import datetime as dt
import time
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from kongming.trigger.service import TriggerService

log = structlog.get_logger()

_TICK_SEC = 30

# 服务工厂:每次调用开独立会话,返回 (TriggerService, 关闭回调)
_ServiceFactory = Callable[[], Awaitable[tuple[TriggerService, Callable[[], Awaitable[None]]]]]


class TriggerScheduler:
    """30s tick 扫描;running 集合防同触发器并发堆积。"""

    def __init__(self, service_factory: _ServiceFactory | None) -> None:
        # None=工厂未注入(lifespan start 前),tick 直接空转;
        # 每个 tick/执行独立会话(015/017 短事务先例)
        self._factory = service_factory
        self._running: dict[str, asyncio.Task[None]] = {}
        self._task: asyncio.Task[None] | None = None

    def attach_factory(self, factory: _ServiceFactory) -> None:
        """lifespan 注入服务工厂(构造后一次性接线)。"""
        self._factory = factory

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # R1-T1:停机收割在飞 fire task(main.py archive_task 同款先例,
        # 防 run 落库半途)
        if self._running:
            await asyncio.gather(*self._running.values(), return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(_TICK_SEC)
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("trigger 调度 tick 异常(下轮继续)", exc_info=True)

    async def _tick(self) -> None:
        factory = self._factory
        if factory is None:
            return
        svc, close = await factory()
        try:
            now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
            due = await svc.list_due(now_iso=now.isoformat())
        finally:
            await close()
        for row in due:
            tid = str(row["id"])
            if tid in self._running:
                if self._running[tid].done():
                    del self._running[tid]
                else:
                    await self._record_skipped(row, "前次执行进行中")
                    continue
            # 最小间隔保护(spec「触发风暴」)
            last = row.get("last_fired_at")
            if last is not None:
                # R1-T3:系统时间回拨致负 elapsed——按 0 计(恒 SKIPPED,
                # 调度不自毁;next_fire 仍推进)
                elapsed = max(0.0, time.time() - last.timestamp())
                if elapsed < int(row["min_interval_sec"]):
                    await self._record_skipped(row, "最小间隔保护")
                    continue
            task = asyncio.create_task(self._fire_then_mark(tid))
            self._running[tid] = task
            # R1-T2:收割异常(防 "never retrieved" 噪音)
            task.add_done_callback(
                lambda t: t.exception() if not t.cancelled() else None
            )

    async def _fire_then_mark(self, trigger_id: str) -> None:
        factory = self._factory
        if factory is None:
            return
        svc, close = await factory()
        fired_iso = ""
        try:
            now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
            fired_iso = now.isoformat()
            await svc.fire_cron(trigger_id=trigger_id)
            # 触发后推进 next_fire(错过窗口不补跑,at-most-once)
            row = await svc.get_def(trigger_id=trigger_id)
            nxt = None
            if row and row.get("cron_expr"):
                from kongming.trigger.models import CronError, next_fire

                try:
                    nxt = next_fire(str(row["cron_expr"]), now).isoformat()
                except CronError:
                    # R1-S1b:重算失败(闰日跨 366 天窗等)——next_fire 置
                    # NULL 停调度止损(防每 tick 重触发风暴),落 FAILED run
                    log.error("trigger 重算 next_fire 失败,调度停止 id=%s", trigger_id)
                    await svc.record_failed(
                        tenant_id=str(row["tenant_id"]),
                        trigger_id=trigger_id,
                        reason="cron 下次触发计算失败,调度已停止(请检查表达式)",
                    )
            await svc.mark_fired(
                trigger_id=trigger_id, last_fired_iso=fired_iso, next_fire_iso=nxt
            )
        except Exception:
            log.warning("trigger 执行异常 id=%s", trigger_id, exc_info=True)
        finally:
            await close()
            self._running.pop(trigger_id, None)

    async def _record_skipped(self, row: dict[str, Any], reason: str) -> None:
        factory = self._factory
        if factory is None:
            return
        svc, close = await factory()
        try:
            await svc.record_skipped(
                tenant_id=str(row["tenant_id"]), trigger_id=str(row["id"]), reason=reason
            )
            # 跳过也要推进 next_fire,否则每 tick 重复判 SKIPPED
            now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
            if row.get("cron_expr"):
                from kongming.trigger.models import CronError, next_fire

                try:
                    nxt: str | None = next_fire(str(row["cron_expr"]), now).isoformat()
                except CronError:
                    # R1-S1c:置 NULL 止损(防同 tick 饿死后续触发器)
                    nxt = None
                last = row.get("last_fired_at")
                await svc.mark_fired(
                    trigger_id=str(row["id"]),
                    last_fired_iso=last.isoformat() if last else now.isoformat(),
                    next_fire_iso=nxt,
                )
        finally:
            await close()
