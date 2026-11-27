"""产品事件发布点:热路径只调用本模块,不直接持有控制面客户端/仓储。

未安装 reporter(standalone/测试)时静默丢弃;上报失败不影响业务。
"""

import logging
from datetime import UTC, datetime
from typing import Protocol

from kongming.control.models import ProductEvent
from kongming.control.usage import ProductEventRejected, build_product_event
from kongming.uuid7 import uuid7

log = logging.getLogger(__name__)


class EventSink(Protocol):
    def enqueue(self, event: ProductEvent) -> None: ...


_sink: EventSink | None = None


def install_event_sink(sink: EventSink | None) -> None:
    global _sink
    _sink = sink


def report_product_event(
    event_type: str,
    *,
    tenant_id: str,
    user_id: str | None = None,
    bot_key: str | None = None,
    attributes: dict[str, object] | None = None,
) -> None:
    """记录一条产品事件(会话/工具/工作流/任务/报告);绝不抛到调用方。"""
    sink = _sink
    if sink is None:
        return
    try:
        event = build_product_event(
            event_id=uuid7().hex,
            event_type=event_type,
            occurred_at=datetime.now(UTC),
            tenant_id=tenant_id,
            user_id=user_id,
            bot_key=bot_key,
            attributes=attributes,
        )
        sink.enqueue(event)
    except ProductEventRejected as e:
        log.warning("产品事件被口径守卫拒绝: %s", e)
    except Exception:
        log.exception("产品事件入队失败(不影响业务)")
