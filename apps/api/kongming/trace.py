"""请求级 trace_id 单源:中间件写入,chat/审计/用量与响应头同一值。"""

from __future__ import annotations

from contextvars import ContextVar, Token
from uuid import uuid4

_current: ContextVar[str | None] = ContextVar("km_trace_id", default=None)


def new_trace_id() -> str:
    return uuid4().hex


def set_trace_id(trace_id: str) -> Token[str | None]:
    return _current.set(trace_id)


def get_trace_id() -> str | None:
    return _current.get()


def ensure_trace_id() -> str:
    """取现有 trace_id;无则生成并写入 contextvar(幂等)。"""
    tid = _current.get()
    if tid:
        return tid
    tid = new_trace_id()
    _current.set(tid)
    return tid
