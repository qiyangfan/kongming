"""cron 调度器(031):lifespan 薄壳(015 归档调度先例)——30s tick
扫描到期 CRON 触发器;最小间隔保护与进行中 SKIPPED 判定;
执行走独立会话任务(不占调度循环)。

单进程形态(多副本互斥挂账 O2);重启补窗:next_fire_at 持久化,
重启后首轮扫描即补到期(未跑窗口不补跑,语义=at-most-once)。
"""

import asyncio
import datetime as dt
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from kongming.trigger.service import TriggerService

log = logging.getLogger(__name__)

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
            now_iso = now.isoformat()
            due = await svc.list_due(now_iso=now_iso)
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
            task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        # 101:到期挂起重试扫描派发(与 cron 同 _running 互斥——重试执行中
        # cron 到期记 SKIPPED;cron 执行中重试到期则顺延)
        await self._tick_retries(now_iso=now_iso)
        # 108:到期邮件触发器轮询派发(轮询间隔独立于 cron 表达式;
        # 与 cron/重试共享 _running 互斥面,前次未完成记 SKIPPED)
        await self._tick_emails(now_iso=now_iso)

    async def _tick_retries(self, *, now_iso: str) -> None:
        """扫描到期挂起重试并派发(101 调度重排):经同一 _running 互斥面,
        重试与普通触发共享「执行中永不并发」保护。"""
        factory = self._factory
        if factory is None:
            return
        svc, close = await factory()
        try:
            due = await svc.list_due_retries(now_iso=now_iso)
        finally:
            await close()
        for run_row in due:
            tid = str(run_row["trigger_id"])
            if tid in self._running:
                if self._running[tid].done():
                    del self._running[tid]
                else:
                    # 执行中永不并发:挂起重试顺延一个退避间隔(不取消)
                    await self._defer_retry(run_row)
                    continue
            task = asyncio.create_task(self._fire_retry_then_close(tid, str(run_row["id"])))
            self._running[tid] = task
            task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

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
            await svc.mark_fired(trigger_id=trigger_id, last_fired_iso=fired_iso, next_fire_iso=nxt)
        except Exception:
            log.warning("trigger 执行异常 id=%s", trigger_id, exc_info=True)
        finally:
            await close()
            self._running.pop(trigger_id, None)

    async def _fire_retry_then_close(self, trigger_id: str, run_id: str) -> None:
        """派发单个到期重试(101):服务侧同路执行+落定+链推进;占用 _running
        期间 cron 到期由既有保护记 SKIPPED。"""
        factory = self._factory
        if factory is None:
            return
        svc, close = await factory()
        try:
            await svc.fire_retry(run_id=run_id)
        except Exception:
            log.warning("trigger 重试派发异常 run=%s", run_id, exc_info=True)
        finally:
            await close()
            self._running.pop(trigger_id, None)

    async def _defer_retry(self, run_row: dict[str, Any]) -> None:
        """挂起重试顺延(101):撞上在途执行推迟一个退避间隔。"""
        factory = self._factory
        if factory is None:
            return
        svc, close = await factory()
        try:
            await svc.defer_retry(run_id=str(run_row["id"]))
        finally:
            await close()

    # ── 邮件入站轮询派发(108) ──

    async def _tick_emails(self, *, now_iso: str) -> None:
        """到期邮件触发器派发(108):轮询间隔(next_fire_at 节流)到期即轮;
        前次轮询未完成记 SKIPPED(复用并发保护语义)且节流照推进。"""
        factory = self._factory
        if factory is None:
            return
        svc, close = await factory()
        try:
            due = await svc.list_due_emails(now_iso=now_iso)
        finally:
            await close()
        for row in due:
            tid = str(row["id"])
            if tid in self._running:
                if self._running[tid].done():
                    del self._running[tid]
                else:
                    await self._record_email_skipped(row)
                    continue
            task = asyncio.create_task(
                self._poll_email_then_mark(tid, int(row.get("imap_poll_interval_sec") or 300))
            )
            self._running[tid] = task
            task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

    async def _poll_email_then_mark(self, trigger_id: str, interval_sec: int) -> None:
        """单触发器轮询(108):轮询永不逃逸;节流无论成败照推进
        (失败按间隔再试,不堆积)。"""
        factory = self._factory
        if factory is None:
            return
        svc, close = await factory()
        try:
            await svc.poll_email(trigger_id=trigger_id)
        except Exception:
            log.warning("trigger 邮件轮询异常 id=%s", trigger_id, exc_info=True)
        finally:
            try:
                await svc.mark_email_polled(trigger_id=trigger_id, interval_sec=interval_sec)
            except Exception:
                log.warning("trigger 邮件轮询节流推进异常 id=%s", trigger_id, exc_info=True)
            await close()
            self._running.pop(trigger_id, None)

    async def _record_email_skipped(self, row: dict[str, Any]) -> None:
        """前次轮询未完成 → SKIPPED(108):复用并发保护语义;节流推进防
        每 tick 重复判 SKIPPED。"""
        factory = self._factory
        if factory is None:
            return
        svc, close = await factory()
        try:
            await svc.record_skipped(
                tenant_id=str(row["tenant_id"]), trigger_id=str(row["id"]), reason="前次轮询进行中"
            )
            await svc.mark_email_polled(
                trigger_id=str(row["id"]),
                interval_sec=int(row.get("imap_poll_interval_sec") or 300),
            )
        finally:
            await close()

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
