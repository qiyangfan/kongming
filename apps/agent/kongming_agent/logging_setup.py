"""运行日志配置(061):单一 API、单一输出流、级别可配、dev 文本 / prod JSON。

两进程各持一份小配置(ADR-002 不跨进程 import)。INFO 及以上不再被
root lastResort 丢弃。
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(*, level: str = "INFO", json_logs: bool = False) -> None:
    """启动期执行一次;重复调用幂等(根 handler 重建)。"""
    fmt = (
        _JsonFormatter()
        if json_logs
        else logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(fmt)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
