"""登录发起限流:进程内每 IP 60 秒滑动窗口。

多 worker 下各进程独立计数,是防刷下限而非全局配额;状态存储本身仍有 TTL 兜底。
"""

import threading
import time

_WINDOW_SEC = 60.0
_SWEEP_EVERY_SEC = 60.0


class LoginRateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self._last_sweep = 0.0

    def allow(self, key: str, limit: int) -> bool:
        now = time.monotonic()
        cutoff = now - _WINDOW_SEC
        with self._lock:
            if now - self._last_sweep >= _SWEEP_EVERY_SEC:
                self._sweep_locked(cutoff)
            hits = [t for t in self._hits.get(key, ()) if t > cutoff]
            if len(hits) >= limit:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
            self._last_sweep = 0.0

    def _sweep_locked(self, cutoff: float) -> None:
        stale = [k for k, hits in self._hits.items() if not hits or hits[-1] <= cutoff]
        for k in stale:
            del self._hits[k]
        self._last_sweep = time.monotonic()
