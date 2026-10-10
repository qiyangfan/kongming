"""协作组合根工厂(091 接线):把 run_collab 编排装配进任务步骤执行。

职责:开账(fail-closed)→ worker=一次受限 ReAct(人格注入、工具求交、
逐 worker trace 父子关联、用量收集)→ worker 账本/终态回写(尽力而为)→
协作终态回写。与 collab_gen(零 api 句柄的编排核心)分层:本模块是唯一
持 ApiClient 的组合层;授权不提权由「worker 工具 ⊆ 任务载荷授权工具」
的结构保证(见 _worker_tools)。
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any, cast

from kongming_agent.apiclient import ApiClient
from kongming_agent.collab_gen import WorkerRunner, run_collab
from kongming_agent.llm import reset_usage_collector, set_usage_collector
from kongming_agent.settings import Settings
from kongming_agent.task_gen import _react_execution_evidence, _react_verdict

log = logging.getLogger(__name__)

# react 执行器(组合根以 _run_react 装配;入参为 QueryReactIn 同构载荷)
ReactRunner = Callable[[Any], Awaitable[dict[str, object]]]
# 协作执行器(task_gen 编排循环调用;(step_id, goal, digest, cfg) → 聚合结论)
CollabRunner = Callable[[str, str, str, dict[str, object]], Awaitable[dict[str, object]]]


def _worker_tools(
    extra_tools: list[dict[str, object]], cfg: dict[str, object]
) -> list[dict[str, object]]:
    """worker 工具集(授权不提权):恒为任务载荷授权工具的子集;
    绑定 Bot 时按 Bot 工具名单求交(Bot 子集 ∩ 任务主体授权,单点收口)。
    名单为空 = 空集(与 bot 域「零工具 Bot 不可运行」同口径,不放大为不限制);
    仅未绑定 Bot(无 bot 载荷)时才继承任务全量授权工具。"""
    bot = cfg.get("bot")
    if not isinstance(bot, dict):
        return extra_tools
    raw: object = cast("dict[str, object]", bot).get("tools")
    if not isinstance(raw, list):
        # 载荷畸形(绑定了 Bot 却无名单):fail-closed 空集,不放大为全量授权
        return []
    names = {str(t) for t in cast("list[object]", raw)}
    return [t for t in extra_tools if str(t.get("name") or "") in names]


def make_collab_runner(
    *,
    api: ApiClient,
    llm: Any,  # noqa: ANN401 —— LlmClient 显式类型在调用方;剧本化测试注入假体
    settings: Settings,
    react_runner: ReactRunner,
    task_id: str,
    tenant_key: str,
    datasource_key: str,
    user_id: str | None,
    trace_root: str,
    tools: list[dict[str, object]],
) -> CollabRunner:
    """装配一次任务编排的协作步骤执行器(每任务一次;依赖显式注入)。"""

    async def _run_collab_step(
        step_id: str, step_goal: str, context: str, cfg: dict[str, object]
    ) -> dict[str, object]:
        opened = await api.collab_start(
            task_id=task_id, tenant_key=tenant_key, step_id=step_id, depth=1
        )
        # fail-closed(无游离协作面):开账被拒或 200 畸形体缺 run_id 均不执行
        run_id = str((opened or {}).get("run_id") or "")
        if opened is None or not run_id:
            return {
                "verdict": "ERROR",
                "summary": "协作开账被拒(控制面)",
                "pending_subgoals": [step_goal],
            }
        topo_raw = opened.get("topology")
        topo: dict[str, object] = dict(topo_raw) if isinstance(topo_raw, dict) else {}
        bot_raw = cfg.get("bot")
        bot: dict[str, object] = dict(bot_raw) if isinstance(bot_raw, dict) else {}
        persona = str(bot.get("system_prompt") or "")
        bot_key = str(bot.get("bot_key") or "") or None
        worker_tools = _worker_tools(tools, cfg)
        # 部署级上限(编排常量与 Settings 双源取一,api 侧已钳过一轮)
        declared = int(str(topo.get("max_workers") or 0))
        workers_cap = min(declared or settings.collab_max_workers, settings.collab_max_workers)
        counter = {"n": 0}
        # worker 用量归集(回注步级预算账;api 预算闸据此可见协作真实消耗)
        usage_totals = {"llm_calls": 0, "total_tokens": 0}

        async def _worker(subgoal: str, ctx: str) -> dict[str, object]:
            """一次受限 ReAct worker:逐 worker 独立 trace(trace_root 父子关联
            口径:-wN 后缀)+ 用量收集 + worker 账本回写(尽力而为)。"""
            counter["n"] += 1
            no = counter["n"]
            wid = f"{trace_root}-w{no}" if trace_root else f"collab-w{no}"
            await api.collab_worker_start(
                task_id=task_id,
                tenant_key=tenant_key,
                run_id=run_id,
                worker_no=no,
                subgoal=subgoal,
                bot_key=bot_key,
                trace_id=wid,
            )
            # 上下文摘要为系统提示附加段(D2);人格经 bot_system_prompt 注入
            sys_prompt = f"{persona}\n\n# 任务上下文摘要\n{ctx}" if persona or ctx else persona
            req_inner = {
                "trace_id": wid,
                "tenant_key": tenant_key,
                "datasource_key": datasource_key,
                "user_id": user_id,
                "question": subgoal,
                "history": [],
                "tools": worker_tools,
                "channel": "task",  # 审计归因任务编排(与 090 同口径)
                "bot_system_prompt": sys_prompt,
            }
            collector: list[dict[str, object]] = []
            token = set_usage_collector(collector)
            try:
                final = await react_runner(req_inner)
            finally:
                reset_usage_collector(token)
            usage: dict[str, object] = {
                "llm_calls": len(collector),
                "total_tokens": sum(
                    int(cast("int", r.get("total_tokens") or 0)) for r in collector
                ),
            }
            usage_totals["llm_calls"] += int(cast("int", usage["llm_calls"]))
            usage_totals["total_tokens"] += int(cast("int", usage["total_tokens"]))
            w_verdict = _react_verdict(final)
            if w_verdict == "PASS" and (final.get("error") or not _react_execution_evidence(final)):
                # 终态带 error(轮次触顶兜底,最终结论未产出)或无执行证据:
                # PASS 不算数(090 证据/预算退出口径,与步骤闸同源)
                w_verdict = "ERROR"
            elif w_verdict in ("", "UNKNOWN"):
                # 触顶兜底等形态的 execution 无 verdict 键:裁决缺失=未通过,
                # 不把空裁决透出账本(与 collab_gen 结论兜底同口径)
                w_verdict = "ERROR"
            summary = str(final.get("summary") or "") or str(final.get("error") or "")
            sql = str(final.get("sql") or final.get("generated_sql") or "")
            await api.collab_worker_result(
                task_id=task_id,
                tenant_key=tenant_key,
                run_id=run_id,
                worker_no=no,
                status="DONE" if w_verdict == "PASS" else "FAILED",
                conclusion={
                    "subgoal": subgoal[:2000],
                    "verdict": w_verdict,
                    "summary": summary[:2000],
                },
                evidence={"trace_id": wid, "sql": sql[:2000]},
                usage=usage,
                trace_id=wid,
            )
            # 归一化终态(编排器契约形态):react 原始终态无 verdict 键,
            # 直传会让聚合裁决恒 UNKNOWN——此处单点归一
            return {"verdict": w_verdict, "summary": summary, "sql": sql}

        runner: WorkerRunner = _worker
        # 执行预算与 api 侧 worker 序号上限同式(declared*2+1):初派 ≤ declared,
        # 余量供分歧核实/critic 修订重派——预算池若取初派数,满额拆解(=declared
        # 个子目标)下预算清零,核实/修订结构性饥饿(功能不可达,api 侧预留成空文)
        result = await run_collab(
            payload={"subgoal": step_goal, "context_summary": context},
            llm=llm,
            worker_runner=runner,
            depth=1,
            worker_budget=workers_cap * 2 + 1,
            max_workers_cap=settings.collab_max_workers,
            max_depth=settings.collab_max_depth,
            max_revision_rounds=settings.collab_max_revision_rounds,
            dispatch=str(topo.get("dispatch") or "serial"),
            critic=bool(topo.get("critic")),
        )
        result["usage"] = dict(usage_totals)
        await api.collab_finish(
            task_id=task_id,
            tenant_key=tenant_key,
            run_id=run_id,
            verdict=str(result.get("verdict") or "ERROR"),
            pending_count=len(cast("list[object]", result.get("pending_subgoals") or [])),
            dissent=bool(result.get("dissent")),
            summary=str(result.get("summary") or "")[:8000],
        )
        return result

    return _run_collab_step
