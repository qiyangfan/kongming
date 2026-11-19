"""SSE 帧与图流 chunk 的公共处理(8 节点图与 ReAct 图共用)。"""

import json
from typing import cast


def sse_frame(event: str, data_json: str) -> str:
    """标准 SSE 帧:event 行 + 单行 data + 空行分帧(事件契约)。"""
    return f"event: {event}\ndata: {data_json}\n\n"


def consume_chunk(mode: str, chunk: object, final: dict[str, object]) -> str | None:
    """消费一个图流 chunk:custom 产出待发帧,updates 累积终态。"""
    if mode == "custom":
        ev = cast("dict[str, object]", chunk) if isinstance(chunk, dict) else {}
        event = str(ev.get("event") or "")
        if not event:
            return None
        return sse_frame(event, json.dumps(ev.get("data"), ensure_ascii=False))
    if not isinstance(chunk, dict):
        return None
    for delta in cast("dict[str, object]", chunk).values():
        if isinstance(delta, dict):
            # updates 累积=终态 state
            final.update(cast("dict[str, object]", delta))
    return None
