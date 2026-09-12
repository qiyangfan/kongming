"""判定值对象:与消费方 kongming sqlfw.Verdict 同构(passed + reason)。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Verdict:
    passed: bool
    reason: str | None = None

    def __repr__(self) -> str:  # 便于测试断言与对拍日志
        return f"Verdict(passed={self.passed}, reason={self.reason!r})"
