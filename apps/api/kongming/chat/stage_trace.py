"""节点阶段轨迹收集器(spec「节点阶段轨迹持久化」纯函数/纯类)。

流式消费端就地累积既有 stage 帧(采集面零新增),终态落库时随行持久化。
条目形态 {node,status,retry,t};条数上限防御 + 截断标记;畸形帧跳过;
零事件 → payload None(同步轮/无帧流,如实为空)。时钟构造注入,测试
无 sleep。"""

import time
from collections.abc import Callable

MAX_STAGE_EVENTS = 64  # 正常轮 ≤16 条目(8 节点×2);64 兜极端重试风暴


class StageTraceCollector:
    def __init__(
        self,
        *,
        max_events: int = MAX_STAGE_EVENTS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max = max_events
        self._clock = clock
        self._events: list[dict[str, object]] = []
        self._start = clock()
        self._truncated = False

    def add_stage(self, data: dict[str, object]) -> None:
        """消费一条 stage 帧(node/status/retry_count);畸形形态静默跳过。"""
        if not isinstance(data, dict):
            return
        node = data.get("node")
        status = data.get("status")
        if not isinstance(node, str) or not node or status not in ("running", "done"):
            return
        if len(self._events) >= self._max:
            self._truncated = True
            return
        retry = data.get("retry_count")
        self._events.append(
            {
                "node": node,
                "status": str(status),
                "retry": retry if isinstance(retry, int) else 0,
                "t": round((self._clock() - self._start) * 1000),
            }
        )

    def payload(self) -> list[dict[str, object]] | None:
        """落库形态:None=无事件(不落轨迹);截断发生时追加截断标记条目。"""
        if not self._events:
            return None
        out = list(self._events)
        if self._truncated:
            out.append({"truncated": True})
        return out
