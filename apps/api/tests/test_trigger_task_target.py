# pyright: reportPrivateUsage=false
"""触发器 task 目标:模板校验、两级过滤闸、线程去重、确认门与自动派发。

service 层用真实仓储 + 替身判读/网关覆盖闸门编排;runner 组合根走真实库
集成(确认门=PAUSED+事件,自动派发经 monkeypatch 断言接线)。
"""

import json
import uuid as uuid_mod
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.config import get_settings
from kongming.trigger.ports import EventGateVerdict
from kongming.trigger.repository import SqlTriggerRepo
from kongming.trigger.service import (
    TriggerValidationError,
    filter_pass,
    render_goal,
    thread_key,
    validate_task_template,
)

_TPL = {
    "goal_template": "核查该 issue 描述的数据异常",
    "datasource_key": "sakila",
    "confirm_auto": False,
}


# ── 模板校验(纯函数) ──


def test_template_valid_and_normalized() -> None:
    out = validate_task_template(dict(_TPL))
    assert out["confirm_auto"] is False
    assert out["goal_template"] == _TPL["goal_template"]
    full = validate_task_template(
        {
            **_TPL,
            "budget": {"max_steps": 5},
            "topology": {"kind": "decomposition", "workers": [{"bot_key": "b"}]},
            "thread_field": "issue_no",
            "filter": {"actions": ["opened"], "equals": {"repo": "a/b"}},
        }
    )
    assert full["thread_field"] == "issue_no"


def test_template_rejects() -> None:
    bad: list[object] = [
        None,
        "x",
        {},
        {**_TPL, "goal_template": ""},
        {**_TPL, "goal_template": "x" * 4001},
        {**_TPL, "datasource_key": ""},
        {**_TPL, "unknown_field": 1},
        {**_TPL, "budget": {"max_steps": 0}},
        {**_TPL, "topology": {"kind": "bogus"}},
        {**_TPL, "confirm_auto": "yes"},
        {**_TPL, "thread_field": ""},
        {**_TPL, "filter": {"actions": []}},
        {**_TPL, "filter": {"equals": {"k": {"nested": 1}}}},
    ]
    for t in bad:
        with pytest.raises(TriggerValidationError):
            validate_task_template(t)


# ── 纯函数:过滤谓词/线程键/goal 渲染 ──


def test_filter_pass() -> None:
    assert filter_pass(None, {})
    assert filter_pass({}, {"event": "anything"})
    assert filter_pass({"actions": ["opened"]}, {"event": "opened"})
    assert not filter_pass({"actions": ["opened"]}, {"event": "labeled"})
    assert not filter_pass({"actions": ["opened"]}, {})  # 载荷缺 event=不匹配
    assert filter_pass({"equals": {"repo": "a/b"}}, {"repo": "a/b"})
    assert not filter_pass({"equals": {"repo": "a/b"}}, {"repo": "c/d"})


def test_thread_key_rules() -> None:
    row_github: dict[str, Any] = {"adapter": "github", "task_template": {}}
    assert thread_key(row_github, {"repo": "a/b", "ref": "42"}) == "a/b#42"
    assert thread_key(row_github, {"repo": "a/b"}) == ""
    row_custom: dict[str, Any] = {
        "adapter": "custom",
        "task_template": {"thread_field": "issue_no"},
    }
    assert thread_key(row_custom, {"issue_no": "77"}) == "77"
    assert thread_key(row_custom, {}) == ""
    row_none: dict[str, Any] = {"adapter": "custom", "task_template": {}}
    assert thread_key(row_none, {"anything": "x"}) == ""


def test_render_goal_delimits_payload() -> None:
    goal = render_goal("核查异常", {"summary": "数据对不上"})
    assert goal.startswith("核查异常")
    assert "触发事件材料" in goal
    assert '"summary"' in goal  # 载荷以数据块形态附加


# ── 闸门编排(真实仓储 + 替身判读/网关) ──


class _StubJudge:
    def __init__(self, verdict: EventGateVerdict | None) -> None:
        self.verdict = verdict
        self.calls: list[dict[str, Any]] = []

    async def judge(
        self, *, source: str, summary: str, goal_preview: str
    ) -> EventGateVerdict | None:
        self.calls.append({"source": source, "summary": summary, "preview": goal_preview})
        return self.verdict


class _StubGateway:
    def __init__(self, *, open: bool) -> None:
        self.open = open
        self.events: list[dict[str, Any]] = []

    async def is_open(self, *, task_id: str) -> bool:
        return self.open

    async def append_thread_event(self, *, task_id: str, event: dict[str, Any]) -> bool:
        self.events.append({"task_id": task_id, **event})
        return True


class _StubRunner:
    def __init__(self, *, ok: bool = True) -> None:
        self.ok = ok
        self.calls: list[dict[str, Any]] = []

    async def run_target(self, **kw: Any) -> tuple[bool, str, str]:
        self.calls.append(kw)
        if self.ok:
            return True, f"task-{len(self.calls)}", ""
        return False, "", "boom"


class _NoCipher:
    def encrypt(self, plain: str) -> str:
        return plain

    def decrypt(self, encoded: str) -> str:
        return encoded


async def _mk_task_trigger(session: AsyncSession, *, template: dict[str, Any]) -> dict[str, Any]:
    repo = SqlTriggerRepo(session)
    tenant_id = str(uuid_mod.uuid4())
    tid = await repo.create(
        tenant_id=tenant_id,
        tg_key=f"tt{uuid_mod.uuid4().hex[:8]}",
        name="task 目标",
        type="WEBHOOK",
        target_type="task",
        target_key="",
        cron_expr=None,
        min_interval_sec=60,
        adapter="github",
        notify_url="",
        created_by=str(uuid_mod.uuid4()),
        task_template=template,
    )
    await session.commit()
    row = await repo.get_by_id(trigger_id=tid)
    assert row is not None
    return row


def _svc(session: AsyncSession, judge: Any, gateway: Any, runner: Any) -> Any:
    from kongming.trigger.service import TriggerService

    return TriggerService(
        repo=SqlTriggerRepo(session),
        cipher=_NoCipher(),
        runner=runner,
        target_checker=None,
        event_judge=judge,
        task_gateway=gateway,
    )


_GITHUB_INPUTS = {"event": "opened", "repo": "a/b", "ref": "7", "summary": "issue #7 数据异常"}


async def test_gate_program_filter_skips(session: AsyncSession) -> None:
    row = await _mk_task_trigger(session, template={**_TPL, "filter": {"actions": ["opened"]}})
    judge = _StubJudge(EventGateVerdict(verdict="create", reason="x"))
    svc = _svc(session, judge, _StubGateway(open=False), _StubRunner())
    labeled = {**_GITHUB_INPUTS, "event": "labeled"}
    await svc.fire_prepared(row=row, inputs=labeled)
    assert judge.calls == []  # 程序层筛除,不进语义判读
    runs = (await session.execute(text("SELECT status, error FROM public.sys_trigger_run"))).all()
    assert [(r[0], r[1]) for r in runs] == [("SKIPPED", "程序层过滤:事件不匹配触发器条件")]


async def test_gate_semantic_skip_and_unavailable(session: AsyncSession) -> None:
    row = await _mk_task_trigger(session, template=dict(_TPL))
    svc = _svc(
        session,
        _StubJudge(EventGateVerdict(verdict="skip", reason="闲聊")),
        _StubGateway(open=False),
        _StubRunner(),
    )
    await svc.fire_prepared(row=row, inputs=dict(_GITHUB_INPUTS))
    runs = (await session.execute(text("SELECT status, error FROM public.sys_trigger_run"))).all()
    assert runs[0][0] == "SKIPPED" and "语义判读否决" in str(runs[0][1])

    svc2 = _svc(session, _StubJudge(None), _StubGateway(open=False), _StubRunner())
    await svc2.fire_prepared(row=row, inputs=dict(_GITHUB_INPUTS))
    runs = (
        await session.execute(
            text("SELECT status, error FROM public.sys_trigger_run ORDER BY created_at")
        )
    ).all()
    assert runs[-1][0] == "SKIPPED" and "判读不可用" in str(runs[-1][1])


async def test_gate_thread_dedup_appends_to_open_task(session: AsyncSession) -> None:
    row = await _mk_task_trigger(session, template=dict(_TPL))
    gateway = _StubGateway(open=True)
    svc = _svc(
        session,
        _StubJudge(EventGateVerdict(verdict="create", reason="值得处理")),
        gateway,
        _StubRunner(),
    )
    # 预置同线程键的成功 run(线程键载体形态与 _fire 写入一致)
    await session.execute(
        text(
            "INSERT INTO public.sys_trigger_run"
            " (id, tenant_id, trigger_id, status, error, duration_ms, target_run_ref, summary)"
            " VALUES (gen_random_uuid(), :t, :g, 'SUCCESS', '', 1, 'task-open-1', :s)"
        ),
        {
            "t": row["tenant_id"],
            "g": row["id"],
            "s": json.dumps({"thread_key": "a/b#7", "task_id": "task-open-1"}),
        },
    )
    await session.commit()
    await svc.fire_prepared(row=row, inputs=dict(_GITHUB_INPUTS))
    assert gateway.events and gateway.events[0]["task_id"] == "task-open-1"
    assert gateway.events[0]["thread_key"] == "a/b#7"
    runs = (
        await session.execute(
            text("SELECT status, error FROM public.sys_trigger_run WHERE status='SKIPPED'")
        )
    ).all()
    assert runs and "已有在途任务" in str(runs[0][1])


async def test_gate_pass_and_summary_carries_thread_key(session: AsyncSession) -> None:
    row = await _mk_task_trigger(session, template=dict(_TPL))
    runner = _StubRunner()
    svc = _svc(
        session,
        _StubJudge(EventGateVerdict(verdict="create", reason="值得处理")),
        _StubGateway(open=False),
        runner,
    )
    await svc.fire_prepared(row=row, inputs=dict(_GITHUB_INPUTS))
    assert runner.calls and runner.calls[0]["template"] == dict(_TPL)
    r = (
        await session.execute(
            text(
                "SELECT status, summary, target_run_ref FROM public.sys_trigger_run"
                " WHERE status='SUCCESS'"
            )
        )
    ).first()
    assert r is not None
    summary = json.loads(str(r[1]))
    assert summary == {"thread_key": "a/b#7", "task_id": str(r[2])}


async def test_cron_task_target_bypasses_gate(session: AsyncSession) -> None:
    repo = SqlTriggerRepo(session)
    tid = await repo.create(
        tenant_id=str(uuid_mod.uuid4()),
        tg_key=f"tc{uuid_mod.uuid4().hex[:8]}",
        name="cron task",
        type="CRON",
        target_type="task",
        target_key="",
        cron_expr="0 9 * * *",
        min_interval_sec=60,
        adapter="custom",
        notify_url="",
        created_by=str(uuid_mod.uuid4()),
        task_template=dict(_TPL),
    )
    await session.commit()
    row = await repo.get_by_id(trigger_id=tid)
    assert row is not None
    judge = _StubJudge(None)  # 判读不可用也不该被调用(CRON 免闸)
    runner = _StubRunner()
    svc = _svc(session, judge, _StubGateway(open=False), runner)
    await svc.fire_cron(trigger_id=tid)
    assert judge.calls == []
    assert runner.calls and runner.calls[0]["channel"] == "cron"


# ── runner 组合根集成(真实库:确认门/自动派发接线) ──


async def test_runner_task_confirm_gate_integration(
    session: AsyncSession, make_user: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kongming.auth.seed import seed_all
    from kongming.trigger.dependencies import make_trigger_service as _make_service
    from kongming.uuid7 import uuid7

    await seed_all(session, get_settings())
    uid = await make_user("tt_user")
    tid = str(
        (
            await session.execute(
                text("SELECT id FROM public.sys_tenant WHERE tenant_key='default'")
            )
        ).scalar_one()
    )
    await session.execute(
        text(
            "INSERT INTO public.sys_datasource"
            " (id, datasource_key, tenant_id, name, host, port, database,"
            "  username, password_enc)"
            " VALUES (gen_random_uuid(), 'tt_ds', CAST(:t AS uuid), '集成测试源',"
            "  'h', 5432, 'd', 'u', 'x')"
        ),
        {"t": tid},
    )
    await session.commit()

    repo = SqlTriggerRepo(session)
    tpl = {**_TPL, "datasource_key": "tt_ds", "confirm_auto": False, "topology": {"kind": "bogus"}}
    tpl_valid: dict[str, Any] = {k: v for k, v in tpl.items() if k != "topology"}
    tid_trigger = await repo.create(
        tenant_id=tid,
        tg_key=f"ti{uuid_mod.uuid4().hex[:8]}",
        name="集成",
        type="WEBHOOK",
        target_type="task",
        target_key="",
        cron_expr=None,
        min_interval_sec=60,
        adapter="github",
        notify_url="",
        created_by=uid,
        task_template=tpl_valid,
    )
    await session.commit()
    row = await repo.get_by_id(trigger_id=tid_trigger)
    assert row is not None

    scheduled: list[str] = []
    import kongming.task.dispatch as _task_dispatch
    from kongming.chat.agent_client import AgentClient as _AC

    async def _fake_schedule(task_id: str) -> int | None:
        scheduled.append(task_id)
        return 1

    async def _fake_judge_event(
        self: Any, *, source: str, summary: str, goal_preview: str
    ) -> dict[str, object] | None:
        return {"verdict": "create", "reason": "测试放行"}

    monkeypatch.setattr(_task_dispatch, "schedule_task_run", _fake_schedule)
    monkeypatch.setattr(_AC, "judge_event", _fake_judge_event)
    svc = _make_service(session)
    await svc.fire_prepared(row=row, inputs=dict(_GITHUB_INPUTS))
    await session.commit()

    runs = (
        await session.execute(
            text("SELECT status, target_run_ref FROM public.sys_trigger_run WHERE status='SUCCESS'")
        )
    ).all()
    assert runs and runs[0][1]  # 立项成功,ref=任务 id
    task_id = str(runs[0][1])
    task = (
        await session.execute(
            text("SELECT status, fail_reason FROM public.sys_agent_task WHERE id=CAST(:i AS uuid)"),
            {"i": task_id},
        )
    ).first()
    assert task is not None
    assert task[0] == "PAUSED"  # 确认门:落 PAUSED 等归属人恢复
    assert "待确认" in str(task[1])
    events = (
        await session.execute(
            text("SELECT events->-1->>'kind' FROM public.sys_agent_task WHERE id=CAST(:i AS uuid)"),
            {"i": task_id},
        )
    ).scalar_one()
    assert events == "trigger_awaiting_confirm"
    assert scheduled == []  # 缺省确认模式不派发
    _ = uuid7  # 保留导入面
