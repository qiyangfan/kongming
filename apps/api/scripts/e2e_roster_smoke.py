# ruff: noqa: S101, S105, ASYNC109 —— 冒烟脚本:断言即验收、口令为本地冒烟账号、超时参数形参
# pyright: reportUnknownParameterType=false, reportMissingTypeArgument=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""collab-worker-roster 端到端冒烟(5.2):建 2 Bot → 建任务 → 步骤声明分工
名册拓扑(2 成员点名)→恢复→分配制协作真实运行→账本逐 worker bot_key 与
成员标签前缀可查→诚实收口。

前置:api:8000 + agent:8001 已起,dev 库含冒烟账号 smoke088 / sakila 数据源。
真实 LLM 驱动:结论内容不预设,断言口径=链路贯通与账本诚实(名册落账/
逐 worker 绑定/事件),非业务答案正确性;终态 DONE/FAILED/PARTIAL 均为
合法收口,如实打印不假绿。
"""

import asyncio
import sys
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

BASE = "http://localhost:8000"
USER = "smoke088"
PASSWORD = "Smoke#2026"
TERMINAL = ("DONE", "FAILED", "CANCELLED")
SUFFIX = int(time.time())

# 长轮询跨 JWT TTL:401 即重登录(可变状态字典,调用方持引用)
STATE: dict[str, dict[str, str]] = {}


def _h() -> dict[str, str]:
    return STATE["h"]


async def login(client: httpx.AsyncClient) -> None:
    r = await client.post("/auth/login", json={"username": USER, "password": PASSWORD})
    r.raise_for_status()
    STATE["h"] = {"Authorization": f"Bearer {r.json()['access_token']}"}


async def relogin_on_401(
    client: httpx.AsyncClient,
    fn: Callable[..., Awaitable[dict[str, Any]]],
    *args: object,
    **kw: object,
):
    try:
        return await fn(client, *args, **kw)
    except httpx.HTTPStatusError as e:
        if e.response.status_code != 401:
            raise
        await login(client)
        return await fn(client, *args, **kw)


async def get_task(client: httpx.AsyncClient, task_id: str) -> dict:
    r = await client.get(f"/tasks/{task_id}", headers=_h())
    r.raise_for_status()
    return r.json()


async def wait_task(
    client: httpx.AsyncClient, task_id: str, statuses: tuple[str, ...], timeout: float
) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        d = await relogin_on_401(client, get_task, task_id)
        if d["task"]["status"] in statuses:
            return d
        if d["task"]["status"] == "AWAITING_APPROVAL":
            for s in d["steps"]:
                if s["status"] == "AWAITING_APPROVAL":
                    ra = await client.post(
                        f"/tasks/{task_id}/steps/{s['id']}/approve",
                        headers=_h(),
                        json={"approve": True},
                    )
                    assert ra.status_code == 200, ra.text
        await asyncio.sleep(1.0)
    live = await get_task(client, task_id)
    raise AssertionError(
        f"任务 {task_id} 未在 {timeout}s 内到达 {statuses}:"
        f" status={live['task']['status']}"
        f" fail_reason={live['task'].get('fail_reason')}"
        f" steps={[s['status'] for s in live['steps']]}"
    )


async def ensure_bot(client: httpx.AsyncClient, key: str, name: str, prompt: str) -> str:
    """幂等建 Bot(键带时间后缀防撞);返回 bot_id。"""
    r = await client.post(
        "/system/bots",
        headers=_h(),
        json={
            "bot_key": key,
            "name": name,
            "system_prompt": prompt,
            "tools": ["execute_sql"],
            "datasource_key": "sakila",
        },
    )
    assert r.status_code == 201, f"建 Bot {key} 失败: {r.text}"
    return str(r.json()["id"])


async def main() -> int:
    created_tasks: list[str] = []
    created_bots: list[str] = []
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as client:
        await login(client)
        try:
            fin_id = await ensure_bot(
                client,
                f"roster_fin_{SUFFIX}",
                "冒烟-财务视角",
                "你是财务分析专家:从收入与金额口径分析问题,优先用只读 SQL 查证关键数字。",
            )
            created_bots.append(fin_id)
            ops_id = await ensure_bot(
                client,
                f"roster_ops_{SUFFIX}",
                "冒烟-运营视角",
                "你是运营分析专家:从租赁与库存运营口径分析问题,优先用只读 SQL 查证关键数字。",
            )
            created_bots.append(ops_id)
            fin_key = f"roster_fin_{SUFFIX}"
            ops_key = f"roster_ops_{SUFFIX}"

            r = await client.post(
                "/tasks",
                headers=_h(),
                json={
                    "goal": "名册冒烟:从财务与运营双视角协同分析 sakila 租赁业务的整体态势",
                    "datasource_key": "sakila",
                },
            )
            assert r.status_code == 202, r.text
            task_id = r.json()["task_id"]
            created_tasks.append(task_id)
            print(f"[smoke] task={task_id} bots={fin_key},{ops_key}")

            # 等规划落账 → 暂停制造声明窗口 → 首个 PENDING 步声明名册拓扑 → 复跑
            d = await wait_task(client, task_id, ("RUNNING", "AWAITING_APPROVAL"), 120)
            if d["task"]["status"] == "RUNNING":
                rp = await client.post(f"/tasks/{task_id}/pause", headers=_h())
                assert rp.status_code == 200, rp.text
                d = await wait_task(client, task_id, ("PAUSED",), 30)
            pending = [s for s in d["steps"] if s["status"] == "PENDING"]
            assert pending, f"无 PENDING 步骤: {[s['status'] for s in d['steps']]}"
            topo_req = {
                "max_workers": 2,
                "dispatch": "parallel",
                "workers": [
                    {"name": "财务视角", "bot_key": fin_key},
                    {"name": "运营视角", "bot_key": ops_key},
                ],
            }
            rt = await client.put(
                f"/tasks/{task_id}/steps/{pending[0]['id']}/collab-topology",
                headers=_h(),
                json=topo_req,
            )
            assert rt.status_code == 200, rt.text
            echoed = rt.json().get("workers") or []
            assert len(echoed) == 2, f"声明响应名册回显异常: {rt.text}"
            print(f"[smoke] 名册声明受理: {[(w['name'], w['bot_key']) for w in echoed]}")

            if d["task"]["status"] == "AWAITING_APPROVAL":
                for s in d["steps"]:
                    if s["status"] == "AWAITING_APPROVAL":
                        ra = await client.post(
                            f"/tasks/{task_id}/steps/{s['id']}/approve",
                            headers=_h(),
                            json={"approve": True},
                        )
                        assert ra.status_code == 200, ra.text
            else:
                rr = await client.post(f"/tasks/{task_id}/resume", headers=_h())
                assert rr.status_code == 200, rr.text

            # 等协作运行出现并到非 RUNNING 终态(诚实收口:任意终态均合法)
            deadline = time.monotonic() + 420
            run: dict = {}
            while time.monotonic() < deadline:
                rruns = await client.get(f"/tasks/{task_id}/collab-runs", headers=_h())
                rruns.raise_for_status()
                items = rruns.json()["items"]
                if items and items[0]["status"] != "RUNNING":
                    run = items[0]
                    break
                await asyncio.sleep(2.0)
            assert run, "协作运行未在时限内收口"
            workers = run.get("workers") or []
            print(
                f"[smoke] run final: status={run['status']} verdict={run['verdict']} "
                f"workers={[(w['worker_no'], w['status'], w.get('bot_key')) for w in workers]}"
            )
            assert workers, "协作运行无 worker 账本"
            # 账本口径:点名成员执行的行必须带 bot_key 与成员标签前缀
            bound = [w for w in workers if w.get("bot_key")]
            assert bound, f"无任何 worker 行携带 bot_key(名册未生效): {workers}"
            for w in bound:
                assert str(w["subgoal"]).startswith("["), f"名册成员行缺标签前缀: {w['subgoal']}"
            used_keys = {str(w.get("bot_key")) for w in bound}
            assert used_keys <= {fin_key, ops_key}, f"账本出现名册外 bot_key: {used_keys}"
            for w in workers:
                assert w["trace_id"], "worker 缺 trace 父子关联"
                assert isinstance(w.get("usage"), dict), "worker 缺用量归集"

            # 验收单元=run 收口(上方已断言);任务终态仅如实打印——后续普通步
            # 被步级 token 预算闸暂停/失败属 090 语义,非冒烟失败(115 先例口径)
            final = await wait_task(client, task_id, TERMINAL + ("PAUSED",), 240)
            task_status = final["task"]["status"]
            events = [e["kind"] for e in final["events"]]
            for kind in ("collab_topology_set", "collab_started", "collab_finished"):
                assert kind in events, f"缺协作事件 {kind}"
            print(
                f"[smoke] 名册冒烟通过: task={task_status} run={run['status']} 绑定行={len(bound)}"
            )
        finally:
            for tid in created_tasks:
                try:
                    live = await get_task(client, tid)
                    if live["task"]["status"] not in TERMINAL:
                        await client.post(f"/tasks/{tid}/cancel", headers=_h())
                except httpx.HTTPError:
                    pass
            for bid in created_bots:
                try:
                    await client.delete(f"/system/bots/{bid}", headers=_h())
                except httpx.HTTPError:
                    pass
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
