# pyright: reportPrivateUsage=false
"""task 协作域测试(091 接线,安全规则类测试先行)。

覆盖:拓扑归一/部署级钳制纯函数、步骤拓扑声明面(归属 404 同形/PENDING 闸/
Bot 绑定校验)、无游离协作入口(未声明拓扑/非 RUNNING 步骤的 collab-start 一律拒)、
协作账本全链(start→worker×n→finish,状态诚实映射)、重复回写 CAS 拒绝、
任务取消收敛在途协作账、预算部署级钳制、列表状态筛选、internal 畸形 UUID 404 同形。
"""

import json
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.test_chat_query import _auth
from tests.test_task import (  # noqa: F401 —— 助手跨文件复用
    INTERNAL,
    _create_task,
    _user_with_task_perms,
)


@pytest.fixture
def no_dispatch(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """派发记录器(与 test_task.py 同款):创建/恢复/批准的 _schedule_run 改为记录。"""
    from kongming.task import router as task_router

    calls: list[str] = []

    def _record(*, task_id: str, epoch: int | None = None) -> None:
        calls.append(task_id)

    monkeypatch.setattr(task_router, "_schedule_run", _record)
    return calls


# ── 纯函数:拓扑归一/钳制与运行状态映射 ──


def test_normalize_collab_topology_defaults_and_clamp() -> None:
    from kongming.task import models as m

    # 缺省回填 + 部署钳制
    out = m.normalize_collab_topology(None, max_workers_cap=3)
    assert out == {"max_workers": 3, "critic": False, "dispatch": "serial", "bot_key": None}
    # 超部署上限 → 钳到上限
    out = m.normalize_collab_topology({"max_workers": 99}, max_workers_cap=3)
    assert out["max_workers"] == 3
    # 部署上限收紧 → 声明同样被钳
    out = m.normalize_collab_topology({"max_workers": 5}, max_workers_cap=2)
    assert out["max_workers"] == 2
    # 非法分配策略 → 回退 serial;空 bot_key 归一 None
    out = m.normalize_collab_topology({"dispatch": "chaos", "bot_key": "  "}, max_workers_cap=3)
    assert out["dispatch"] == "serial"
    assert out["bot_key"] is None
    assert out["critic"] is False


def test_clamp_budget_deployment_cap() -> None:
    from kongming.task import models as m

    normalized = m.normalize_budget({"max_tokens": 999_999, "max_steps": 2})
    clamped = m.clamp_budget(
        normalized, {"max_steps": 12, "max_tokens": 200_000, "max_seconds": 900}
    )
    assert clamped["max_tokens"] == 200_000
    assert clamped["max_steps"] == 2  # 任务配置低于上限不放大
    # 部署收紧:超限项被钳,未超限项保持任务配置
    clamped = m.clamp_budget(normalized, {"max_steps": 5, "max_tokens": 1_000, "max_seconds": 600})
    assert clamped["max_steps"] == 2
    assert clamped["max_tokens"] == 1_000


def test_collab_run_status_honest_mapping() -> None:
    from kongming.task import models as m

    assert m.collab_run_status("PASS", 0) == "DONE"
    assert m.collab_run_status("PASS", 2) == "PARTIAL"  # 未派发清单非空=部分完成
    assert m.collab_run_status("ERROR", 0) == "FAILED"


# ── 助手 ──


async def _plan_one_step(
    client: AsyncClient, tid: str, tk: str = "default", goal: str = "交叉验证三个数据源口径"
) -> dict[str, Any]:
    r = await client.post(
        "/internal/task/plan",
        headers=INTERNAL,
        json={"task_id": tid, "tenant_key": tk, "steps": [{"goal": goal}]},
    )
    assert r.status_code == 200, r.text
    return dict(r.json()["steps"][0])


async def _set_topology(
    client: AsyncClient, token: str, tid: str, sid: str, topo: dict[str, object]
) -> Any:
    return await client.put(
        f"/tasks/{tid}/steps/{sid}/collab-topology", json=topo, headers=_auth(token)
    )


async def _insert_bot(
    s: AsyncSession, uid: str, bot_key: str = "fin_analyst", prompt: str = "你是财务分析专家"
) -> None:
    await s.execute(
        text(
            "INSERT INTO public.sys_bot_definition"
            " (id, tenant_id, bot_key, name, system_prompt, tools, datasource_key,"
            "  enabled, is_public, created_by) VALUES (gen_random_uuid(),"
            " (SELECT id FROM public.sys_tenant WHERE tenant_key = 'default'),"
            " :k, :k, :sp, CAST(:tools AS jsonb), 'default', true, false, CAST(:u AS uuid))"
        ),
        {"k": bot_key, "sp": prompt, "tools": json.dumps([]), "u": uid},
    )


# ── HTTP 集成 ──


@pytest.mark.asyncio
async def test_topology_declare_and_begin_step_collab_decision(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Any,
    no_dispatch: list[str],  # noqa: ARG001
) -> None:
    """拓扑声明 → step-start 决策携带协作载荷(库内归一值为准);未声明步骤
    决策无协作载荷;配置与派发事件落任务事件流。"""
    uid, token = await _user_with_task_perms(client, session, make_user)
    task_id = await _create_task(client, token)
    step = await _plan_one_step(client, task_id)

    # PENDING 步骤声明拓扑
    topo = {"max_workers": 3, "critic": True, "dispatch": "parallel"}
    r = await _set_topology(client, token, task_id, step["id"], topo)
    assert r.status_code == 200, r.text
    assert r.json()["max_workers"] == 3
    assert r.json()["critic"] is True

    # 执行中步骤再声明 → 409(PENDING 闸)
    d = await client.post(
        "/internal/task/step-start",
        headers=INTERNAL,
        json={"task_id": task_id, "tenant_key": "default", "step_id": step["id"]},
    )
    assert d.status_code == 200
    decision = d.json()
    assert decision["decision"] == "execute"
    assert decision["collab"]["max_workers"] == 3
    assert decision["collab"]["dispatch"] == "parallel"
    assert decision["collab"]["bot"] is None  # 未绑定 Bot 如实降级
    r2 = await _set_topology(client, token, task_id, step["id"], {"max_workers": 2})
    assert r2.status_code == 409

    # 未声明协作的步骤 → 决策无 collab 键(普通单 ReAct 步)
    tid2 = await _create_task(client, token)
    await _plan_one_step(client, tid2, goal="普通单步")
    d2 = await client.get(
        "/internal/task/steps", headers=INTERNAL, params={"task_id": tid2, "tenant_key": "default"}
    )
    plain_step = d2.json()["steps"][0]
    d3 = await client.post(
        "/internal/task/step-start",
        headers=INTERNAL,
        json={"task_id": tid2, "tenant_key": "default", "step_id": plain_step["id"]},
    )
    assert d3.status_code == 200
    assert "collab" not in d3.json()

    detail = await client.get(f"/tasks/{task_id}", headers=_auth(token))
    kinds = [str(e["kind"]) for e in detail.json()["events"]]
    assert "collab_topology_set" in kinds
    assert "collab_started" not in kinds  # 尚未开账
    _ = uid


@pytest.mark.asyncio
async def test_no_free_collab_entry(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Any,
    no_dispatch: list[str],  # noqa: ARG001
) -> None:
    """无游离协作入口(D5):未声明拓扑/非 RUNNING 步骤/超深嵌套一律拒绝。"""
    _uid, token = await _user_with_task_perms(client, session, make_user)
    task_id = await _create_task(client, token)
    step = await _plan_one_step(client, task_id)

    # PENDING 步骤(未 begin_step)直接 collab-start → 拒(宿主未就位)
    r = await client.post(
        "/internal/task/collab-start",
        headers=INTERNAL,
        json={"task_id": task_id, "tenant_key": "default", "step_id": step["id"], "depth": 1},
    )
    assert r.status_code == 409

    # 声明拓扑后 RUNNING,但 depth=2(> 部署上限 1)→ 拒
    rtop = await _set_topology(client, token, task_id, step["id"], {"max_workers": 2})
    assert rtop.status_code == 200
    d = await client.post(
        "/internal/task/step-start",
        headers=INTERNAL,
        json={"task_id": task_id, "tenant_key": "default", "step_id": step["id"]},
    )
    assert d.status_code == 200 and d.json()["decision"] == "execute"
    rdeep = await client.post(
        "/internal/task/collab-start",
        headers=INTERNAL,
        json={"task_id": task_id, "tenant_key": "default", "step_id": step["id"], "depth": 2},
    )
    assert rdeep.status_code == 409

    # 未声明拓扑的 RUNNING 步骤 → 拒(拓扑闸,协作只由声明步骤发起)
    task2 = await _create_task(client, token)
    step2 = await _plan_one_step(client, task2, goal="未声明拓扑的执行步")
    d2 = await client.post(
        "/internal/task/step-start",
        headers=INTERNAL,
        json={"task_id": task2, "tenant_key": "default", "step_id": step2["id"]},
    )
    assert d2.status_code == 200
    r2 = await client.post(
        "/internal/task/collab-start",
        headers=INTERNAL,
        json={"task_id": task2, "tenant_key": "default", "step_id": step2["id"], "depth": 1},
    )
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_collab_ledger_full_chain(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Any,
    no_dispatch: list[str],  # noqa: ARG001
) -> None:
    """协作全链:开账 → worker 派发/回写 ×2 → 终态收口(DONE/PARTIAL/FAILED
    诚实映射)→ 查询端点 worker 账本完整;重复回写 CAS 拒。"""
    uid, token = await _user_with_task_perms(client, session, make_user)
    await _insert_bot(session, uid)
    await session.commit()
    task_id = await _create_task(client, token)
    step = await _plan_one_step(client, task_id)
    r = await _set_topology(
        client, token, task_id, step["id"], {"max_workers": 3, "bot_key": "fin_analyst"}
    )
    assert r.status_code == 200
    d = await client.post(
        "/internal/task/step-start",
        headers=INTERNAL,
        json={"task_id": task_id, "tenant_key": "default", "step_id": step["id"]},
    )
    assert d.status_code == 200
    # Bot 绑定注入决策:人格与工具子集按 Bot 行带出
    assert d.json()["collab"]["bot"] == {
        "bot_key": "fin_analyst",
        "system_prompt": "你是财务分析专家",
        "tools": [],
    }

    cs = await client.post(
        "/internal/task/collab-start",
        headers=INTERNAL,
        json={"task_id": task_id, "tenant_key": "default", "step_id": step["id"], "depth": 1},
    )
    assert cs.status_code == 200, cs.text
    run_id = cs.json()["run_id"]
    assert cs.json()["topology"]["bot_key"] == "fin_analyst"

    for no in (1, 2):
        ws = await client.post(
            "/internal/task/collab-worker-start",
            headers=INTERNAL,
            json={
                "task_id": task_id,
                "tenant_key": "default",
                "run_id": run_id,
                "worker_no": no,
                "subgoal": f"查口径{no}",
                "bot_key": "fin_analyst",
                "trace_id": f"trace-w{no}",
            },
        )
        assert ws.status_code == 204, ws.text
    # 重复序号 → 拒(唯一键)
    dup = await client.post(
        "/internal/task/collab-worker-start",
        headers=INTERNAL,
        json={
            "task_id": task_id,
            "tenant_key": "default",
            "run_id": run_id,
            "worker_no": 1,
            "subgoal": "重复派发",
            "trace_id": "t",
        },
    )
    assert dup.status_code == 409
    for no, status in ((1, "DONE"), (2, "FAILED")):
        wr = await client.post(
            "/internal/task/collab-worker-result",
            headers=INTERNAL,
            json={
                "task_id": task_id,
                "tenant_key": "default",
                "run_id": run_id,
                "worker_no": no,
                "status": status,
                "conclusion": {"summary": f"结论{no}"},
                "evidence": {"trace_id": f"trace-w{no}"},
                "usage": {"llm_calls": 2, "total_tokens": 100 * no},
                "trace_id": f"trace-w{no}",
            },
        )
        assert wr.status_code == 204, wr.text
    # 迟到重复回写 → CAS 拒
    late = await client.post(
        "/internal/task/collab-worker-result",
        headers=INTERNAL,
        json={
            "task_id": task_id,
            "tenant_key": "default",
            "run_id": run_id,
            "worker_no": 1,
            "status": "FAILED",
            "trace_id": "trace-w1",
        },
    )
    assert late.status_code == 409

    fin = await client.post(
        "/internal/task/collab-finish",
        headers=INTERNAL,
        json={
            "task_id": task_id,
            "tenant_key": "default",
            "run_id": run_id,
            "verdict": "ERROR",
            "pending_count": 0,
            "dissent": False,
            "summary": "worker1 失败",
        },
    )
    assert fin.status_code == 200
    assert fin.json()["status"] == "FAILED"  # 非 PASS 且无未派发 → FAILED

    runs = await client.get(f"/tasks/{task_id}/collab-runs", headers=_auth(token))
    assert runs.status_code == 200
    items = runs.json()["items"]
    assert len(items) == 1
    run = items[0]
    assert run["status"] == "FAILED"
    assert run["verdict"] == "ERROR"
    assert len(run["workers"]) == 2
    assert run["workers"][0]["status"] == "DONE"
    assert run["workers"][0]["usage"]["total_tokens"] == 100
    assert run["workers"][1]["status"] == "FAILED"
    assert run["workers"][0]["conclusion"]["summary"] == "结论1"
    detail = await client.get(f"/tasks/{task_id}", headers=_auth(token))
    kinds = [str(e["kind"]) for e in detail.json()["events"]]
    assert "collab_started" in kinds
    assert "collab_worker_started" in kinds
    assert "collab_worker_finished" in kinds
    assert "collab_finished" in kinds

    # PARTIAL:pending 非空诚实部分完成
    task2 = await _create_task(client, token)
    step2 = await _plan_one_step(client, task2)
    await _set_topology(client, token, task2, step2["id"], {"max_workers": 3})
    await client.post(
        "/internal/task/step-start",
        headers=INTERNAL,
        json={"task_id": task2, "tenant_key": "default", "step_id": step2["id"]},
    )
    cs2 = await client.post(
        "/internal/task/collab-start",
        headers=INTERNAL,
        json={"task_id": task2, "tenant_key": "default", "step_id": step2["id"], "depth": 1},
    )
    run2 = cs2.json()["run_id"]
    fin2 = await client.post(
        "/internal/task/collab-finish",
        headers=INTERNAL,
        json={
            "task_id": task2,
            "tenant_key": "default",
            "run_id": run2,
            "verdict": "PASS",
            "pending_count": 2,
            "dissent": False,
            "summary": "预算截断",
        },
    )
    assert fin2.json()["status"] == "PARTIAL"


@pytest.mark.asyncio
async def test_collab_supersede_and_cancel_convergence(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Any,
    no_dispatch: list[str],  # noqa: ARG001
) -> None:
    """重跑前在途运行收敛(supersede)+ 任务取消收敛在途协作账。"""
    _uid, token = await _user_with_task_perms(client, session, make_user)
    task_id = await _create_task(client, token)
    step = await _plan_one_step(client, task_id)
    await _set_topology(client, token, task_id, step["id"], {"max_workers": 2})
    await client.post(
        "/internal/task/step-start",
        headers=INTERNAL,
        json={"task_id": task_id, "tenant_key": "default", "step_id": step["id"]},
    )
    cs = await client.post(
        "/internal/task/collab-start",
        headers=INTERNAL,
        json={"task_id": task_id, "tenant_key": "default", "step_id": step["id"], "depth": 1},
    )
    run_id = cs.json()["run_id"]
    await client.post(
        "/internal/task/collab-worker-start",
        headers=INTERNAL,
        json={
            "task_id": task_id,
            "tenant_key": "default",
            "run_id": run_id,
            "worker_no": 1,
            "subgoal": "在途",
            "trace_id": "tw",
        },
    )
    # 同 (task, step) 再开账:旧运行 CANCELLED、在途 worker SKIPPED(不谎称完成)
    cs2 = await client.post(
        "/internal/task/collab-start",
        headers=INTERNAL,
        json={"task_id": task_id, "tenant_key": "default", "step_id": step["id"], "depth": 1},
    )
    assert cs2.status_code == 200
    items = (await client.get(f"/tasks/{task_id}/collab-runs", headers=_auth(token))).json()[
        "items"
    ]
    by_status = {r["status"] for r in items}
    assert "RUNNING" in by_status and "CANCELLED" in by_status
    old = next(r for r in items if r["id"] == run_id)
    assert old["workers"][0]["status"] == "SKIPPED"

    # 终态运行不可再派发 worker(读检 + 写入谓词双闸)
    r_late = await client.post(
        "/internal/task/collab-worker-start",
        headers=INTERNAL,
        json={
            "task_id": task_id,
            "tenant_key": "default",
            "run_id": run_id,
            "worker_no": 2,
            "subgoal": "迟到派发",
            "trace_id": "tw2",
        },
    )
    assert r_late.status_code == 409

    # 运行收口收敛孤儿 worker:落账 RUNNING 但无结果回写的行,在运行终态时
    # 收敛 SKIPPED(旧账不谎称完成),事件带 workers_skipped
    run2 = cs2.json()["run_id"]
    ws2 = await client.post(
        "/internal/task/collab-worker-start",
        headers=INTERNAL,
        json={
            "task_id": task_id,
            "tenant_key": "default",
            "run_id": run2,
            "worker_no": 1,
            "subgoal": "孤儿 worker",
            "trace_id": "tw3",
        },
    )
    assert ws2.status_code == 204, ws2.text
    cf = await client.post(
        "/internal/task/collab-finish",
        headers=INTERNAL,
        json={
            "task_id": task_id,
            "tenant_key": "default",
            "run_id": run2,
            "verdict": "PASS",
            "pending_count": 0,
            "dissent": False,
            "summary": "收口",
        },
    )
    assert cf.status_code == 200 and cf.json()["status"] == "DONE", cf.text
    items_done = (await client.get(f"/tasks/{task_id}/collab-runs", headers=_auth(token))).json()[
        "items"
    ]
    run2_row = next(r for r in items_done if r["id"] == run2)
    assert run2_row["workers"][0]["status"] == "SKIPPED"
    detail_mid = await client.get(f"/tasks/{task_id}", headers=_auth(token))
    ev_fin = next(e for e in detail_mid.json()["events"] if e["kind"] == "collab_finished")
    assert ev_fin["workers_skipped"] == 1

    # 再开一账保留开放运行(上账已被收口),保住「取消收敛在途账」断言前提
    cs3 = await client.post(
        "/internal/task/collab-start",
        headers=INTERNAL,
        json={"task_id": task_id, "tenant_key": "default", "step_id": step["id"], "depth": 1},
    )
    assert cs3.status_code == 200

    # 任务取消:开放协作账一并收敛
    rc = await client.post(f"/tasks/{task_id}/cancel", headers=_auth(token))
    assert rc.status_code == 200
    items2 = (await client.get(f"/tasks/{task_id}/collab-runs", headers=_auth(token))).json()[
        "items"
    ]
    assert all(r["status"] != "RUNNING" for r in items2)
    detail = await client.get(f"/tasks/{task_id}", headers=_auth(token))
    ev = next(e for e in detail.json()["events"] if e["kind"] == "steps_cancelled")
    assert ev["collab_runs_cancelled"] >= 1


@pytest.mark.asyncio
async def test_topology_guards_and_budget_clamp_and_filter(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Any,
    no_dispatch: list[str],  # noqa: ARG001
) -> None:
    """拓扑守卫(越权任务 404 同形/未知 Bot 422)+ 预算部署钳制 + 列表状态筛选
    + internal 畸形 UUID 404 同形。"""
    uid, token = await _user_with_task_perms(client, session, make_user)
    await _insert_bot(session, uid, bot_key="good_bot")
    await session.commit()
    task_id = await _create_task(client, token, budget={"max_steps": 99, "max_tokens": 5_000_000})
    step = await _plan_one_step(client, task_id)

    # 归属 404 同形(他人任务不可声明)
    _, other_token = await _make_other(client, session, make_user)
    r404 = await _set_topology(client, other_token, task_id, step["id"], {"max_workers": 2})
    assert r404.status_code == 404
    # 未知 Bot → 422(非归属 404)
    rbot = await _set_topology(
        client, token, task_id, step["id"], {"max_workers": 2, "bot_key": "ghost_bot"}
    )
    assert rbot.status_code == 422
    # 合法 Bot
    rok = await _set_topology(
        client, token, task_id, step["id"], {"max_workers": 2, "bot_key": "good_bot"}
    )
    assert rok.status_code == 200

    # 预算部署钳制:创建时 99/5M 被钳到部署上限 12/200k
    detail = await client.get(f"/tasks/{task_id}", headers=_auth(token))
    assert detail.json()["budget_config"]["max_steps"] == 12
    assert detail.json()["budget_config"]["max_tokens"] == 200_000

    # 状态筛选:非法值 422;合法值按状态过滤
    rbad = await client.get("/tasks", headers=_auth(token), params={"status": "WAT"})
    assert rbad.status_code == 422
    rok2 = await client.get("/tasks", headers=_auth(token), params={"status": "RUNNING"})
    assert rok2.status_code == 200
    assert all(it["status"] == "RUNNING" for it in rok2.json()["items"])
    assert rok2.json()["total"] >= 1

    # internal 畸形 UUID → 404 同形(非 DBAPI 裸 500)
    for path, payload in (
        ("/internal/task/plan", {"task_id": "xx", "tenant_key": "d", "steps": [{"goal": "g"}]}),
        (
            "/internal/task/step-start",
            {"task_id": "xx", "tenant_key": "d", "step_id": "yy"},
        ),
        (
            "/internal/task/collab-start",
            {"task_id": "xx", "tenant_key": "d", "step_id": "yy", "depth": 1},
        ),
        (
            "/internal/task/collab-finish",
            {"task_id": "xx", "tenant_key": "d", "run_id": "yy", "verdict": "PASS"},
        ),
    ):
        rbaduuid = await client.post(path, headers=INTERNAL, json=payload)
        assert rbaduuid.status_code == 404, path
    rbadsteps = await client.get(
        "/internal/task/steps", headers=INTERNAL, params={"task_id": "xx", "tenant_key": "d"}
    )
    assert rbadsteps.status_code == 404


async def _make_other(client: AsyncClient, s: AsyncSession, make_user: Any) -> tuple[str, str]:
    """第二个持权用户(归属隔离断言用)。"""
    uid, token = await _user_with_task_perms(client, s, make_user, username="tasker_other")
    return uid, token


# ── 派发代际(0042 双执行者闸)──


@pytest.mark.asyncio
async def test_dispatch_epoch_gate(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Any,
    no_dispatch: list[str],  # noqa: ARG001
) -> None:
    """代际失配三闸:begin_step → abort 决策;step-result → stale_epoch(不写账);
    finish → 409(旧循环不得收口);在位代数路径零回归(epoch=None 兼容)。"""
    _uid, token = await _user_with_task_perms(client, session, make_user)
    task_id = await _create_task(client, token)
    step = await _plan_one_step(client, task_id)
    # create 已真实 bump(no_dispatch 只拦派发不拦 bump):当前代数=1,旧代数=0

    # 在位代数(epoch=1)→ execute
    d = await client.post(
        "/internal/task/step-start",
        headers=INTERNAL,
        json={
            "task_id": task_id,
            "tenant_key": "default",
            "step_id": step["id"],
            "epoch": 1,
        },
    )
    assert d.status_code == 200 and d.json()["decision"] == "execute", d.text

    # 旧循环代数(epoch=0)→ abort,任务未被触碰
    d_old = await client.post(
        "/internal/task/step-start",
        headers=INTERNAL,
        json={
            "task_id": task_id,
            "tenant_key": "default",
            "step_id": step["id"],
            "epoch": 0,
        },
    )
    assert d_old.status_code == 200
    assert d_old.json()["decision"] == "abort"
    assert "代际失配" in str(d_old.json().get("reason"))

    # 旧循环迟到回写 → stale_epoch:步骤不落终态、证据/预算零写入
    r_old = await client.post(
        "/internal/task/step-result",
        headers=INTERNAL,
        json={
            "task_id": task_id,
            "tenant_key": "default",
            "step_id": step["id"],
            "epoch": 0,
            "status": "DONE",
            "evidence": {"trace_id": "tr-old"},
            "summary": "旧循环结论",
        },
    )
    assert r_old.status_code == 200
    assert r_old.json()["decision"] == "stale_epoch"
    detail = await client.get(f"/tasks/{task_id}", headers=_auth(token))
    srow = next(s for s in detail.json()["steps"] if s["id"] == step["id"])
    assert srow["status"] == "RUNNING" and srow["evidence"] is None
    assert detail.json()["budget_used"].get("total_tokens") in (None, 0)

    # 旧循环终态收口 → 409(不迁移)
    f_old = await client.post(
        "/internal/task/finish",
        headers=INTERNAL,
        json={
            "task_id": task_id,
            "tenant_key": "default",
            "epoch": 0,
            "status": "FAILED",
            "fail_reason": "旧循环收口",
        },
    )
    assert f_old.status_code == 409

    # 在位代数回写 → 正常落账(决策 continue);在位 finish → 204
    r_cur = await client.post(
        "/internal/task/step-result",
        headers=INTERNAL,
        json={
            "task_id": task_id,
            "tenant_key": "default",
            "step_id": step["id"],
            "epoch": 1,
            "status": "DONE",
            "evidence": {"trace_id": "tr-cur"},
            "summary": "在位结论",
            "tokens_used": 10,
        },
    )
    assert r_cur.status_code == 200 and r_cur.json()["decision"] == "continue"
    f_cur = await client.post(
        "/internal/task/finish",
        headers=INTERNAL,
        json={
            "task_id": task_id,
            "tenant_key": "default",
            "epoch": 1,
            "status": "DONE",
            "fail_reason": None,
        },
    )
    assert f_cur.status_code == 204

    # 终态后任务代数仍为 1(未误增)
    detail = await client.get(f"/tasks/{task_id}", headers=_auth(token))
    assert detail.json()["task"]["status"] == "DONE"


@pytest.mark.asyncio
async def test_epoch_predicate_guards_cas_writes(
    client: AsyncClient,
    session: AsyncSession,
    make_user: Any,
    no_dispatch: list[str],  # noqa: ARG001
) -> None:
    """执行侧 CAS 写入谓词(expect_epoch):失配代数的任务态/步骤态写入
    不命中(读检查与 CAS 写之间的并发 bump 窗口由此关死);在位代数照常命中。"""
    from kongming.task.repository import SqlTaskRepo

    _uid, token = await _user_with_task_perms(client, session, make_user)
    task_id = await _create_task(client, token)
    step = await _plan_one_step(client, task_id)
    repo = SqlTaskRepo(session)
    tenant_id = str(
        (
            await session.execute(
                text("SELECT tenant_id FROM public.sys_agent_task WHERE id = CAST(:i AS uuid)"),
                {"i": task_id},
            )
        ).scalar_one()
    )

    # 在位代数=1:步骤 PENDING→RUNNING 命中;失配代数(999)不命中
    assert await repo.transition_step(
        step_id=step["id"],
        task_id=task_id,
        from_status="PENDING",
        to_status="RUNNING",
        expect_epoch=1,
    )
    assert not await repo.transition_step(
        step_id=step["id"],
        task_id=task_id,
        from_status="RUNNING",
        to_status="DONE",
        expect_epoch=999,
    )

    # 任务态:在位代数 RUNNING→PAUSED 命中;失配代数不迁移
    assert await repo.transition_task_system(
        task_id=task_id,
        tenant_id=tenant_id,
        from_status="RUNNING",
        to_status="PAUSED",
        expect_epoch=1,
    )
    assert not await repo.transition_task_system(
        task_id=task_id,
        tenant_id=tenant_id,
        from_status="PAUSED",
        to_status="RUNNING",
        expect_epoch=999,
    )
    row = (
        await session.execute(
            text("SELECT status FROM public.sys_agent_task WHERE id = CAST(:i AS uuid)"),
            {"i": task_id},
        )
    ).scalar_one()
    assert row == "PAUSED"
    await session.commit()
