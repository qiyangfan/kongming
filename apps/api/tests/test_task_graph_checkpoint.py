# pyright: reportPrivateUsage=false
"""覆盖图级检查点、审批挂起和恢复及过期清理。"""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.test_task import INTERNAL, _create_task, _user_with_task_perms


@pytest.fixture
def _no_dispatch(monkeypatch: pytest.MonkeyPatch) -> list[str]:  # pyright: ignore[reportUnusedFunction]
    """派发记录器(与 test_task.py 同款):创建/恢复/批准的 _schedule_run 改为
    记录,编排回调由本文件内联驱动(消除后台任务嵌套 ASGI 传输层死锁)。"""
    from kongming.task import router as task_router

    calls: list[str] = []

    def _record(*, task_id: str, epoch: int | None = None) -> None:
        calls.append(task_id)

    monkeypatch.setattr(task_router, "_schedule_run", _record)
    return calls


pytestmark = [pytest.mark.usefixtures("_no_dispatch")]


def _thread(tid: str, sid: str) -> str:
    return f"task:{tid}:step:{sid}"


async def _plan_one_step(
    client: AsyncClient, tid: str, tk: str, *, require_confirm: bool = False
) -> str:
    r = await client.post(
        "/internal/task/plan",
        headers=INTERNAL,
        json={
            "task_id": tid,
            "tenant_key": tk,
            "steps": [{"goal": "发布数据修正公告", "require_confirm": require_confirm}],
        },
    )
    assert r.status_code == 200, r.text
    return str(r.json()["steps"][0]["id"])


async def _suspend(
    client: AsyncClient,
    tid: str,
    tk: str,
    sid: str,
    confirm_id: str = "conf-1",
    *,
    epoch: int | None = None,
) -> dict[str, Any]:
    r = await client.post(
        "/internal/task/step-suspended",
        headers=INTERNAL,
        json={
            "task_id": tid,
            "tenant_key": tk,
            "step_id": sid,
            "epoch": epoch,
            "confirm_id": confirm_id,
            "tool_name": "create_system_announcement",
            "summary": "发布公告:修正华东口径",
            "thread_id": _thread(tid, sid),
        },
    )
    return cast_any(r)


def cast_any(r: Any) -> dict[str, Any]:
    import json as _json

    return _json.loads(_json.dumps(r.json()))


# ── 检查点存储(租户隔离 + 往返) ──


@pytest.mark.asyncio
async def test_checkpoint_put_latest_roundtrip(
    client: AsyncClient, session: AsyncSession, make_user: Any
) -> None:
    _, token = await _user_with_task_perms(client, session, make_user)
    tid = await _create_task(client, token)
    sid = await _plan_one_step(client, tid, "default")
    th = _thread(tid, sid)
    body = {
        "tenant_key": "default",
        "thread_id": th,
        "checkpoint_ns": "",
        "kind": "checkpoint",
        "checkpoint_id": "ck-1",
        "task_id": tid,
        "step_id": sid,
        "payload_type": "msgpack",
        "payload_b64": "AQIDBA==",
    }
    r = await client.put("/internal/task/graph-checkpoint", headers=INTERNAL, json=body)
    assert r.status_code == 200, r.text
    r = await client.get(
        "/internal/task/graph-checkpoint/latest",
        headers=INTERNAL,
        params={"tenant_key": "default", "thread_id": th},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["checkpoint_id"] == "ck-1"
    assert data["payload_type"] == "msgpack"
    assert data["payload_b64"] == "AQIDBA=="
    # 写入行 + 按 checkpoint_id 取回
    r = await client.put(
        "/internal/task/graph-checkpoint",
        headers=INTERNAL,
        json={**body, "kind": "write", "checkpoint_id": "ck-1", "payload_b64": "ZWZnaA=="},
    )
    assert r.status_code == 200
    r = await client.get(
        "/internal/task/graph-checkpoint/writes",
        headers=INTERNAL,
        params={"tenant_key": "default", "thread_id": th, "checkpoint_id": "ck-1"},
    )
    assert r.status_code == 200
    writes = r.json()["writes"]
    assert len(writes) == 1 and writes[0]["payload_b64"] == "ZWZnaA=="
    # 线程清理
    r = await client.delete(
        "/internal/task/graph-checkpoint",
        headers=INTERNAL,
        params={"tenant_key": "default", "thread_id": th},
    )
    assert r.status_code == 200 and r.json()["deleted"] == 2
    r = await client.get(
        "/internal/task/graph-checkpoint/latest",
        headers=INTERNAL,
        params={"tenant_key": "default", "thread_id": th},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_checkpoint_tenant_isolated_404_same_shape(
    client: AsyncClient, session: AsyncSession, make_user: Any
) -> None:
    """跨租户线程读写一律 404 同形(不暴露存在性);未知任务线程同形。"""
    _, token = await _user_with_task_perms(client, session, make_user)
    tid = await _create_task(client, token)
    sid = await _plan_one_step(client, tid, "default")
    th = _thread(tid, sid)
    body = {
        "tenant_key": "default",
        "thread_id": th,
        "payload_b64": "AQ==",
    }
    r = await client.put("/internal/task/graph-checkpoint", headers=INTERNAL, json=body)
    assert r.status_code == 200
    # 他租户 key 读 → TENANT_NOT_FOUND(404);写 → 同形
    for method, path in (("get", "latest"), ("put", "")):
        if method == "get":
            rr = await client.get(
                f"/internal/task/graph-checkpoint/{path}",
                headers=INTERNAL,
                params={"tenant_key": "no-such-tenant", "thread_id": th},
            )
        else:
            rr = await client.put(
                "/internal/task/graph-checkpoint",
                headers=INTERNAL,
                json={**body, "tenant_key": "no-such-tenant"},
            )
        assert rr.status_code == 404, rr.text
    # 任务归属他租户口径:未知任务线程(格式合法)读 → 404 同形
    fake = f"task:{'0' * 8}-0000-0000-0000-000000000000:step:{sid}"
    rr = await client.get(
        "/internal/task/graph-checkpoint/latest",
        headers=INTERNAL,
        params={"tenant_key": "default", "thread_id": fake},
    )
    assert rr.status_code == 404


# ── 图内挂起与确认处置 ──


@pytest.mark.asyncio
async def test_suspend_then_approve_resumes_with_hint(
    client: AsyncClient, session: AsyncSession, make_user: Any
) -> None:
    _, token = await _user_with_task_perms(client, session, make_user)
    tid = await _create_task(client, token)
    sid = await _plan_one_step(client, tid, "default")
    # 步骤启动(RUNNING)→ 图内挂起
    d = await client.post(
        "/internal/task/step-start",
        headers=INTERNAL,
        json={"task_id": tid, "tenant_key": "default", "step_id": sid},
    )
    assert d.json()["decision"] == "execute"
    out = await _suspend(client, tid, "default", sid)
    assert out["status"] == "pending"
    # 任务/步骤进入 AWAITING_APPROVAL;detail 暴露 pending 确认行
    r = await client.get(f"/tasks/{tid}", headers={"Authorization": f"Bearer {token}"})
    body = r.json()
    assert body["task"]["status"] == "AWAITING_APPROVAL"
    assert body["steps"][0]["status"] == "AWAITING_APPROVAL"
    pending = [c for c in body["graph_confirms"] if c["status"] == "pending"]
    assert len(pending) == 1 and pending[0]["tool_name"] == "create_system_announcement"
    # 重复挂起(同 confirm_id)幂等:reused=True 不重复上账
    out2 = await _suspend(client, tid, "default", sid)
    assert out2.get("reused") is True
    # 他人处置 404 同形
    _, token_b = await _user_with_task_perms(client, session, make_user, "tasker_b_gc")
    rb = await client.post(
        f"/tasks/{tid}/confirms/{pending[0]['confirm_id']}",
        json={"approve": True},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert rb.status_code == 404
    # 归属人批准 → 任务 RUNNING、步骤回队;再启动携带批准恢复提示
    r = await client.post(
        f"/tasks/{tid}/confirms/{pending[0]['confirm_id']}",
        json={"approve": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    r = await client.get(f"/tasks/{tid}", headers={"Authorization": f"Bearer {token}"})
    assert r.json()["task"]["status"] == "RUNNING"
    d = await client.post(
        "/internal/task/step-start",
        headers=INTERNAL,
        json={"task_id": tid, "tenant_key": "default", "step_id": sid},
    )
    assert d.json()["decision"] == "execute"
    hint = d.json().get("resume_confirm")
    assert hint is not None and hint["decision"] == "approved"
    assert hint["confirm_id"] == pending[0]["confirm_id"]


@pytest.mark.asyncio
async def test_confirm_reject_hint_and_idempotent(
    client: AsyncClient, session: AsyncSession, make_user: Any
) -> None:
    _, token = await _user_with_task_perms(client, session, make_user)
    tid = await _create_task(client, token)
    sid = await _plan_one_step(client, tid, "default")
    d = await client.post(
        "/internal/task/step-start",
        headers=INTERNAL,
        json={"task_id": tid, "tenant_key": "default", "step_id": sid},
    )
    assert d.json()["decision"] == "execute"
    await _suspend(client, tid, "default", sid, "conf-rej")
    r = await client.get(f"/tasks/{tid}", headers={"Authorization": f"Bearer {token}"})
    cid = [c["confirm_id"] for c in r.json()["graph_confirms"] if c["status"] == "pending"][0]
    r = await client.post(
        f"/tasks/{tid}/confirms/{cid}",
        json={"approve": False},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    # 已决再决:幂等同态(already_decided),状态机不二次迁移
    r2 = await client.post(
        f"/tasks/{tid}/confirms/{cid}",
        json={"approve": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 200
    d = await client.post(
        "/internal/task/step-start",
        headers=INTERNAL,
        json={"task_id": tid, "tenant_key": "default", "step_id": sid},
    )
    assert d.json()["resume_confirm"]["decision"] == "denied"


@pytest.mark.asyncio
async def test_suspend_rejects_when_task_not_running(
    client: AsyncClient, session: AsyncSession, make_user: Any
) -> None:
    """任务非 RUNNING(如已暂停)不可挂起:编排回调 409,agent 幂等收口。"""
    _, token = await _user_with_task_perms(client, session, make_user)
    tid = await _create_task(client, token)
    sid = await _plan_one_step(client, tid, "default")
    # 任务尚在 PLANNING(未启动步骤):挂起被拒
    r = await client.post(
        "/internal/task/step-suspended",
        headers=INTERNAL,
        json={
            "task_id": tid,
            "tenant_key": "default",
            "step_id": sid,
            "confirm_id": "conf-x",
            "tool_name": "t",
            "summary": "",
            "thread_id": _thread(tid, sid),
        },
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_graph_confirm_expiry_skips_step(
    client: AsyncClient, session: AsyncSession, make_user: Any
) -> None:
    """过期扫描:pending 确认超时 → expired;步骤 AWAITING_APPROVAL → SKIPPED;
    事件留痕。与任务审批门过期同语义。"""
    _, token = await _user_with_task_perms(client, session, make_user)
    tid = await _create_task(client, token)
    sid = await _plan_one_step(client, tid, "default")
    d = await client.post(
        "/internal/task/step-start",
        headers=INTERNAL,
        json={"task_id": tid, "tenant_key": "default", "step_id": sid},
    )
    assert d.json()["decision"] == "execute"
    await _suspend(client, tid, "default", sid, "conf-exp")
    # 强制过期(expires_at 提前)
    await session.execute(
        text(
            "UPDATE public.sys_agent_graph_confirm"
            " SET expires_at = now() - interval '1 minute' WHERE confirm_id = 'conf-exp'"
        )
    )
    await session.commit()
    from kongming.task.graph_repo import SqlGraphRepo
    from kongming.task.repository import SqlTaskRepo
    from kongming.task.service import TaskService

    svc = TaskService(SqlTaskRepo(session), graph_repo=SqlGraphRepo(session))
    await svc.mark_interrupted_stale()
    await session.commit()
    r = await client.get(f"/tasks/{tid}", headers={"Authorization": f"Bearer {token}"})
    body = r.json()
    assert body["steps"][0]["status"] == "SKIPPED"
    confirms = [c for c in body["graph_confirms"] if c["confirm_id"] == "conf-exp"]
    assert confirms and confirms[0]["status"] == "expired"
    assert any(e["kind"] == "graph_confirm_expired" for e in body["events"])
