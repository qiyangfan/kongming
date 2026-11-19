# ruff: noqa: S101, S105, ASYNC109 —— 冒烟脚本:断言即验收、口令为本地冒烟账号、超时参数形参
# pyright: reportUnknownParameterType=false, reportMissingTypeArgument=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""协作任务端到端冒烟脚本：创建并恢复包含并行 worker 与 critic 的任务，
检查运行状态、事件和账本。需要已启动的 API、Agent 及开发用例数据；
真实模型答案不作断言。"""

import asyncio
import sys
import time

import httpx

BASE = "http://localhost:8000"
USER = "smoke088"
PASSWORD = "Smoke#2026"
TERMINAL = ("DONE", "FAILED", "CANCELLED")


async def login(client: httpx.AsyncClient) -> dict[str, str]:
    r = await client.post("/auth/login", json={"username": USER, "password": PASSWORD})
    r.raise_for_status()
    data = r.json()
    return {"Authorization": f"Bearer {data['access_token']}"}


async def get_task(client: httpx.AsyncClient, h: dict[str, str], task_id: str) -> dict:
    r = await client.get(f"/tasks/{task_id}", headers=h)
    r.raise_for_status()
    return r.json()


async def wait_task(
    client: httpx.AsyncClient,
    h: dict[str, str],
    task_id: str,
    statuses: tuple[str, ...],
    timeout: float,
    *,
    auto_approve: bool = False,
) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        d = await get_task(client, h, task_id)
        status = d["task"]["status"]
        if status in statuses:
            return d
        if auto_approve and status == "AWAITING_APPROVAL":
            # 真实 LLM 规划可能给后续步骤标需确认:批准后继续等终态
            for s in d["steps"]:
                if s["status"] == "AWAITING_APPROVAL":
                    ra = await client.post(
                        f"/tasks/{task_id}/steps/{s['id']}/approve",
                        headers=h,
                        json={"approve": True},
                    )
                    assert ra.status_code == 200, ra.text
        await asyncio.sleep(1.0)
    # 超时带最后一次观测现场(只报目标集合无法定位卡点)
    try:
        live = await get_task(client, h, task_id)
    except httpx.HTTPError as e:
        raise AssertionError(
            f"任务 {task_id} 未在 {timeout}s 内到达 {statuses}(尾读失败: {e})"
        ) from e
    raise AssertionError(
        f"任务 {task_id} 未在 {timeout}s 内到达 {statuses}:"
        f" status={live['task']['status']}"
        f" fail_reason={live['task'].get('fail_reason')}"
        f" steps={[s['status'] for s in live['steps']]}"
    )


async def wait_runs(
    client: httpx.AsyncClient, h: dict[str, str], task_id: str, timeout: float
) -> list[dict]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = await client.get(f"/tasks/{task_id}/collab-runs", headers=h)
        r.raise_for_status()
        if r.json()["items"]:
            return r.json()["items"]
        await asyncio.sleep(1.0)
    raise AssertionError(f"任务 {task_id} 未在 {timeout}s 内产生协作运行")


async def cleanup_task(client: httpx.AsyncClient, h: dict[str, str], task_id: str) -> None:
    """兜底清理:断言失败/跳过路径不留烧真实 LLM 的在途任务。"""
    try:
        d = await get_task(client, h, task_id)
        if d["task"]["status"] not in TERMINAL:
            await client.post(f"/tasks/{task_id}/cancel", headers=h)
    except httpx.HTTPError:
        pass


async def declare_topology_and_launch(
    client: httpx.AsyncClient, h: dict[str, str], task_id: str, topology: dict
) -> None:
    """等规划落账 → 暂停制造声明窗口 → 首个 PENDING 步声明拓扑 → 复跑。"""
    d = await wait_task(client, h, task_id, ("RUNNING", "AWAITING_APPROVAL"), 120)
    if d["task"]["status"] == "RUNNING":
        rp = await client.post(f"/tasks/{task_id}/pause", headers=h)
        assert rp.status_code == 200, rp.text
        d = await wait_task(client, h, task_id, ("PAUSED",), 30)
    pending = [s for s in d["steps"] if s["status"] == "PENDING"]
    assert pending, f"无 PENDING 步骤可声明拓扑: {[s['status'] for s in d['steps']]}"
    rt = await client.put(
        f"/tasks/{task_id}/steps/{pending[0]['id']}/collab-topology",
        headers=h,
        json=topology,
    )
    assert rt.status_code == 200, rt.text
    print(f"[smoke] task={task_id} topology declared on step {pending[0]['ord']}: {rt.json()}")
    if d["task"]["status"] == "AWAITING_APPROVAL":
        # 遍历审批挂起步(首个 require_confirm 步未必在首位,批错步 404 假失败)
        for s in d["steps"]:
            if s["status"] == "AWAITING_APPROVAL":
                ra = await client.post(
                    f"/tasks/{task_id}/steps/{s['id']}/approve",
                    headers=h,
                    json={"approve": True},
                )
                assert ra.status_code == 200, ra.text
    else:
        rr = await client.post(f"/tasks/{task_id}/resume", headers=h)
        assert rr.status_code == 200, rr.text


async def _create_task(
    client: httpx.AsyncClient, h: dict[str, str], goal: str, created: list[str]
) -> str:
    """建任务、登记待清理清单并返回 task_id(只断言受理码)。"""
    r = await client.post(
        "/tasks",
        headers=h,
        json={"goal": goal, "datasource_key": "sakila"},
    )
    assert r.status_code == 202, r.text
    task_id = r.json()["task_id"]
    created.append(task_id)
    print(f"[smoke] task={task_id}")
    return task_id


async def _scenario_full_chain(
    client: httpx.AsyncClient, h: dict[str, str], created: list[str]
) -> None:
    """场景一:协作步骤全链(3 worker 并行 + critic)。"""
    task_id = await _create_task(
        client, h, "协作冒烟:对 sakila 库做协同交叉验证,统计影片分类维度的一致口径", created
    )
    await declare_topology_and_launch(
        client, h, task_id, {"max_workers": 3, "critic": True, "dispatch": "parallel"}
    )
    runs = await wait_runs(client, h, task_id, 300)
    print(f"[smoke] collab run appeared: id={runs[0]['id']} status={runs[0]['status']}")

    # auto_approve:后续步骤被规划标注需确认时批准放行(真实 LLM 非确定)
    final = await wait_task(client, h, task_id, TERMINAL, 600, auto_approve=True)
    status = final["task"]["status"]
    print(f"[smoke] task final={status} fail_reason={final['task'].get('fail_reason')}")

    runs = (await client.get(f"/tasks/{task_id}/collab-runs", headers=h)).json()["items"]
    assert runs, "终态后协作运行清单为空"
    run = runs[0]
    workers = run.get("workers") or []
    print(
        f"[smoke] run final: status={run['status']} verdict={run['verdict']} "
        f"workers={[(w['worker_no'], w['status']) for w in workers]}"
    )
    assert run["status"] in ("DONE", "FAILED", "PARTIAL", "CANCELLED"), run
    assert run["status"] != "RUNNING", "任务终态后协作运行不得滞留 RUNNING"
    assert workers, "协作运行无 worker 账本"
    for w in workers:
        assert w["trace_id"], "worker 缺 trace 父子关联"
        assert isinstance(w.get("usage"), dict), "worker 缺用量归集"
    events = [e["kind"] for e in final["events"]]
    for kind in ("collab_topology_set", "collab_started", "collab_finished"):
        assert kind in events, f"缺协作事件 {kind}: {events}"
    collab_step = next((s for s in final["steps"] if s["id"] == run["step_id"]), None)
    assert collab_step is not None, f"宿主步骤 {run['step_id']} 不在账本"
    if run["status"] == "DONE":
        assert collab_step["status"] == "DONE", "裁决 DONE 而步骤非 DONE(聚合结论须驱动步骤终态)"
    if run["status"] == "FAILED":
        assert collab_step["status"] in ("FAILED", "DONE"), "运行 FAILED 但步骤状态漂移"
    print(f"[smoke] 场景一(协作全链)通过: task={status} run={run['status']}")


async def _cancel_and_check_convergence(
    client: httpx.AsyncClient, h: dict[str, str], task2: str
) -> bool:
    """场景二主体:等协作开账后取消,核对在途账收敛;未及开账返回 False。"""
    # 等协作开账后取消
    try:
        await wait_runs(client, h, task2, 60)
    except AssertionError:
        # 协作未及开账=取消收敛场景未验证:如实报失败并清场,不假绿
        print("[smoke] 场景二:协作未及开账(时序窗口),本场景未验证 → 失败退出")
        return False
    # cancel 前核对现态:协作先跑完(任务已终态)则跳过取消走收敛断言
    live = await get_task(client, h, task2)
    if live["task"]["status"] not in TERMINAL:
        rc = await client.post(f"/tasks/{task2}/cancel", headers=h)
        assert rc.status_code == 200, rc.text
        await wait_task(client, h, task2, ("CANCELLED",), 30)
    runs2 = (await client.get(f"/tasks/{task2}/collab-runs", headers=h)).json()["items"]
    assert all(rr["status"] != "RUNNING" for rr in runs2), "取消后协作运行滞留 RUNNING"
    cancelled = [rr for rr in runs2 if rr["status"] == "CANCELLED"]
    skipped = [w for rr in runs2 for w in (rr.get("workers") or []) if w["status"] == "SKIPPED"]
    print(
        f"[smoke] 场景二(取消收敛)通过: runs={[rr['status'] for rr in runs2]} "
        f"skipped_workers={len(skipped)}"
    )
    assert cancelled or all(rr["status"] in ("FAILED", "PARTIAL", "DONE") for rr in runs2)
    return True


async def _scenario_cancel(
    client: httpx.AsyncClient, h: dict[str, str], created: list[str]
) -> bool:
    """场景二:运行中取消 → 在途协作账收敛;未验证返回 False。"""
    task2 = await _create_task(client, h, "协作冒烟:取消场景,统计租赁金额 Top5 客户", created)
    await declare_topology_and_launch(client, h, task2, {"max_workers": 3})
    return await _cancel_and_check_convergence(client, h, task2)


async def main() -> int:
    created: list[str] = []
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as client:
        h = await login(client)
        try:
            await _scenario_full_chain(client, h, created)
            if not await _scenario_cancel(client, h, created):
                return 1
        finally:
            for tid in created:
                await cleanup_task(client, h, tid)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
