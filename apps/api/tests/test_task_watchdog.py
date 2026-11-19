# pyright: reportPrivateUsage=false
"""看门狗巡检:两段式决策、判读去重、夺权收口与二次超窗机械兜底。

service 层用替身仓储与替身判读端口覆盖决策编排;仓储 SQL 查询走真实库。
"""

import json
from collections.abc import Callable
from typing import Any, cast

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.task.ports import StagnationVerdict, TaskRepo
from kongming.task.watchdog import TaskWatchdog


class _FakeRepo:
    """最小仓储替身:候选行 + 事件追加/夺权调用记录。"""

    def __init__(self, candidates: list[dict[str, Any]] | None = None) -> None:
        self.candidates = candidates or []
        self.events: list[tuple[str, dict[str, object]]] = []
        self.transitions: list[dict[str, Any]] = []
        self.bumps = 0
        self.transition_ok = True

    async def list_stale_inflight(self, *, stale_sec: int) -> list[dict[str, Any]]:
        return self.candidates

    async def append_task_event(self, *, task_id: str, event: dict[str, object]) -> None:
        self.events.append((task_id, event))

    async def bump_dispatch_epoch(self, *, task_id: str) -> int | None:
        self.bumps += 1
        if not self.candidates:
            return None
        return int(self.candidates[0]["dispatch_epoch"]) + 1

    async def transition_task_system(self, **kw: Any) -> bool:
        self.transitions.append(kw)
        return self.transition_ok


class _FakeJudge:
    def __init__(self, verdict: StagnationVerdict | None) -> None:
        self.verdict = verdict
        self.calls: list[dict[str, Any]] = []

    async def judge(
        self, *, goal: str, events_tail: list[dict[str, object]], stale_sec: int
    ) -> StagnationVerdict | None:
        self.calls.append({"goal": goal, "tail": events_tail, "stale": stale_sec})
        return self.verdict


def _row(**over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": "t1",
        "tenant_id": "ten1",
        "goal": "分析华东区销售异常",
        "status": "RUNNING",
        "dispatch_epoch": 3,
        "last_kind": "step_started",
        "last_verdict": "",
        "events_tail": [{"at": "2026-01-01T00:00:00+00:00", "kind": "step_started"}],
    }
    row.update(over)
    return row


# ── 两段式决策 ──


async def test_patrol_judges_candidate_without_marker() -> None:
    repo = _FakeRepo([_row()])
    judge = _FakeJudge(StagnationVerdict(verdict="progressing", reason="步骤在推进"))
    svc = TaskWatchdog(cast("TaskRepo", repo))

    summary = await svc.patrol_stagnation(stale_sec=300, judge=judge)

    assert len(judge.calls) == 1
    assert judge.calls[0]["goal"] == "分析华东区销售异常"
    assert judge.calls[0]["stale"] == 300
    # 仍进展:落标记事件重置观察窗,不夺权
    assert summary == {"candidates": 1, "judged": 1, "taken_over": 0}
    assert len(repo.events) == 1
    assert repo.events[0][1]["kind"] == "watchdog_judged"
    assert repo.events[0][1]["verdict"] == "progressing"
    assert repo.transitions == []


async def test_patrol_takeover_on_stagnant_verdict() -> None:
    repo = _FakeRepo([_row()])
    judge = _FakeJudge(StagnationVerdict(verdict="stagnant", reason="反复同一查询无产出"))
    svc = TaskWatchdog(cast("TaskRepo", repo))

    summary = await svc.patrol_stagnation(stale_sec=300, judge=judge)

    assert summary["taken_over"] == 1
    assert repo.bumps == 1
    assert len(repo.transitions) == 1
    tr = repo.transitions[0]
    assert tr["from_status"] == "RUNNING"
    assert tr["to_status"] == "INTERRUPTED"
    assert tr["expect_epoch"] == 4  # bump 后新代数入 CAS 谓词
    assert "判读停滞" in str(tr["fail_reason"])
    assert repo.events == []  # 收口走迁移事件,不另追加


async def test_patrol_takeover_from_planning() -> None:
    repo = _FakeRepo([_row(status="PLANNING")])
    judge = _FakeJudge(StagnationVerdict(verdict="stagnant", reason="规划期即卡死"))
    svc = TaskWatchdog(cast("TaskRepo", repo))

    summary = await svc.patrol_stagnation(stale_sec=300, judge=judge)

    assert summary["taken_over"] == 1
    assert repo.transitions[0]["from_status"] == "PLANNING"


async def test_patrol_judge_unavailable_marks_and_spares_task() -> None:
    repo = _FakeRepo([_row()])
    judge = _FakeJudge(None)
    svc = TaskWatchdog(cast("TaskRepo", repo))

    summary = await svc.patrol_stagnation(stale_sec=300, judge=judge)

    # 判读不可用:落标记事件(下一窗机械收口),不收口不默认放行
    assert summary == {"candidates": 1, "judged": 1, "taken_over": 0}
    assert repo.events[0][1]["verdict"] == "unavailable"
    assert repo.transitions == []


async def test_patrol_mechanical_takeover_after_marker() -> None:
    # 末事件已是判读标记且再超阈:不再判读,机械收口
    repo = _FakeRepo([_row(last_kind="watchdog_judged", last_verdict="progressing")])
    judge = _FakeJudge(StagnationVerdict(verdict="progressing", reason="不应被调用"))
    svc = TaskWatchdog(cast("TaskRepo", repo))

    summary = await svc.patrol_stagnation(stale_sec=300, judge=judge)

    assert judge.calls == []  # 同一滞留段判读去重
    assert summary == {"candidates": 1, "judged": 0, "taken_over": 1}
    reason = str(repo.transitions[0]["fail_reason"])
    assert "二次超窗" in reason
    assert "progressing" in reason  # 前判读结论入因,可审计


async def test_patrol_mechanical_takeover_after_unavailable_marker() -> None:
    repo = _FakeRepo([_row(last_kind="watchdog_judged", last_verdict="unavailable")])
    judge = _FakeJudge(None)
    svc = TaskWatchdog(cast("TaskRepo", repo))

    summary = await svc.patrol_stagnation(stale_sec=300, judge=judge)

    assert judge.calls == []
    assert summary["taken_over"] == 1
    assert "判读不可用" in str(repo.transitions[0]["fail_reason"])


async def test_patrol_cas_miss_abandons_takeover() -> None:
    repo = _FakeRepo([_row()])
    repo.transition_ok = False  # 并发迁移先到:谓词未命中
    judge = _FakeJudge(StagnationVerdict(verdict="stagnant", reason="停滞"))
    svc = TaskWatchdog(cast("TaskRepo", repo))

    summary = await svc.patrol_stagnation(stale_sec=300, judge=judge)

    assert summary["taken_over"] == 0  # 放弃不抛,任务保持并发方写入的状态


async def test_patrol_empty_candidates_is_noop() -> None:
    repo = _FakeRepo([])
    judge = _FakeJudge(None)
    svc = TaskWatchdog(cast("TaskRepo", repo))

    summary = await svc.patrol_stagnation(stale_sec=300, judge=judge)

    assert summary == {"candidates": 0, "judged": 0, "taken_over": 0}
    assert repo.bumps == 0


# ── 仓储 SQL(真实库) ──


async def test_list_stale_inflight_filters_by_heartbeat(
    session: AsyncSession, make_user: Any
) -> None:
    from kongming.auth.seed import seed_all
    from kongming.config import get_settings
    from kongming.task.system_repo import SqlTaskSystemRepo as SqlTaskRepo
    from kongming.uuid7 import uuid7

    await seed_all(session, get_settings())
    uid = await make_user("wd_user")
    tid = str(
        (
            await session.execute(
                text("SELECT id FROM public.sys_tenant WHERE tenant_key='default'")
            )
        ).scalar_one()
    )

    async def _insert(
        task_id: str, status: str, *, age_sec: int, last_kind: str = "", events_json: str = ""
    ) -> None:
        events = events_json or (
            f'[{{"at":"2026-01-01T00:00:00+00:00","kind":"{last_kind or "step_started"}"}}]'
        )
        await session.execute(
            text(
                "INSERT INTO public.sys_agent_task"
                " (id, tenant_id, user_id, goal, status, datasource_key, budget_config,"
                "  budget_used, events, dispatch_epoch, updated_at)"
                " VALUES (CAST(:i AS uuid), CAST(:t AS uuid), CAST(:u AS uuid), :g, :st,"
                "  'ds', '{}', '{}', CAST(:ev AS jsonb), 1,"
                "  now() - make_interval(secs => :age))"
            ),
            {
                "i": task_id,
                "t": tid,
                "u": uid,
                "g": f"目标{task_id}",
                "st": status,
                "ev": events,
                "age": age_sec,
            },
        )

    stale_running = str(uuid7())
    fresh_running = str(uuid7())
    stale_paused = str(uuid7())
    stale_planning = str(uuid7())
    # 10 条时间线:尾窗只取最近 8 条,且按时间升序(晚在后,判读 prompt 口径)
    ten_events = json.dumps(
        [
            {"at": f"2026-01-01T00:{m:02d}:{s:02d}+00:00", "kind": f"e{m}{s}"}
            for m in range(5)
            for s in range(2)
        ],
        ensure_ascii=False,
    )
    await _insert(stale_running, "RUNNING", age_sec=1200, events_json=ten_events)
    await _insert(fresh_running, "RUNNING", age_sec=30)
    await _insert(stale_paused, "PAUSED", age_sec=1200)  # 设计等待态:任何心跳都不进候选
    await _insert(stale_planning, "PLANNING", age_sec=1200, last_kind="watchdog_judged")

    await session.commit()
    rows = await SqlTaskRepo(session).list_stale_inflight(stale_sec=300)
    ids = {str(r["id"]) for r in rows}
    assert stale_running in ids
    assert stale_planning in ids
    assert fresh_running not in ids
    assert stale_paused not in ids
    row = next(r for r in rows if str(r["id"]) == stale_planning)
    assert row["last_kind"] == "watchdog_judged"
    assert isinstance(row["events_tail"], list) and row["events_tail"]
    tail_raw = next(r for r in rows if str(r["id"]) == stale_running)["events_tail"]
    assert isinstance(tail_raw, list)
    tail = cast("list[dict[str, object]]", tail_raw)
    assert len(tail) == 8
    kinds = [str(e["kind"]) for e in tail]
    assert kinds == [f"e{m}{s}" for m in range(5) for s in range(2)][2:]  # 舍头 2 条,升序晚在后


# ── AgentClient 判读回调降级契约(不触真实 agent) ──

_REAL_ASYNC_CLIENT = httpx.AsyncClient  # 模块导入期捕获,防 monkeypatch 迭代污染


def _patch_http(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    """替身传输注入:AgentClient 自建 AsyncClient 处统一挂 MockTransport。"""

    def _factory(**kw: Any) -> httpx.AsyncClient:
        return _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler), **kw)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)


def _client() -> Any:
    from kongming.chat.agent_client import AgentClient
    from kongming.config import get_settings

    return AgentClient(get_settings())


async def test_judge_stagnation_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_http(
        monkeypatch,
        lambda request: httpx.Response(
            200, json={"judged": True, "verdict": "stagnant", "reason": "无产出"}
        ),
    )
    out = await _client().judge_stagnation(goal="g", events=[], stale_sec=300)
    assert out == {"verdict": "stagnant", "reason": "无产出"}


def _raise_down(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("down")  # 不可达


async def test_judge_stagnation_degrades_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    handlers: list[Callable[[httpx.Request], httpx.Response]] = [
        _raise_down,
        lambda request: httpx.Response(503),  # 非 200
        lambda request: httpx.Response(200, text="not-json"),  # 坏报文
        lambda request: httpx.Response(200, json={"judged": False}),  # agent 降级
        lambda request: httpx.Response(200, json={"judged": True, "verdict": "??"}),  # 越界
    ]
    for handler in handlers:
        _patch_http(monkeypatch, handler)
        out = await client.judge_stagnation(goal="g", events=[], stale_sec=300)
        assert out is None
