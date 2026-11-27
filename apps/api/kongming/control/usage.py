"""产品事件/健康上报:本地 outbox 缓冲、批量补发、幂等键与口径守卫。

LLM token/费用由 muniu 唯一上报;kongming 的产品事件不收这些字段,也不收
提示词/结果内容/凭据。控制面不可用时事件留在 outbox,恢复后按顺序补发。
"""

import asyncio
import logging
from datetime import datetime, timedelta

from kongming.control.models import ProductEvent
from kongming.control.ports import Clock, UsageOutbox, UsageSink

log = logging.getLogger(__name__)

_FORBIDDEN_KEYS = {
    "tokens",
    "token",
    "token_count",
    "prompt_tokens",
    "completion_tokens",
    "cost",
    "price",
    "amount",
    "prompt",
    "messages",
    "question",
    "answer",
    "result",
    "content",
    "sql",
    "api_key",
    "password",
    "secret",
    "dsn",
    "connection_string",
}
_FORBIDDEN_FRAGMENTS = ("token", "cost", "prompt", "secret", "password", "credential")


class ProductEventRejected(ValueError):
    """事件含口径禁止字段(LLM 计费/提示词/结果/凭据)。"""


def build_product_event(
    *,
    event_id: str,
    event_type: str,
    occurred_at: datetime,
    tenant_id: str,
    user_id: str | None = None,
    bot_key: str | None = None,
    attributes: dict[str, object] | None = None,
) -> ProductEvent:
    attrs = attributes or {}
    for key in attrs:
        lowered = key.lower()
        if lowered in _FORBIDDEN_KEYS or any(f in lowered for f in _FORBIDDEN_FRAGMENTS):
            raise ProductEventRejected(f"产品事件不得包含字段 {key}")
    if not event_type.strip() or not tenant_id.strip():
        raise ProductEventRejected("产品事件缺 event_type/tenant_id")
    return ProductEvent(
        event_id=event_id,
        event_type=event_type,
        occurred_at=occurred_at,
        tenant_id=tenant_id,
        user_id=user_id,
        bot_key=bot_key,
        attributes=attrs,
    )


class UsageReporter:
    def __init__(
        self,
        *,
        outbox: UsageOutbox,
        sink: UsageSink,
        clock: Clock,
        enabled: bool,
        buffer_max: int,
        batch_size: int = 100,
    ) -> None:
        self._outbox = outbox
        self._sink = sink
        self._clock = clock
        self._enabled = enabled
        self._buffer_max = buffer_max
        self._batch = batch_size
        self._queue: asyncio.Queue[ProductEvent] = asyncio.Queue(maxsize=buffer_max)
        self._dropped = 0

    @property
    def dropped(self) -> int:
        return self._dropped

    def enqueue(self, event: ProductEvent) -> None:
        """热路径非阻塞入队;满即丢弃最旧(仅内存队列,持久层另有上限)。"""
        if not self._enabled:
            return
        if self._queue.full():
            try:
                self._queue.get_nowait()
                self._dropped += 1
            except asyncio.QueueEmpty:  # 并发消费后队列已空
                pass
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:  # 极小窗口内又被填满
            self._dropped += 1

    async def drain_queue(self) -> int:
        """把内存队列落到 outbox;返回落库条数。"""
        n = 0
        while True:
            try:
                event = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            await self._outbox.add(event)
            n += 1
        return n

    async def record(self, event: ProductEvent) -> None:
        """同步落库入口(测试/低频事件);先按缓冲上限裁剪,再写入。"""
        if not self._enabled:
            return
        pending = await self._outbox.count_pending()
        if pending >= self._buffer_max:
            dropped = await self._outbox.trim_to(limit=self._buffer_max - 1)
            if dropped:
                self._dropped += dropped
                log.warning("用量缓冲达上限,丢弃最旧事件 %d 条", dropped)
        await self._outbox.add(event)

    async def flush_once(self) -> int:
        """投递一批;返回成功条数。失败事件按 1 分钟后退避。"""
        if not self._enabled:
            return 0
        await self.drain_queue()
        events = await self._outbox.pending(limit=self._batch)
        if not events:
            return 0
        try:
            await self._sink.send(events)
        except Exception as e:  # noqa: BLE001
            log.warning("用量上报失败(保留缓冲): %s", e)
            await self._outbox.failed(
                event_ids=[event.event_id for event in events],
                next_attempt_at=self._clock.now() + timedelta(minutes=1),
            )
            return 0
        await self._outbox.delivered(event_ids=[event.event_id for event in events])
        return len(events)

    async def run_forever(self, *, interval_sec: int) -> None:
        while True:
            try:
                await self.flush_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("用量上报循环异常(下轮重试)")
            await asyncio.sleep(interval_sec)
