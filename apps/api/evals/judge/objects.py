"""judge 评审对象取数(092,任务 2.4):报告/任务/协作经 api 查询端点、
文本回答经问答链路(persist=false)——只读,评审对象零写副作用(spec)。

对象清单形态(抽检层):按创建时间倒序取最新 N 个;存量不足取全量;
类型空缺=空清单如实记录(门禁口径:不计失败)。
"""
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportPrivateUsage=false

import asyncio
import json
import logging
from datetime import datetime
from typing import cast

import httpx

from kongming.evalj.models import (
    OBJECT_ANSWER,
    OBJECT_COLLAB,
    OBJECT_REPORT,
    OBJECT_ROUNDTABLE,
    OBJECT_TASK,
    JudgeObject,
)

_TEXT_LIMIT = 6000  # 送 judge 的产出正文截断(评审摘要而非全文重放)
_TIMEOUT = 60.0

# 文本回答评审题面(知识/融合各一;093 落地后融合答天然入清单)
ANSWER_QUESTIONS: tuple[tuple[str, str], ...] = (
    ("退货政策是什么?", "知识"),
    ("逾期费是怎么规定的?片库里租金最贵的影片租金是多少?", "融合"),
    ("租金政策是怎么规定的?片库平均租金是多少?", "融合"),
)


class ObjectClient:
    """api 查询端点只读客户端(登录态复用评估器同款 admin 账密)。"""

    def __init__(self, base_url: str, token: str) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}

    async def _get(self, path: str) -> dict[str, object]:
        """GET + JSON 收窄(非对象响应=端点契约破坏,显式失败——静默空对象
        会让四类对象集体变空清单,门禁假绿)。"""
        async with httpx.AsyncClient(base_url=self._base, timeout=_TIMEOUT) as c:
            r = await c.get(path, headers=self._headers)
            r.raise_for_status()
            body: object = r.json()
            if not isinstance(body, dict):
                raise ValueError(f"端点 {path} 响应非 JSON 对象(契约漂移,拒绝静默降级)")
            return cast("dict[str, object]", body)


def _dict_list(value: object) -> list[dict[str, object]]:
    """JSON 数组 → dict 元素列表(异形元素丢弃;类型收窄单点)。"""
    if not isinstance(value, list):
        return []
    return [cast("dict[str, object]", it) for it in value if isinstance(it, dict)]


def _json_short(value: object, limit: int) -> str:
    """dict/其他异形值 → 截断 JSON 文本(评审正文喂入;None/空=空串)。"""
    if value is None or value == {} or value == "":
        return ""
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:  # 不可序列化(不应发生,防御性收口)
        text = str(value)
    return text[:limit]


async def fetch_report_objects(client: ObjectClient, limit: int) -> list[JudgeObject]:
    """报告评审对象:最新报告的标题+章节正文(088 交付物;READY/PARTIAL 均评)。"""
    data = await client._get(f"/reports?page=1&size={max(limit, 1)}")
    items = _dict_list(data.get("items"))
    out: list[JudgeObject] = []
    for item in items[:limit]:
        rid = str(item.get("id") or "")
        if not rid:
            continue
        detail = await client._get(f"/reports/{rid}")
        report = _dict_list([detail.get("report")])
        title = str(report[0].get("title") if report else "") or rid
        body_parts: list[str] = []
        for sec in _dict_list(detail.get("sections")):
            narrative = str(sec.get("narrative_md") or sec.get("content") or "")
            body_parts.append(f"## {sec.get('title', '')}\n{narrative}")
        text = (f"报告标题:{title}\n\n" + "\n\n".join(body_parts))[:_TEXT_LIMIT]
        out.append(
            JudgeObject(
                object_type=OBJECT_REPORT,
                object_id=rid,
                object_ref=f"{title}(v{item.get('version', '')})"[:200],
                text=text,
            )
        )
    return out


async def fetch_task_objects(client: ObjectClient, limit: int) -> list[JudgeObject]:
    """任务评审对象:最新 DONE 任务的 goal+证据化步骤账本(090 交付物)。
    字段严格按 /tasks/{{id}} 契约(TaskDetailOut:goal/status 在 task 嵌套内,
    步骤=goal/status/evidence;该契约无「最终结论」字段,不虚构——评审输入
    必须真实,近空文本会让判定失真)。"""
    data = await client._get("/tasks?page=1&size=50&status=DONE")
    items = _dict_list(data.get("items"))
    out: list[JudgeObject] = []
    for item in items[:limit]:
        tid = str(item.get("id") or "")
        if not tid:
            continue
        detail = await client._get(f"/tasks/{tid}")
        task = _dict_list([detail.get("task")])
        task_goal = str(task[0].get("goal") or "") if task else ""
        task_status = str(task[0].get("status") or "") if task else ""
        fail_reason = str(task[0].get("fail_reason") or "") if task else ""
        step_lines: list[str] = []
        for step in _dict_list(detail.get("steps")):
            evidence = _json_short(step.get("evidence"), 300)
            line = f"- [{step.get('status', '')}] {step.get('goal', '')}"
            if evidence:
                line += f"(证据:{evidence})"
            if step.get("fail_reason"):
                line += f"(失败:{step.get('fail_reason')})"
            step_lines.append(line)
        text = (
            f"任务目标:{task_goal or tid}\n"
            f"任务状态:{task_status}\n"
            + (f"失败原因:{fail_reason}\n" if fail_reason else "")
            + "步骤账本(证据驱动,无独立结论字段):\n"
            + "\n".join(step_lines)
        )[:_TEXT_LIMIT]
        out.append(
            JudgeObject(
                object_type=OBJECT_TASK,
                object_id=tid,
                object_ref=(task_goal or tid)[:200],
                text=text,
            )
        )
    return out


async def fetch_collab_objects(client: ObjectClient, limit: int) -> list[JudgeObject]:
    """协作评审对象:最新 DONE 任务的协作 run(091 账本)。字段严格按
    CollabRunOut 契约:run 级=topology/status/verdict/depth(无 goal/conclusion
    顶层字段——目标经 step_id 映射步骤 goal);worker 级=subgoal/bot_key/
    status/conclusion(dict,JSON 化喂入,不 str() 成 Python repr)。"""
    data = await client._get("/tasks?page=1&size=50&status=DONE")
    items = _dict_list(data.get("items"))
    out: list[JudgeObject] = []
    for item in items:
        if len(out) >= limit:
            break
        tid = str(item.get("id") or "")
        if not tid:
            continue
        detail = await client._get(f"/tasks/{tid}")
        step_goal = {
            str(s.get("id")): str(s.get("goal") or "") for s in _dict_list(detail.get("steps"))
        }
        runs = await client._get(f"/tasks/{tid}/collab-runs")
        for run in _dict_list(runs.get("items")):
            if len(out) >= limit:
                break
            goal = step_goal.get(str(run.get("step_id")), "")
            worker_lines: list[str] = []
            for w in _dict_list(run.get("workers")):
                who = str(w.get("bot_key") or "") or f"#{w.get('worker_no', '?')}"
                subgoal = str(w.get("subgoal") or "")
                conclusion = _json_short(w.get("conclusion"), 300)
                worker_lines.append(
                    f"- [{w.get('status', '')}] {who}({subgoal}): {conclusion or '无产出'}"
                )
            text = (
                f"协作目标:{goal or tid}\n"
                f"协作终态:{run.get('status', '')}(裁决:{run.get('verdict') or '—'})\n"
                f"worker 产出:\n" + "\n".join(worker_lines)
            )[:_TEXT_LIMIT]
            out.append(
                JudgeObject(
                    object_type=OBJECT_COLLAB,
                    object_id=str(run.get("id") or ""),
                    object_ref=(goal or str(run.get("id") or ""))[:200],
                    text=text,
                )
            )
    return out


# 圆桌对象资格 = 运行终态(收口诚实语义:DONE=共识收口,PARTIAL=预算/截断
# 诚实收口)——评审单元是 run(转写自含问题+发言+判定),宿主任务的后续
# 步骤是否被预算饿死与对辩质量无关,不以任务终态为门槛(115 决策;
# 与 092「DONE 任务」口径分叉:task/collab 的交付物是步骤产出,圆桌是收口
# 转写本身)。CANCELLED/RUNNING = 未完成旧账,不评。
_ROUNDTABLE_ELIGIBLE = frozenset({"DONE", "PARTIAL"})


async def fetch_roundtable_objects(client: ObjectClient, limit: int) -> list[JudgeObject]:
    """圆桌评审对象(115):拓扑形态为 roundtable 且运行已诚实收口的协作
    run。装配轮次转写(发言/弃权/判定按序)供 rubric 评共识质量/分歧诚实/
    证据接地/收敛效率;字段严格按 CollabRunOut 契约(turns 为查询面扩展)。"""
    data = await client._get("/tasks?page=1&size=50")
    items = _dict_list(data.get("items"))
    out: list[JudgeObject] = []
    for item in items:
        if len(out) >= limit:
            break
        tid = str(item.get("id") or "")
        if not tid:
            continue
        detail = await client._get(f"/tasks/{tid}")
        step_goal = {
            str(s.get("id")): str(s.get("goal") or "") for s in _dict_list(detail.get("steps"))
        }
        runs = await client._get(f"/tasks/{tid}/collab-runs")
        for run in _dict_list(runs.get("items")):
            if len(out) >= limit:
                break
            topo = run.get("topology")
            topo_d = topo if isinstance(topo, dict) else {}
            if topo_d.get("kind") != "roundtable":
                continue
            if str(run.get("status") or "") not in _ROUNDTABLE_ELIGIBLE:
                continue
            goal = step_goal.get(str(run.get("step_id")), "")
            turn_lines: list[str] = []
            for t in _dict_list(run.get("turns")):
                worker_no_raw = t.get("worker_no")
                worker_no = int(worker_no_raw) if isinstance(worker_no_raw, (int, float)) else 0
                who = "主席" if worker_no == 0 else f"#{worker_no}"
                content = t.get("content")
                cd = content if isinstance(content, dict) else {}
                if str(t.get("kind")) == "verdict":
                    turn_lines.append(
                        f"- 第{t.get('round_no')}轮 {who}:判定"
                        f" consensus={cd.get('consensus')}({cd.get('reason')})"
                    )
                    continue
                stance = str(cd.get("stance") or "")
                turn_lines.append(
                    f"- 第{t.get('round_no')}轮 {who}[{stance}]:"
                    f" {_json_short(cd.get('summary'), 240) or '弃权'}"
                )
            text = (
                f"圆桌问题:{goal or tid}\n"
                f"终态:{run.get('status', '')}(裁决:{run.get('verdict') or '—'})\n"
                f"轮次转写:\n" + "\n".join(turn_lines)
            )[:_TEXT_LIMIT]
            out.append(
                JudgeObject(
                    object_type=OBJECT_ROUNDTABLE,
                    object_id=str(run.get("id") or ""),
                    object_ref=(goal or str(run.get("id") or ""))[:200],
                    text=text,
                )
            )
    return out


async def fetch_answer_objects(
    tenant: str, limit: int, datasource_key: str = "sakila"
) -> list[JudgeObject]:
    """文本回答评审对象:经问答链路现跑题面(persist=false,零业务落库)——
    与 eval 生成侧同路径;回答正文+来源即评审对象(093 知识/融合答)。
    连接目标由 conftest 模块级 API_BASE 单源(评估器同进程同目标,无第二入口)。"""
    from conftest import call_chat  # 评估器既有链路调用(persist=false)

    out: list[JudgeObject] = []
    for question, kind in ANSWER_QUESTIONS:
        if len(out) >= limit:
            break
        try:
            r = await call_chat(question, tenant, datasource_key=datasource_key)
            r.raise_for_status()
        except Exception as e:  # noqa: BLE001  单对象取数失败不中断清单(独立收口在 runner)
            logging.warning("answer 评审对象取数失败(%s): %s", kind, e)
            continue
        body = r.json()
        sources = _dict_list(body.get("sources"))
        src_lines = [f"- {s.get('citation_key')}: {s.get('source_ref')}" for s in sources]
        text = (
            f"问题:{question}\n回答正文:\n{body.get('summary') or ''}\n"
            f"引用来源:\n"
            + ("\n".join(src_lines) or "(无)")
            + f"\n数据行数:{len(body.get('rows') or [])}"
        )[:_TEXT_LIMIT]
        out.append(
            JudgeObject(
                object_type=OBJECT_ANSWER,
                object_id=f"{kind}-{datetime.now().strftime('%Y%m%d%H%M%S')}-{len(out)}",
                object_ref=f"[{kind}] {question}"[:200],
                text=text,
            )
        )
    return out


async def gather_objects(
    client: ObjectClient, tenant: str, per_type: int
) -> dict[str, list[JudgeObject]]:
    """四类对象并发取数(类型间独立,单类型失败=空清单如实记录——
    异常必须落日志,与「类型空缺」可区分)。"""
    results = await asyncio.gather(
        fetch_report_objects(client, per_type),
        fetch_task_objects(client, per_type),
        fetch_collab_objects(client, per_type),
        fetch_answer_objects(tenant, per_type),
        fetch_roundtable_objects(client, per_type),
        return_exceptions=True,
    )
    names = (OBJECT_REPORT, OBJECT_TASK, OBJECT_COLLAB, OBJECT_ANSWER, OBJECT_ROUNDTABLE)
    out: dict[str, list[JudgeObject]] = {}
    for name, res in zip(names, results, strict=True):
        if isinstance(res, BaseException):
            logging.warning("judge 对象取数失败(%s): %s", name, res)
            out[name] = []
        else:
            out[name] = list(res)
    return out
