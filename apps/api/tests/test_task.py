# pyright: reportPrivateUsage=false
"""覆盖任务状态机、权限、归属、审批和预算退出。

测试通过真实 HTTP 调用内部回调；派发时机由记录器断言，避免嵌套 ASGI 请求导致传输层死锁。
"""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.test_chat_query import _auth, _bind, _default_tenant_id, _login
from tests.test_system_admin import _admin

INTERNAL = {"X-Internal-Token": "test-internal-token"}


# ── 纯函数:状态机/证据约束 ──


def test_task_transition_whitelist() -> None:
    from kongming.task import models as m

    assert m.transition("RUNNING", "PAUSED") == "PAUSED"
    with pytest.raises(m.InvalidTransition):
        m.transition("CANCELLED", "RUNNING")  # 终态不复活
    with pytest.raises(m.InvalidTransition):
        m.transition("DONE", "RUNNING")
    with pytest.raises(m.InvalidTransition):
        m.transition("PAUSED", "DONE")  # 未执行不得直接完成


def test_step_transition_and_evidence_rules() -> None:
    from kongming.task import models as m

    assert m.step_transition("PENDING", "RUNNING") == "RUNNING"
    with pytest.raises(m.InvalidTransition):
        m.step_transition("DONE", "RUNNING")  # 已终态步骤不可改写
    with pytest.raises(m.InvalidTransition):
        m.step_transition("PENDING", "DONE")  # 未执行不得直接完成
    # 审批边:AWAITING → PENDING(批准回队)/ SKIPPED(拒绝)
    assert m.step_transition("AWAITING_APPROVAL", "PENDING") == "PENDING"
    assert m.step_transition("AWAITING_APPROVAL", "SKIPPED") == "SKIPPED"
    # 证据驱动:无证据不得 DONE
    assert m.validate_result_status("DONE", {}) is not None
    assert m.validate_result_status("DONE", {"trace_id": "t"}) is None
    assert m.validate_result_status("FAILED", {}) is None


def test_over_budget() -> None:
    from kongming.task import models as m

    config = m.normalize_budget({"max_steps": 2})
    assert m.over_budget({"steps": 1}, config) is None
    assert m.over_budget({"steps": 2}, config) == "步数预算耗尽"


# ── 助手 ──


async def _user_with_task_perms(
    client: AsyncClient,
    s: AsyncSession,
    make_user: Any,
    username: str = "tasker",
    *,
    codes: tuple[str, ...] = ("task:create", "task:read", "task:manage"),
) -> tuple[str, str]:
    from kongming.auth.seed import seed_all
    from kongming.config import get_settings

    await seed_all(s, get_settings())
    uid = await make_user(username)
    rid = (
        await s.execute(
            text(
                "INSERT INTO public.sys_role (id, role_key, role_name, data_scope)"
                " VALUES (gen_random_uuid(), :k, :k, 'SELF') RETURNING id"
            ),
            {"k": f"task_{username}"},
        )
    ).scalar_one()
    for code in codes:
        await s.execute(
            text(
                "INSERT INTO public.sys_permission (id, perm_code, perm_name)"
                " VALUES (gen_random_uuid(), :c, :c) ON CONFLICT (perm_code) DO NOTHING"
            ),
            {"c": code},
        )
        await s.execute(
            text(
                "INSERT INTO public.sys_role_permission (role_id, permission_id)"
                " SELECT :r, p.id FROM public.sys_permission p WHERE p.perm_code = :c"
                " ON CONFLICT DO NOTHING"
            ),
            {"r": rid, "c": code},
        )
    await _bind(s, "sys_user_role", uid, str(rid))
    await _bind(s, "sys_user_tenant", uid, await _default_tenant_id(s))
    return uid, await _login(client, username)


async def _drive_orchestration(
    client: AsyncClient, tid: str, tk: str, steps_script: list[dict[str, object]]
) -> str:
    """内联驱动一次编排(plan → 逐步 start/result;真实 HTTP 全链)。
    返回终态(DONE/PAUSED/AWAITING_APPROVAL);审批/预算由控制面决策驱动。
    plan 409 = 续跑(计划已在账),读既有步骤与真实编排器同径。"""

    h = INTERNAL
    r = await client.post(
        "/internal/task/plan",
        headers=h,
        json={"task_id": tid, "tenant_key": tk, "steps": steps_script},
    )
    if r.status_code == 409:
        r2 = await client.get(
            "/internal/task/steps", headers=h, params={"task_id": tid, "tenant_key": tk}
        )
        steps = r2.json()["steps"]
    elif r.status_code != 200:
        raise RuntimeError(f"plan {r.status_code}: {r.text[:200]}")
    else:
        steps = r.json()["steps"]
    for step in steps:
        d = await client.post(
            "/internal/task/step-start",
            headers=h,
            json={"task_id": tid, "tenant_key": tk, "step_id": step["id"]},
        )
        if d.status_code != 200:
            raise RuntimeError(f"step-start {d.status_code}: {d.text[:200]}")
        decision = str(d.json().get("decision"))
        if decision == "pause":
            return "PAUSED"
        if decision == "await_approval":
            return "AWAITING_APPROVAL"
        if decision == "abort":
            # 控制面判定任务不可再执行(取消/终态):如实返回现态口径
            return f"ABORT:{d.json().get('task_status')}"
        if decision != "execute":
            continue  # skip(DONE/已跳过步骤):推进下一步
        rr = await client.post(
            "/internal/task/step-result",
            headers=h,
            json={
                "task_id": tid,
                "tenant_key": tk,
                "step_id": step["id"],
                "status": "DONE",
                "evidence": {"trace_id": "tr", "summary": "ok"},
                "summary": "ok",
            },
        )
        if rr.status_code != 200:
            raise RuntimeError(f"step-result {rr.status_code}: {rr.text[:200]}")
    # 终态收口与真实编排器同径:全部 DONE 方可 DONE(spec),否则如实 FAILED
    rs = await client.get(
        "/internal/task/steps", headers=h, params={"task_id": tid, "tenant_key": tk}
    )
    statuses = [str(s["status"]) for s in rs.json()["steps"]]
    final = "DONE" if statuses and all(s == "DONE" for s in statuses) else "FAILED"
    rf = await client.post(
        "/internal/task/finish",
        headers=h,
        json={
            "task_id": tid,
            "tenant_key": tk,
            "status": final,
            "fail_reason": None if final == "DONE" else "部分步骤未完成(测试驱动如实收口)",
        },
    )
    if rf.status_code != 204:
        raise RuntimeError(f"finish {rf.status_code}: {rf.text[:200]}")
    return final


async def _create_task(client: AsyncClient, token: str, **over: Any) -> str:
    body: dict[str, Any] = {"goal": "梳理华东销售异常"}
    body.update(over)
    r = await client.post("/tasks", json=body, headers=_auth(token))
    assert r.status_code == 202, r.text
    return r.json()["task_id"]


async def _wait_status(
    client: AsyncClient, token: str, task_id: str, statuses: tuple[str, ...]
) -> dict[str, Any]:
    import asyncio

    for _ in range(100):
        r = await client.get(f"/tasks/{task_id}", headers=_auth(token))
        if r.status_code == 200 and r.json()["task"]["status"] in statuses:
            return r.json()
        await asyncio.sleep(0.02)
    raise AssertionError(f"任务未在预算内到达 {statuses}")


@pytest.fixture
def _no_dispatch(monkeypatch: pytest.MonkeyPatch) -> list[str]:  # pyright: ignore[reportUnusedFunction]
    """将派发替换为记录器，由测试显式调用回调，避免在同一 app 中嵌套请求。"""
    from kongming.task import router as task_router

    calls: list[str] = []

    def _record(*, task_id: str, epoch: int | None = None) -> None:
        calls.append(task_id)

    monkeypatch.setattr(task_router, "_schedule_run", _record)
    return calls


# ── HTTP 集成 ──


@pytest.mark.asyncio
async def test_create_requires_permission(
    client: AsyncClient, session: AsyncSession, make_user: Any
) -> None:
    _, token = await _admin(client, session, make_user)
    await session.execute(text("DELETE FROM public.sys_role_permission"))
    await session.commit()
    r = await client.post("/tasks", json={"goal": "梳理销售异常"}, headers=_auth(token))
    assert r.status_code == 403
    from kongming.auth.seed import seed_all
    from kongming.config import get_settings

    await seed_all(session, get_settings())


@pytest.mark.asyncio
async def test_full_lifecycle_done(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Any,
    _no_dispatch: list[str],
) -> None:
    """创建 → 计划 → 逐步执行 → DONE;步骤证据与事件时间线落库。"""
    uid, token = await _user_with_task_perms(client, session, make_user)
    _ = uid
    task_id = await _create_task(client, token)
    assert _no_dispatch[-1] == task_id  # 派发被触发(记录断言)
    steps: list[dict[str, object]] = [
        {"goal": "查询华东销量", "require_confirm": False},
        {"goal": "汇总异常点", "require_confirm": False},
    ]
    final = await _drive_orchestration(client, task_id, "default", steps)
    assert final == "DONE"
    detail = await _wait_status(client, token, task_id, ("DONE",))
    assert [s["status"] for s in detail["steps"]] == ["DONE", "DONE"]
    assert detail["steps"][0]["evidence"]["trace_id"] == "tr"  # 有证据
    assert any(e["kind"] == "planned" for e in detail["events"])
    assert any(e["kind"] == "finished" for e in detail["events"])
    r2 = await client.get("/tasks", headers=_auth(token))
    assert r2.json()["total"] == 1


@pytest.mark.asyncio
async def test_ownership_404_same_shape(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Any,
    _no_dispatch: list[str],
) -> None:
    _, token_a = await _user_with_task_perms(client, session, make_user, "ownA")
    _, token_b = await _user_with_task_perms(client, session, make_user, "ownB")
    task_id = await _create_task(client, token_a)
    await _drive_orchestration(
        client, task_id, "default", [{"goal": "s1", "require_confirm": False}]
    )
    await _wait_status(client, token_a, task_id, ("DONE",))
    for method, path in (
        ("GET", f"/tasks/{task_id}"),
        ("POST", f"/tasks/{task_id}/pause"),
        ("POST", f"/tasks/{task_id}/cancel"),
    ):
        call = getattr(client, method.lower())
        resp = await call(path, headers=_auth(token_b))
        assert resp.status_code == 404, f"{method} {path} → {resp.status_code}"


@pytest.mark.asyncio
async def test_illegal_transition_conflict(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Any,
    _no_dispatch: list[str],
) -> None:
    """终态不复活:DONE → resume 409 且状态保持。"""
    _, token = await _user_with_task_perms(client, session, make_user, "illA")
    task_id = await _create_task(client, token)
    await _drive_orchestration(
        client, task_id, "default", [{"goal": "s1", "require_confirm": False}]
    )
    await _wait_status(client, token, task_id, ("DONE",))
    r2 = await client.post(f"/tasks/{task_id}/resume", headers=_auth(token))
    assert r2.status_code == 409
    d = (await client.get(f"/tasks/{task_id}", headers=_auth(token))).json()
    assert d["task"]["status"] == "DONE"


@pytest.mark.asyncio
async def test_approval_gate_flow(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Any,
    _no_dispatch: list[str],
) -> None:
    """需确认步骤 → AWAITING_APPROVAL 挂起 → 批准 → 续跑 → DONE。"""
    _, token = await _user_with_task_perms(client, session, make_user, "apprA")
    task_id = await _create_task(client, token)
    steps: list[dict[str, object]] = [
        {"goal": "铺垫查询", "require_confirm": False},
        {"goal": "高危归因", "require_confirm": True},
        {"goal": "收尾汇总", "require_confirm": False},
    ]
    first = await _drive_orchestration(client, task_id, "default", steps)
    assert first == "AWAITING_APPROVAL"
    detail = await _wait_status(client, token, task_id, ("AWAITING_APPROVAL",))
    pending = [s for s in detail["steps"] if s["status"] == "AWAITING_APPROVAL"]
    assert len(pending) == 1
    r2 = await client.post(
        f"/tasks/{task_id}/steps/{pending[0]['id']}/approve",
        json={"approve": True},
        headers=_auth(token),
    )
    assert r2.status_code == 200, r2.text
    second = await _drive_orchestration(client, task_id, "default", steps)  # 续跑(plan 409)
    assert second == "DONE"
    detail = await _wait_status(client, token, task_id, ("DONE",))
    assert all(s["status"] == "DONE" for s in detail["steps"])


@pytest.mark.asyncio
async def test_budget_pause_honest(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Any,
    _no_dispatch: list[str],
) -> None:
    """步数预算:第 3 步启动前控制面判超限 → PAUSED(诚实收口,非 DONE)。"""
    _, token = await _user_with_task_perms(client, session, make_user, "budA")
    task_id = await _create_task(client, token, budget={"max_steps": 2})
    final = await _drive_orchestration(
        client,
        task_id,
        "default",
        [{"goal": f"s{i}", "require_confirm": False} for i in (1, 2, 3)],
    )
    assert final == "PAUSED"
    detail = await _wait_status(client, token, task_id, ("PAUSED",))
    assert [s["status"] for s in detail["steps"]].count("DONE") == 2
    assert any(e["kind"] == "budget_paused" for e in detail["events"])
    assert detail["task"]["status"] == "PAUSED"  # 不出现 DONE


async def _step_start(client: AsyncClient, tid: str, tk: str, sid: str) -> dict[str, object]:
    r = await client.post(
        "/internal/task/step-start",
        headers=INTERNAL,
        json={"task_id": tid, "tenant_key": tk, "step_id": sid},
    )
    assert r.status_code == 200, r.text
    return dict(r.json())


async def _step_result_done(client: AsyncClient, tid: str, tk: str, sid: str) -> None:
    r = await client.post(
        "/internal/task/step-result",
        headers=INTERNAL,
        json={
            "task_id": tid,
            "tenant_key": tk,
            "step_id": sid,
            "status": "DONE",
            "evidence": {"trace_id": "tr", "summary": "ok"},
            "summary": "ok",
        },
    )
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_cancel_stops_orchestration_and_blocks_done(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Any,
    _no_dispatch: list[str],
) -> None:
    """P0 终态复活链:取消后 step-start 必须 abort、finish DONE 必须 409、
    任务保持 CANCELLED(收口器白名单不可越权改写)。"""
    _, token = await _user_with_task_perms(client, session, make_user, "cancelA")
    task_id = await _create_task(client, token)
    steps: list[dict[str, object]] = [
        {"goal": "s1", "require_confirm": False},
        {"goal": "s2", "require_confirm": False},
    ]
    r = await client.post(
        "/internal/task/plan",
        headers=INTERNAL,
        json={"task_id": task_id, "tenant_key": "default", "steps": steps},
    )
    assert r.status_code == 200, r.text
    ids = [s["id"] for s in r.json()["steps"]]
    await _step_start(client, task_id, "default", ids[0])
    await _step_result_done(client, task_id, "default", ids[0])
    rc = await client.post(f"/tasks/{task_id}/cancel", headers=_auth(token))
    assert rc.status_code == 200
    d = await _step_start(client, task_id, "default", ids[1])
    assert d["decision"] == "abort", d
    assert d["task_status"] == "CANCELLED"
    rf = await client.post(
        "/internal/task/finish",
        headers=INTERNAL,
        json={"task_id": task_id, "tenant_key": "default", "status": "DONE"},
    )
    assert rf.status_code == 409
    detail = (await client.get(f"/tasks/{task_id}", headers=_auth(token))).json()
    assert detail["task"]["status"] == "CANCELLED"  # 终态不复活
    assert any(e["kind"] == "invalid_transition" for e in detail["events"])  # 审计留痕


@pytest.mark.asyncio
async def test_finish_done_requires_all_steps_terminal(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Any,
    _no_dispatch: list[str],
) -> None:
    """DONE 前置校验:存在 PENDING 步骤时 finish DONE 拒绝(审批闸不可被
    finish 旁路),任务保持 RUNNING。"""
    _, token = await _user_with_task_perms(client, session, make_user, "doneA")
    task_id = await _create_task(client, token)
    r = await client.post(
        "/internal/task/plan",
        headers=INTERNAL,
        json={
            "task_id": task_id,
            "tenant_key": "default",
            "steps": [
                {"goal": "s1", "require_confirm": False},
                {"goal": "s2", "require_confirm": False},
            ],
        },
    )
    ids = [s["id"] for s in r.json()["steps"]]
    await _step_start(client, task_id, "default", ids[0])
    await _step_result_done(client, task_id, "default", ids[0])
    rf = await client.post(
        "/internal/task/finish",
        headers=INTERNAL,
        json={"task_id": task_id, "tenant_key": "default", "status": "DONE"},
    )
    assert rf.status_code == 409
    detail = (await client.get(f"/tasks/{task_id}", headers=_auth(token))).json()
    assert detail["task"]["status"] == "RUNNING"


@pytest.mark.asyncio
async def test_finish_same_state_idempotent(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Any,
    _no_dispatch: list[str],
) -> None:
    """同态 finish 幂等收敛(agent 侧先收口,段三复收口是常态链路):
    第二次 FAILED 收口 204、不重复迁移、不留 invalid_transition 噪音事件。"""
    _, token = await _user_with_task_perms(client, session, make_user, "doneA")
    task_id = await _create_task(client, token)
    r = await client.post(
        "/internal/task/plan",
        headers=INTERNAL,
        json={
            "task_id": task_id,
            "tenant_key": "default",
            "steps": [
                {"goal": "s1", "require_confirm": False},
                {"goal": "s2", "require_confirm": False},
            ],
        },
    )
    ids = [s["id"] for s in r.json()["steps"]]
    await _step_start(client, task_id, "default", ids[0])
    await _step_result_done(client, task_id, "default", ids[0])
    rf = await client.post(
        "/internal/task/finish",
        headers=INTERNAL,
        json={
            "task_id": task_id,
            "tenant_key": "default",
            "status": "FAILED",
            "fail_reason": "部分步骤未完成",
        },
    )
    assert rf.status_code == 204
    rf2 = await client.post(
        "/internal/task/finish",
        headers=INTERNAL,
        json={
            "task_id": task_id,
            "tenant_key": "default",
            "status": "FAILED",
            "fail_reason": "部分步骤未完成",
        },
    )
    assert rf2.status_code == 204  # 同态收敛,不再 409
    detail = (await client.get(f"/tasks/{task_id}", headers=_auth(token))).json()
    kinds = [e["kind"] for e in detail["events"]]
    assert kinds.count("finished") == 1
    assert "invalid_transition" not in kinds


@pytest.mark.asyncio
async def test_resume_blocked_while_awaiting_approval(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Any,
    _no_dispatch: list[str],
) -> None:
    """审批闸不可经 resume 旁路:AWAITING_APPROVAL 态 resume 409 且状态保持;
    拒绝审批(步骤 SKIPPED)后续跑可正常 DONE(全终态)。"""
    _, token = await _user_with_task_perms(client, session, make_user, "resA")
    task_id = await _create_task(client, token)
    steps: list[dict[str, object]] = [
        {"goal": "s1", "require_confirm": False},
        {"goal": "高危", "require_confirm": True},
    ]
    first = await _drive_orchestration(client, task_id, "default", steps)
    assert first == "AWAITING_APPROVAL"
    rr = await client.post(f"/tasks/{task_id}/resume", headers=_auth(token))
    assert rr.status_code == 409
    d = (await client.get(f"/tasks/{task_id}", headers=_auth(token))).json()
    assert d["task"]["status"] == "AWAITING_APPROVAL"
    pending = [s for s in d["steps"] if s["status"] == "AWAITING_APPROVAL"]
    rj = await client.post(
        f"/tasks/{task_id}/steps/{pending[0]['id']}/approve",
        json={"approve": False},
        headers=_auth(token),
    )
    assert rj.status_code == 200
    # spec:全部 DONE 方可 DONE——SKIPPED 也是未完成,不得全称完成
    rfd = await client.post(
        "/internal/task/finish",
        headers=INTERNAL,
        json={"task_id": task_id, "tenant_key": "default", "status": "DONE"},
    )
    assert rfd.status_code == 409
    second = await _drive_orchestration(client, task_id, "default", steps)
    assert second == "FAILED"  # 跳过步骤如实收口 FAILED(收口表述带未完成原因)
    detail = await _wait_status(client, token, task_id, ("FAILED",))
    assert [s["status"] for s in detail["steps"]] == ["DONE", "SKIPPED"]


@pytest.mark.asyncio
async def test_resume_requeues_running_steps(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Any,
    _no_dispatch: list[str],
) -> None:
    """恢复语义:暂停时在途(RUNNING)步骤回队 PENDING,恢复后重跑不丢失。"""
    _, token = await _user_with_task_perms(client, session, make_user, "requeueA")
    task_id = await _create_task(client, token)
    r = await client.post(
        "/internal/task/plan",
        headers=INTERNAL,
        json={
            "task_id": task_id,
            "tenant_key": "default",
            "steps": [{"goal": "s1", "require_confirm": False}],
        },
    )
    sid = r.json()["steps"][0]["id"]
    await _step_start(client, task_id, "default", sid)  # 步骤 RUNNING(在途)
    rp = await client.post(f"/tasks/{task_id}/pause", headers=_auth(token))
    assert rp.status_code == 200
    res = await client.post(f"/tasks/{task_id}/resume", headers=_auth(token))
    assert res.status_code == 200
    d = (await client.get(f"/tasks/{task_id}", headers=_auth(token))).json()
    assert d["task"]["status"] == "RUNNING"
    assert d["steps"][0]["status"] == "PENDING"  # 孤儿步骤已回队
    assert any(e["kind"] == "steps_requeued" for e in d["events"])


@pytest.mark.asyncio
async def test_expect_version_real_cas(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Any,
    _no_dispatch: list[str],
) -> None:
    """乐观锁:期望版本入 WHERE——陈旧版本 409 且状态不变,新版本命中。"""
    _, token = await _user_with_task_perms(client, session, make_user, "casA")
    task_id = await _create_task(client, token)
    ver = await session.execute(
        text("SELECT version FROM public.sys_agent_task WHERE id = CAST(:i AS uuid)"),
        {"i": task_id},
    )
    current = int(ver.scalar_one())
    stale = await client.post(
        f"/tasks/{task_id}/pause", params={"version": current + 5}, headers=_auth(token)
    )
    assert stale.status_code == 409
    d = (await client.get(f"/tasks/{task_id}", headers=_auth(token))).json()
    assert d["task"]["status"] == "PLANNING"
    hit = await client.post(
        f"/tasks/{task_id}/pause", params={"version": current}, headers=_auth(token)
    )
    assert hit.status_code == 200
    assert hit.json()["status"] == "PAUSED"


@pytest.mark.asyncio
async def test_approval_expiry_skips_and_resumes(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Any,
    _no_dispatch: list[str],
) -> None:
    """审批过期:超 TTL 的待审步骤不可再批准(404);启动清扫置 SKIPPED;
    resume 守卫因步骤已非 AWAITING 放行,续跑可 DONE。"""
    from kongming.task.repository import SqlTaskRepo
    from kongming.task.service import TaskService

    _, token = await _user_with_task_perms(client, session, make_user, "expA")
    task_id = await _create_task(client, token)
    steps: list[dict[str, object]] = [
        {"goal": "高危", "require_confirm": True},
        {"goal": "s2", "require_confirm": False},
    ]
    first = await _drive_orchestration(client, task_id, "default", steps)
    assert first == "AWAITING_APPROVAL"
    d = (await client.get(f"/tasks/{task_id}", headers=_auth(token))).json()
    pending = [s for s in d["steps"] if s["status"] == "AWAITING_APPROVAL"][0]
    await session.execute(
        text(
            "UPDATE public.sys_agent_task_step SET updated_at = now() - interval '25 hours'"
            " WHERE id = CAST(:i AS uuid)"
        ),
        {"i": pending["id"]},
    )
    await session.commit()
    ra = await client.post(
        f"/tasks/{task_id}/steps/{pending['id']}/approve",
        json={"approve": True},
        headers=_auth(token),
    )
    assert ra.status_code == 404  # 超时审批不可再决
    await TaskService(SqlTaskRepo(session)).mark_interrupted_stale()  # 启动清扫
    await session.commit()
    d2 = (await client.get(f"/tasks/{task_id}", headers=_auth(token))).json()
    assert d2["steps"][0]["status"] == "SKIPPED"
    assert d2["steps"][0]["fail_reason"] == "审批超时,自动跳过"
    res = await client.post(f"/tasks/{task_id}/resume", headers=_auth(token))
    assert res.status_code == 200
    second = await _drive_orchestration(client, task_id, "default", steps)
    assert second == "FAILED"  # 过期跳过的步骤计入未完成,如实 FAILED 非 DONE
    detail = await _wait_status(client, token, task_id, ("FAILED",))
    assert [s["status"] for s in detail["steps"]] == ["SKIPPED", "DONE"]


@pytest.mark.asyncio
async def test_wallclock_budget_pauses(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Any,
    _no_dispatch: list[str],
) -> None:
    """墙钟预算:步骤起跑→回写的时长被累加,超 max_seconds 下一步 pause。"""
    _, token = await _user_with_task_perms(client, session, make_user, "wallA")
    task_id = await _create_task(client, token, budget={"max_seconds": 1})
    r = await client.post(
        "/internal/task/plan",
        headers=INTERNAL,
        json={
            "task_id": task_id,
            "tenant_key": "default",
            "steps": [
                {"goal": "s1", "require_confirm": False},
                {"goal": "s2", "require_confirm": False},
            ],
        },
    )
    ids = [s["id"] for s in r.json()["steps"]]
    await _step_start(client, task_id, "default", ids[0])
    await session.execute(
        text(
            "UPDATE public.sys_agent_task_step SET started_at = now() - interval '5 minutes'"
            " WHERE id = CAST(:i AS uuid)"
        ),
        {"i": ids[0]},
    )
    await session.commit()
    await _step_result_done(client, task_id, "default", ids[0])
    d = await _step_start(client, task_id, "default", ids[1])
    assert d["decision"] == "pause", d
    assert d["reason"] == "时长预算耗尽"
    detail = await _wait_status(client, token, task_id, ("PAUSED",))
    assert detail["task"]["status"] == "PAUSED"


@pytest.mark.asyncio
async def test_replan_after_planning_interrupt(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Any,
    _no_dispatch: list[str],
) -> None:
    """规划期中断恢复:RUNNING 且零步骤(计划未落账)允许重规划,续跑 DONE。"""
    _, token = await _user_with_task_perms(client, session, make_user, "replanA")
    task_id = await _create_task(client, token)
    # 模拟「迁移已提交、插步前崩溃」:任务 RUNNING 但零步骤
    await session.execute(
        text(
            "UPDATE public.sys_agent_task SET status = 'RUNNING', version = version + 1"
            " WHERE id = CAST(:i AS uuid)"
        ),
        {"i": task_id},
    )
    await session.commit()
    final = await _drive_orchestration(
        client, task_id, "default", [{"goal": "重规划步骤", "require_confirm": False}]
    )
    assert final == "DONE"
    detail = await _wait_status(client, token, task_id, ("DONE",))
    assert len(detail["steps"]) == 1
    assert any(e["kind"] == "replanned" for e in detail["events"])


@pytest.mark.asyncio
async def test_internal_steps_tenant_scoped(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Any,
    _no_dispatch: list[str],
) -> None:
    """internal steps 端点租户归属:他租户键读任务 → 404(不横读)。"""
    _, token = await _user_with_task_perms(client, session, make_user, "tscA")
    task_id = await _create_task(client, token)
    await session.execute(
        text(
            "INSERT INTO public.sys_tenant (id, tenant_key, tenant_name, status)"
            " VALUES (gen_random_uuid(), 'other-task-tenant', '他租户', 'ACTIVE')"
        )
    )
    await session.commit()
    r = await client.get(
        "/internal/task/steps",
        headers=INTERNAL,
        params={"task_id": task_id, "tenant_key": "other-task-tenant"},
    )
    assert r.status_code == 404
    r2 = await client.get(
        "/internal/task/steps",
        headers=INTERNAL,
        params={"task_id": task_id, "tenant_key": "default"},
    )
    assert r2.status_code == 200


@pytest.mark.asyncio
async def test_read_perm_and_malformed_uuid(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Any,
    _no_dispatch: list[str],
) -> None:
    """读取面 enforcement:无 task:read → 403;畸形 UUID → 404 同形(非 500)。"""
    _, token = await _user_with_task_perms(
        client, session, make_user, "roA", codes=("task:create",)
    )
    r = await client.get("/tasks", headers=_auth(token))
    assert r.status_code == 403
    _, token2 = await _user_with_task_perms(client, session, make_user, "roB")
    r2 = await client.get("/tasks/not-a-uuid", headers=_auth(token2))
    assert r2.status_code == 404
    r3 = await client.post("/tasks/not-a-uuid/cancel", headers=_auth(token2))
    assert r3.status_code == 404


@pytest.mark.asyncio
async def test_approve_allowed_while_paused(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Any,
    _no_dispatch: list[str],
) -> None:
    """审批期暂停不是死锁:PAUSED 下审批决策可作出,任务随决策恢复运行。"""
    _, token = await _user_with_task_perms(client, session, make_user, "apPa")
    task_id = await _create_task(client, token)
    steps: list[dict[str, object]] = [
        {"goal": "s1", "require_confirm": False},
        {"goal": "高危", "require_confirm": True},
    ]
    first = await _drive_orchestration(client, task_id, "default", steps)
    assert first == "AWAITING_APPROVAL"
    rp = await client.post(f"/tasks/{task_id}/pause", headers=_auth(token))
    assert rp.status_code == 200
    d = (await client.get(f"/tasks/{task_id}", headers=_auth(token))).json()
    assert d["task"]["status"] == "PAUSED"
    pending = [s for s in d["steps"] if s["status"] == "AWAITING_APPROVAL"]
    assert len(pending) == 1
    ra = await client.post(
        f"/tasks/{task_id}/steps/{pending[0]['id']}/approve",
        json={"approve": True},
        headers=_auth(token),
    )
    assert ra.status_code == 200, ra.text
    d = (await client.get(f"/tasks/{task_id}", headers=_auth(token))).json()
    assert d["task"]["status"] == "RUNNING"  # 任务随审批决策恢复
    assert await _drive_orchestration(client, task_id, "default", steps) == "DONE"


@pytest.mark.asyncio
async def test_reject_reschedules_orchestration(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Any,
    _no_dispatch: list[str],
) -> None:
    """拒绝审批后续跑其余步骤:拒绝侧同样触发编排派发(挂起即停后
    审批决策是循环唯一的再启动口,拒绝不派发=任务假 RUNNING)。"""
    _, token = await _user_with_task_perms(client, session, make_user, "rejB")
    task_id = await _create_task(client, token)
    steps: list[dict[str, object]] = [
        {"goal": "s1", "require_confirm": False},
        {"goal": "高危", "require_confirm": True},
    ]
    assert await _drive_orchestration(client, task_id, "default", steps) == "AWAITING_APPROVAL"
    d = (await client.get(f"/tasks/{task_id}", headers=_auth(token))).json()
    pending = [s for s in d["steps"] if s["status"] == "AWAITING_APPROVAL"]
    _no_dispatch.clear()
    rj = await client.post(
        f"/tasks/{task_id}/steps/{pending[0]['id']}/approve",
        json={"approve": False},
        headers=_auth(token),
    )
    assert rj.status_code == 200
    assert _no_dispatch == [task_id]  # 拒绝侧同样派发续跑
    d = (await client.get(f"/tasks/{task_id}", headers=_auth(token))).json()
    assert d["task"]["status"] == "RUNNING"


@pytest.mark.asyncio
async def test_resume_running_idempotent_no_redispatch(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Any,
    _no_dispatch: list[str],
) -> None:
    """双派发防护:已 RUNNING 的任务 resume 幂等收敛(200),不二次派发编排。"""
    _, token = await _user_with_task_perms(client, session, make_user, "resC")
    task_id = await _create_task(client, token)
    r = await client.post(
        "/internal/task/plan",
        headers=INTERNAL,
        json={
            "task_id": task_id,
            "tenant_key": "default",
            "steps": [{"goal": "s1", "require_confirm": False}],
        },
    )
    assert r.status_code == 200
    await _step_start(client, task_id, "default", str(r.json()["steps"][0]["id"]))
    _no_dispatch.clear()
    res = await client.post(f"/tasks/{task_id}/resume", headers=_auth(token))
    assert res.status_code == 200
    assert res.json()["status"] == "RUNNING"
    assert _no_dispatch == []  # 幂等成功不派发(v1 单执行循环)


@pytest.mark.asyncio
async def test_resume_blocked_while_planning(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Any,
    _no_dispatch: list[str],
) -> None:
    """PLANNING 态 resume 拒绝:规划循环在驱动,二次派发即双执行者。"""
    _, token = await _user_with_task_perms(client, session, make_user, "plaF")
    task_id = await _create_task(client, token)
    rr = await client.post(f"/tasks/{task_id}/resume", headers=_auth(token))
    assert rr.status_code == 409
    d = (await client.get(f"/tasks/{task_id}", headers=_auth(token))).json()
    assert d["task"]["status"] == "PLANNING"


@pytest.mark.asyncio
async def test_cancel_closes_open_steps(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Any,
    _no_dispatch: list[str],
) -> None:
    """取消账本收敛:非终态步骤一并 CANCELLED(steps_cancelled 事件);
    此后的迟到步骤回写被步骤 CAS 诚实拒绝(409),不改写已关闭账本。"""
    _, token = await _user_with_task_perms(client, session, make_user, "canD")
    task_id = await _create_task(client, token)
    r = await client.post(
        "/internal/task/plan",
        headers=INTERNAL,
        json={
            "task_id": task_id,
            "tenant_key": "default",
            "steps": [{"goal": "s1", "require_confirm": False}],
        },
    )
    sid = str(r.json()["steps"][0]["id"])
    await _step_start(client, task_id, "default", sid)  # RUNNING 在途
    rc = await client.post(f"/tasks/{task_id}/cancel", headers=_auth(token))
    assert rc.status_code == 200
    d = (await client.get(f"/tasks/{task_id}", headers=_auth(token))).json()
    assert d["task"]["status"] == "CANCELLED"
    assert all(s["status"] == "CANCELLED" for s in d["steps"])
    assert any(e["kind"] == "steps_cancelled" for e in d["events"])
    rl = await client.post(
        "/internal/task/step-result",
        headers=INTERNAL,
        json={
            "task_id": task_id,
            "tenant_key": "default",
            "step_id": sid,
            "status": "DONE",
            "evidence": {"trace_id": "tr"},
            "summary": "迟到回写",
        },
    )
    assert rl.status_code == 409  # 步骤已 CANCELLED:CAS 拒绝改写


@pytest.mark.asyncio
async def test_late_result_after_failed_task_stops_ledger(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Any,
    _no_dispatch: list[str],
) -> None:
    """终态任务迟到回写:保留步骤证据,但不再追加步骤/累加预算
    (late_step_result 事件标注;终态任务无执行者,账本封账)。"""
    _, token = await _user_with_task_perms(client, session, make_user, "latE")
    task_id = await _create_task(client, token)
    r = await client.post(
        "/internal/task/plan",
        headers=INTERNAL,
        json={
            "task_id": task_id,
            "tenant_key": "default",
            "steps": [
                {"goal": "s1", "require_confirm": False},
                {"goal": "s2", "require_confirm": False},
            ],
        },
    )
    ids = [str(s["id"]) for s in r.json()["steps"]]
    await _step_start(client, task_id, "default", ids[0])
    await _step_result_done(client, task_id, "default", ids[0])
    await _step_start(client, task_id, "default", ids[1])  # s2 RUNNING 在途
    rf = await client.post(
        "/internal/task/finish",
        headers=INTERNAL,
        json={
            "task_id": task_id,
            "tenant_key": "default",
            "status": "FAILED",
            "fail_reason": "编排失败",
        },
    )
    assert rf.status_code == 204
    rl = await client.post(
        "/internal/task/step-result",
        headers=INTERNAL,
        json={
            "task_id": task_id,
            "tenant_key": "default",
            "step_id": ids[1],
            "status": "DONE",
            "evidence": {"trace_id": "tr"},
            "summary": "迟到",
            "tokens_used": 500,
            "appended_steps": [{"goal": "不该新增"}],
        },
    )
    assert rl.status_code == 200
    assert rl.json()["decision"] == "continue"
    d = (await client.get(f"/tasks/{task_id}", headers=_auth(token))).json()
    assert [s["status"] for s in d["steps"]] == ["DONE", "DONE"]  # 证据保留
    assert len(d["steps"]) == 2  # appended_steps 未落账
    assert d["budget_used"].get("steps") == 1  # 终态封账:迟到回写不计数
    assert any(e["kind"] == "late_step_result" for e in d["events"])
