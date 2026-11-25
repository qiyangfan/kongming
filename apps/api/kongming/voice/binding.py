"""在途轮绑定:语音轮关联字段(voice_turn_token)的签发、绑定与终态订阅。

进程内单例(单节点前提;多实例与在途轮登记一并外置)。chat 侧在落用户消息
之前校验并绑定,voice 侧凭 token 等待绑定与终态(D2 接线方式)。
"""

import asyncio
import logging
import secrets
from dataclasses import dataclass, field
from enum import Enum

log = logging.getLogger(__name__)


class TokenState(Enum):
    PENDING = "PENDING"
    BOUND = "BOUND"
    TERMINAL = "TERMINAL"
    USED = "USED"  # 已被问答侧消费但尚未绑定(占位防重放)


@dataclass
class _TurnEntry:
    user_id: str
    call_id: str
    state: TokenState = TokenState.PENDING
    trace_id: str | None = None
    outcome: object | None = None
    # 事件在 issue 时即就位,避免异步等待方早于 validate 到达时误判为未创建而直接返回
    bound: asyncio.Event = field(default_factory=asyncio.Event)
    terminal: asyncio.Event = field(default_factory=asyncio.Event)


class VoiceTurnRegistry:
    """语音轮关联字段注册表:一次性、绑定本人,终态经事件等待。"""

    def __init__(self) -> None:
        self._entries: dict[str, _TurnEntry] = {}

    def issue(self, *, user_id: str, call_id: str) -> str:
        token = secrets.token_urlsafe(24)
        self._entries[token] = _TurnEntry(user_id=user_id, call_id=call_id)
        return token

    def validate(self, *, token: str, user_id: str) -> bool:
        """问答侧校验:存在、属于本人、未被使用;用过即作废(防重放)。"""
        entry = self._entries.get(token)
        if entry is None or entry.user_id != user_id:
            return False
        if entry.state in (TokenState.USED, TokenState.TERMINAL):
            return False
        entry.state = TokenState.USED
        return True

    def bind(self, *, token: str, trace_id: str) -> bool:
        """问答侧发起轮后绑定轮次标识(USED → BOUND)。"""
        entry = self._entries.get(token)
        if entry is None or entry.state is not TokenState.USED:
            return False
        entry.state = TokenState.BOUND
        entry.trace_id = trace_id
        entry.bound.set()
        return True

    async def wait_bound(self, *, token: str, timeout_sec: float) -> str | None:
        """voice 侧等待问答侧绑定(5 秒);超时返回 None。"""
        entry = self._entries.get(token)
        if entry is None:
            return None
        try:
            await asyncio.wait_for(entry.bound.wait(), timeout_sec)
        except TimeoutError:
            return None
        return entry.trace_id

    def settle(self, *, trace_id: str, user_id: str, outcome: object) -> bool:
        """问答侧终态收敛后回填(BOUND → TERMINAL);用户须与签发人一致。"""
        for entry in self._entries.values():
            if (
                entry.trace_id == trace_id
                and entry.user_id == user_id
                and entry.state is TokenState.BOUND
            ):
                entry.state = TokenState.TERMINAL
                entry.outcome = outcome
                entry.terminal.set()
                return True
        return False

    async def wait_terminal(self, *, token: str, timeout_sec: float | None = None) -> object | None:
        """voice 侧等待终态;超时返回 None(调用方按取消/超时处理)。"""
        entry = self._entries.get(token)
        if entry is None:
            return None
        if timeout_sec is None:
            await entry.terminal.wait()
        else:
            try:
                await asyncio.wait_for(entry.terminal.wait(), timeout_sec)
            except TimeoutError:
                return None
        return entry.outcome

    def trace_id_of(self, *, token: str) -> str | None:
        entry = self._entries.get(token)
        return entry.trace_id if entry else None

    def drop(self, *, token: str) -> None:
        self._entries.pop(token, None)

    def drop_call(self, *, call_id: str) -> None:
        """通话结束清理该通话名下的全部关联字段,并唤醒仍在等待的后台任务。"""
        for token in [t for t, e in self._entries.items() if e.call_id == call_id]:
            entry = self._entries.pop(token)
            if not entry.bound.is_set():
                entry.bound.set()
            if not entry.terminal.is_set():
                entry.state = TokenState.TERMINAL
                entry.outcome = {"verdict": "CANCELLED"}
                entry.terminal.set()
