# ruff: noqa: S101, S105, ASYNC109 —— 冒烟脚本:断言即验收、口令为本地冒烟账号、超时参数形参
# pyright: reportUnknownParameterType=false, reportMissingTypeArgument=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""115 圆桌端到端冒烟(4.3):建任务→步骤声明圆桌拓扑(2 参与者)
→恢复→轮次对辩真实运行→轮次转写/参与者账本/事件可查→诚实收口。

前置:api:8000 + agent:8001 已起,dev 库含冒烟账号 smoke088 / sakila 数据源。
真实 LLM 驱动:结论与共识与否不预设,断言口径=链路贯通与账本诚实
(状态机/事件/轮次转写/参与者行),非业务答案正确性;收敛通道二选一如实
打印(共识判定 / 轮次触顶保留分歧),不假绿。
"""

import asyncio
import sys
import time

import httpx

BASE = "http://localhost:8000"
USER = "smoke088"
PASSWORD = "Smoke#2026"
TERMINAL = ("DONE", "FAILED", "CANCELLED")

# 长轮询跨 JWT TTL:401 即重登录(可变状态字典,调用方持引用)
STATE: dict[str, dict[str, str]] = {}


async def login(client: httpx.AsyncClient) -> dict[str, str]:
    r = await client.post("/auth/login", json={"username": USER, "password": PASSWORD})
    r.raise_for_status()
    data = r.json()
    return {"Authorization": f"Bearer {data['access_token']}"}


def _h() -> dict[str, str]:
    return STATE["h"]


async def get_task(client: httpx.AsyncClient, h: dict[str, str], task_id: str) -> dict:
    r = await client.get(f"/tasks/{task_id}", headers=_h())
    if r.status_code == 401:
        STATE["h"] = await login(client)
        r = await client.get(f"/tasks/{task_id}", headers=_h())
    r.raise_for_status()
    return r.json()


async def wait_task(
    client: httpx.AsyncClient,
    h: dict[str, str],
    task_id: str,
    statuses: tuple[str, ...],
    timeout: float,
) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        d = await get_task(client, h, task_id)
        status = d["task"]["status"]
        if status in statuses:
            return d
        await asyncio.sleep(1.0)
    live = await get_task(client, h, task_id)
    raise AssertionError(
        f"任务 {task_id} 未在 {timeout}s 内到达 {statuses}:"
        f" status={live['task']['status']}"
        f" fail_reason={live['task'].get('fail_reason')}"
        f" steps={[s['status'] for s in live['steps']]}"
    )


async def wait_roundtable_run(
    client: httpx.AsyncClient, h: dict[str, str], task_id: str, timeout: float
) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = await client.get(f"/tasks/{task_id}/collab-runs", headers=_h())
        if r.status_code == 401:
            STATE["h"] = await login(client)
            r = await client.get(f"/tasks/{task_id}/collab-runs", headers=_h())
        r.raise_for_status()
        items = [x for x in r.json()["items"] if x.get("topology", {}).get("kind") == "roundtable"]
        if items:
            return items[0]
        await asyncio.sleep(1.0)
    raise AssertionError(f"任务 {task_id} 未在 {timeout}s 内产生圆桌运行")


async def cleanup_task(client: httpx.AsyncClient, h: dict[str, str], task_id: str) -> None:
    """兜底清理:断言失败/跳过路径不留烧真实 LLM 的在途任务。"""
    try:
        d = await get_task(client, h, task_id)
        if d["task"]["status"] not in TERMINAL:
            await client.post(f"/tasks/{task_id}/cancel", headers=h)
    except httpx.HTTPError:
        pass


async def declare_roundtable_and_launch(
    client: httpx.AsyncClient, h: dict[str, str], task_id: str, max_rounds: int
) -> dict:
    """等规划落账 → 暂停制造声明窗口 → 首个 PENDING 步声明圆桌拓扑 → 复跑。
    返回归一后的拓扑(断言名册/轮次钳制生效)。"""
    d = await wait_task(client, h, task_id, ("RUNNING",), 120)
    rp = await client.post(f"/tasks/{task_id}/pause", headers=_h())
    assert rp.status_code == 200, rp.text
    d = await wait_task(client, h, task_id, ("PAUSED",), 30)
    pending = [s for s in d["steps"] if s["status"] == "PENDING"]
    assert pending, f"无 PENDING 步骤可声明拓扑: {[s['status'] for s in d['steps']]}"
    rt = await client.put(
        f"/tasks/{task_id}/steps/{pending[0]['id']}/collab-topology",
        headers=_h(),
        json={"kind": "roundtable", "max_rounds": max_rounds},
    )
    assert rt.status_code == 200, rt.text
    topo = rt.json()
    assert topo["kind"] == "roundtable", topo
    assert len(topo["participants"]) >= 2, f"缺省名册不足 2: {topo}"
    rr = await client.post(f"/tasks/{task_id}/resume", headers=_h())
    assert rr.status_code == 200, rr.text
    return topo


async def main() -> int:
    created: list[str] = []
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as client:
        STATE["h"] = await login(client)
        h = STATE["h"]
        try:
            # ── 场景一:圆桌全链(2 参与者 × ≤2 轮,真实对辩)──
            r = await client.post(
                "/tasks",
                headers=h,
                json={
                    "goal": (
                        "圆桌冒烟:两位专家圆桌对辩——rental 表的月度租赁量"
                        "是否存在明显季节性,给出数据依据并相互核查口径"
                    ),
                    "datasource_key": "sakila",
                },
            )
            assert r.status_code == 202, r.text
            task_id = r.json()["task_id"]
            created.append(task_id)
            print(f"[smoke] task={task_id}")

            topo = await declare_roundtable_and_launch(client, h, task_id, max_rounds=2)
            print(f"[smoke] roundtable declared: {topo}")

            run = await wait_roundtable_run(client, h, task_id, 300)
            print(f"[smoke] roundtable run appeared: id={run['id']} status={run['status']}")

            # 等圆桌运行终态(运行口径);任务可能因步级 token 预算被 090 闸
            # PAUSED——平台诚实语义,不作为冒烟失败(与 091 冒烟同款处理)
            deadline = time.monotonic() + 900
            while time.monotonic() < deadline:
                runs_now = (await client.get(f"/tasks/{task_id}/collab-runs", headers=_h())).json()[
                    "items"
                ]
                run = next(x for x in runs_now if x["topology"].get("kind") == "roundtable")
                if run["status"] != "RUNNING":
                    break
                await asyncio.sleep(2.0)
            final = await get_task(client, h, task_id)
            status = final["task"]["status"]
            STATE["h"] = await login(client)  # 长等待后刷新
            runs = (await client.get(f"/tasks/{task_id}/collab-runs", headers=_h())).json()["items"]
            run = next(x for x in runs if x["topology"].get("kind") == "roundtable")
            workers = run.get("workers") or []
            turns = run.get("turns") or []
            print(
                f"[smoke] run final: status={run['status']} verdict={run['verdict']} "
                f"workers={[(w['worker_no'], w['status']) for w in workers]} "
                f"turns={len(turns)}"
            )
            # 账本诚实断言(不预设共识结论)
            assert run["status"] in ("DONE", "FAILED", "PARTIAL", "CANCELLED"), run
            assert run["status"] != "RUNNING", "任务终态后圆桌运行不得滞留 RUNNING"
            assert workers, "圆桌运行无参与者账本"
            for w in workers:
                assert w["trace_id"], "参与者缺 trace 父子关联"
                assert isinstance(w.get("usage"), dict), "参与者缺用量归集"
            speech_turns = [t for t in turns if t["kind"] == "speech"]
            verdict_turns = [t for t in turns if t["kind"] == "verdict"]
            assert verdict_turns, "圆桌无主席判定轮次(治理协议未执行)"
            assert all(t["worker_no"] == 0 for t in verdict_turns), "判定行必须 0 号位"
            print(
                f"[smoke] 轮次转写: speech={len(speech_turns)} verdict={len(verdict_turns)} "
                f"判定={[t['content'].get('consensus') for t in verdict_turns]}"
            )
            events = [e["kind"] for e in final["events"]]
            for kind in ("collab_topology_set", "collab_started", "collab_finished"):
                assert kind in events, f"缺协作事件 {kind}: {events}"
            round_events = [
                e for e in events if e in ("collab_round_finished", "collab_round_verdict")
            ]
            print(f"[smoke] 生命周期事件: round_finished/verdict={len(round_events)}")
            collab_step = next((s for s in final["steps"] if s["id"] == run["step_id"]), None)
            assert collab_step is not None, f"宿主步骤 {run['step_id']} 不在账本"
            if run["status"] == "DONE":
                assert collab_step["status"] == "DONE", "共识收口而宿主步骤非 DONE"
            if run["status"] == "FAILED":
                assert collab_step["status"] in ("FAILED", "DONE"), "运行 FAILED 但步骤状态漂移"
            dissent = next(
                (e.get("dissent") for e in final["events"] if e["kind"] == "collab_finished"),
                None,
            )
            channel = "共识判定" if dissent is False else "未共识保留分歧(触顶/弃权/预算)"
            print(f"[smoke] 场景一(圆桌全链)通过: task={status} 收口通道={channel}")

            # ── 场景二:运行中取消 → 在途圆桌账收敛 ──
            r2 = await client.post(
                "/tasks",
                headers=h,
                json={
                    "goal": "圆桌冒烟:取消场景,分析客户租赁行为的类别分布",
                    "datasource_key": "sakila",
                },
            )
            assert r2.status_code == 202, r2.text
            task2 = r2.json()["task_id"]
            created.append(task2)
            await declare_roundtable_and_launch(client, h, task2, max_rounds=2)
            try:
                await wait_roundtable_run(client, h, task2, 60)
            except AssertionError:
                print("[smoke] 场景二:圆桌未及开账(时序窗口),本场景未验证 → 失败退出")
                return 1
            live = await get_task(client, h, task2)
            if live["task"]["status"] not in TERMINAL:
                rc = await client.post(f"/tasks/{task2}/cancel", headers=_h())
                assert rc.status_code == 200, rc.text
                await wait_task(client, h, task2, ("CANCELLED",), 30)
            runs2 = (await client.get(f"/tasks/{task2}/collab-runs", headers=_h())).json()["items"]
            rt2 = [x for x in runs2 if x.get("topology", {}).get("kind") == "roundtable"]
            assert all(x["status"] != "RUNNING" for x in rt2), "取消后圆桌运行滞留 RUNNING"
            print(
                f"[smoke] 场景二(取消收敛)通过: runs={[x['status'] for x in rt2]} "
                f"turns={[len(x.get('turns') or []) for x in rt2]}"
            )
        finally:
            for tid in created:
                await cleanup_task(client, h, tid)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
