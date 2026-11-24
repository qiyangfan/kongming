"""在途轮登记与取消收敛(query-streaming spec「取消端点与归属校验」「取消的执行效果与终态」)。

取消分两步:取消发起方置取消标记、通知 Agent 面并等本轮收敛;收敛本身(写已取消行、
入队终态帧)由后台消费任务完成。登记表是进程内存结构——取消请求与发起请求须经同一进程。"""

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from kongming.chat.agent_client import ABORT_TIMEOUT_SEC
from kongming.chat.domain_errors import TurnNotFound
from kongming.chat.ports import ChatRepo, TurnRegistry

if TYPE_CHECKING:
    from kongming.chat.agent_client import AgentClient

log = logging.getLogger(__name__)

CANCEL_SETTLE_TIMEOUT_SEC = 5.0  # 等本轮收敛的上限(硬取消之后再等同样长)


@dataclass(frozen=True)
class TurnSettlement:
    """取消处理后的结果:outcome 为 CANCELLED(本轮已取消)或 COMPLETED(取消到达前已正常结束)。"""

    outcome: Literal["CANCELLED", "COMPLETED"]
    message_id: str | None = None  # 该轮落库的 assistant 消息 id(未落库为 None)


class InflightTurn:
    """一轮在途流式问答的取消控制点:取消标记、后台任务与收敛结果。"""

    def __init__(self, *, user_id: str, trace_id: str, task: "asyncio.Task[None]") -> None:
        self.user_id = user_id
        self.trace_id = trace_id
        self.cancel_requested = False  # 取消标记(置位后本轮收敛为已取消)
        self.settlement: TurnSettlement | None = None  # 本轮收敛结果(消费任务写入)
        self._task = task

    @property
    def task(self) -> "asyncio.Task[None]":
        """后台消费任务。"""
        return self._task

    async def settle(self) -> TurnSettlement:
        """等本轮收敛;超时才硬取消本地任务,之后仍等它收尾(两段各有上限)。"""
        if not await self._wait():
            self._task.cancel()
            await self._wait()
        return self.settlement or TurnSettlement("CANCELLED")

    async def _wait(self) -> bool:
        """限时等待后台任务结束(上限 CANCEL_SETTLE_TIMEOUT_SEC);返回它是否如期结束。"""
        try:
            await asyncio.wait_for(asyncio.shield(self._task), CANCEL_SETTLE_TIMEOUT_SEC)
        except TimeoutError:
            return False
        except asyncio.CancelledError:
            if not self._task.done():
                raise  # 等待方自己被取消(如请求方断开):原样上抛,不动后台任务
        return True


class InflightTurns:
    """进程内在途轮登记表,按「用户 + trace_id」隔离(入站 trace_id 可由调用方指定)。"""

    def __init__(self) -> None:
        self._turns: dict[tuple[str, str], list[InflightTurn]] = {}

    def register(self, turn: InflightTurn) -> None:
        """登记一个在途轮。"""
        self._turns.setdefault((turn.user_id, turn.trace_id), []).append(turn)

    def unregister(self, turn: InflightTurn) -> None:
        """注销一个在途轮;该键下已无在途时清掉键。"""
        key = (turn.user_id, turn.trace_id)
        turns = self._turns.get(key)
        if turns is None:
            return
        if turn in turns:
            turns.remove(turn)
        if not turns:
            del self._turns[key]

    def find_all(self, *, user_id: str, trace_id: str) -> list[InflightTurn]:
        """取出该用户该标识下全部在途轮(他人同标识查不到)。"""
        return list(self._turns.get((user_id, trace_id), ()))


class TurnCanceller:
    """取消一轮流式问答的编排:置取消标记 → 通知 Agent 面 → 等本轮收敛。"""

    def __init__(self, *, agent: "AgentClient", turns: TurnRegistry | None, repo: ChatRepo) -> None:
        self._agent = agent
        self._turns = turns
        self._repo = repo

    async def cancel(self, *, user_id: str, trace_id: str) -> TurnSettlement:
        """取消一轮问答;该轮不在途时按落库结果回答(无落库结果则抛 TurnNotFound)。"""
        turns = self._turns.find_all(user_id=user_id, trace_id=trace_id) if self._turns else []
        if not turns:
            return await self._settled_without_turn(user_id=user_id, trace_id=trace_id)
        for turn in turns:
            turn.cancel_requested = True
        await self._notify(trace_id)
        settled = await asyncio.gather(*(turn.settle() for turn in turns))
        return TurnSettlement(
            outcome="CANCELLED" if any(s.outcome == "CANCELLED" for s in settled) else "COMPLETED",
            message_id=next((s.message_id for s in settled if s.message_id is not None), None),
        )

    async def _notify(self, trace_id: str) -> None:
        """通知 Agent 面停止该轮执行(限时);未能送达只记日志,随后的硬取消兜底。"""
        try:
            aborted = await asyncio.wait_for(
                self._agent.abort_query(trace_id=trace_id), ABORT_TIMEOUT_SEC
            )
        except Exception:
            log.warning("中止请求未能送达 agent trace_id=%s", trace_id, exc_info=True)
            return
        if not aborted:
            log.info("agent 面报告该轮未在执行 trace_id=%s", trace_id)

    async def _settled_without_turn(self, *, user_id: str, trace_id: str) -> TurnSettlement:
        """该轮不在途:按本人名下的落库结果回答(经会话归属校验,他人同形于不存在)。"""
        row = await self._repo.assistant_message_by_trace(trace_id=trace_id, user_id=user_id)
        if row is None:
            raise TurnNotFound(trace_id)
        status = str(row["status"])
        return TurnSettlement(
            outcome="CANCELLED" if status == "CANCELLED" else "COMPLETED",
            message_id=str(row["id"]),
        )
