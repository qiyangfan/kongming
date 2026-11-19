"""task 看门狗用例:在途任务停滞巡检与夺权收口。

spec「运行中停滞检测与判读」「停滞任务夺权收口」;只消费 TaskRepo 端口,
commit 所有权在巡检循环(整 tick 原子)。
"""

import logging
from datetime import UTC, datetime
from typing import Any, cast

from kongming.task import models as m
from kongming.task.ports import StagnationJudge, TaskRepo

log = logging.getLogger(__name__)

# 看门狗事件账本 kinds(巡检去重标记 + 夺权留痕;web 事件流按原始 kind 兜底展示)
EVENT_WATCHDOG_JUDGED = "watchdog_judged"
EVENT_WATCHDOG_TAKEN_OVER = "watchdog_taken_over"


def _now() -> str:
    return datetime.now(UTC).isoformat()


class TaskWatchdog:
    def __init__(self, repo: TaskRepo) -> None:
        self._repo = repo

    async def patrol_stagnation(self, *, stale_sec: int, judge: StagnationJudge) -> dict[str, int]:
        """巡检一轮:候选(心跳超阈在途任务)两段式决策。

        末事件非判读标记 → 单次判读:「仍进展」落标记事件重置观察窗,
        「停滞」即夺权收口,判读不可用落 unavailable 标记;末事件已是
        判读标记 → 二次超窗机械收口(同一滞留段不再判读)。返回
        候选/判读/收口计数。commit 所有权在巡检循环(整 tick 原子);
        收口谓词未命中(并发迁移先到)即放弃,不抛不重试。"""
        candidates = await self._repo.list_stale_inflight(stale_sec=stale_sec)
        judged = taken = 0
        for row in candidates:
            if str(row.get("last_kind") or "") == EVENT_WATCHDOG_JUDGED:
                # 判读后仍无任何活动且再超阈:机械收口(误判进展/判读不可用的兜底)
                prev = str(row.get("last_verdict") or "")
                prev_label = "判读不可用" if prev == "unavailable" else (prev or "不可用")
                if await self._watchdog_takeover(
                    row, f"看门狗:判读后仍停滞(二次超窗,前判读={prev_label})"
                ):
                    taken += 1
                continue
            verdict = await judge.judge(
                goal=str(row["goal"]),
                events_tail=cast("list[dict[str, object]]", row.get("events_tail") or []),
                stale_sec=stale_sec,
            )
            if verdict is None:
                await self._repo.append_task_event(
                    task_id=str(row["id"]),
                    event={
                        "at": _now(),
                        "kind": EVENT_WATCHDOG_JUDGED,
                        "verdict": "unavailable",
                        "reason": "判读不可用,二次超窗将机械收口",
                    },
                )
                judged += 1
                continue
            if verdict.verdict == "progressing":
                await self._repo.append_task_event(
                    task_id=str(row["id"]),
                    event={
                        "at": _now(),
                        "kind": EVENT_WATCHDOG_JUDGED,
                        "verdict": "progressing",
                        "reason": verdict.reason,
                    },
                )
                judged += 1
                continue
            if await self._watchdog_takeover(row, f"看门狗:判读停滞 —— {verdict.reason}"):
                taken += 1
        return {"candidates": len(candidates), "judged": judged, "taken_over": taken}

    async def _watchdog_takeover(self, row: dict[str, Any], reason: str) -> bool:
        """夺权收口:派发代际自增(旧编排循环回调全失配)+ 条件迁移
        INTERRUPTED(新代数入 CAS 谓词)。谓词未命中返回 False。"""
        task_id = str(row["id"])
        epoch = await self._repo.bump_dispatch_epoch(task_id=task_id)
        if epoch is None:
            return False
        ok = await self._repo.transition_task_system(
            task_id=task_id,
            tenant_id=str(row["tenant_id"]),
            from_status=str(row["status"]),
            to_status=m.T_INTERRUPTED,
            fail_reason=reason,
            event={
                "at": _now(),
                "kind": EVENT_WATCHDOG_TAKEN_OVER,
                "reason": reason,
                "epoch": epoch,
            },
            expect_epoch=epoch,
        )
        if not ok:
            log.info("看门狗收口谓词未命中(任务已被并发迁移): task_id=%s", task_id)
        return ok
