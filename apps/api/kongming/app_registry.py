"""进程级 FastAPI 实例登记。

非请求依赖注入的装配点(如 MCP 入站中间件)需要读取应用上的
dependency_overrides 才能感知测试替身;经此叶子模块转交,避免
组合根反向 import kongming.main 形成循环。不 import 任何 kongming 模块。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

_app: FastAPI | None = None


def bind_app(app: FastAPI) -> None:
    """登记进程内的主应用实例(装配时调用一次)。"""
    global _app
    _app = app


def get_bound_app() -> FastAPI:
    """返回已登记的主应用;未登记说明装配顺序有误。"""
    if _app is None:
        raise RuntimeError("FastAPI 实例尚未登记")
    return _app
