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
from kongming_agent.roundtable_gen import ROLE_CHARTERS, run_roundtable
from kongming_agent.settings import Settings
from kongming_agent.task_gen import react_execution_evidence, react_verdict

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


class _UsageCountingLlm:
    """主席判定 LLM 适配(细节-2):临时挂用量收集器,把主席份额归集进协作
    账本——核心直连 llm.chat 的用量此前仅走 021 平台通道,运行账少计主席。"""

    def __init__(self, inner: Any, totals: dict[str, int]) -> None:
        self._inner = inner
        self._totals = totals

    async def chat(self, system: str, user: str, **kw: object) -> str:
        collector: list[dict[str, object]] = []
        token = set_usage_collector(collector)
        try:
            reply = await self._inner.chat(system, user, **kw)
            return str(reply)
        finally:
            reset_usage_collector(token)
            self._totals["llm_calls"] += len(collector)
            self._totals["total_tokens"] += sum(
                int(cast("int", r.get("total_tokens") or 0)) for r in collector
            )


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
        topo: dict[str, object] = (
            dict(cast("dict[str, object]", topo_raw)) if isinstance(topo_raw, dict) else {}
        )
        bot_raw = cfg.get("bot")
        bot: dict[str, object] = (
            dict(cast("dict[str, object]", bot_raw)) if isinstance(bot_raw, dict) else {}
        )
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
            w_verdict = react_verdict(final)
            if w_verdict == "PASS" and (final.get("error") or not react_execution_evidence(final)):
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

        async def _run_roundtable() -> dict[str, object]:
            """圆桌分支(115):名册装配(Bot 人格/内置章程、逐参与者工具求交、
            零工具/超执行面上限 fail-closed 剔除)→ 逐参与者 worker 行 → 圆桌编排核心
            (轮次/黑板/主席判定)→ 逐 turn 上账(尽力而为)→ 参与者聚合
            回写 → 运行收口。参与者名册运行期锁定,执行预算 = 名册 × 轮次。"""

            async def _recorder(
                round_no: int,
                worker_no: int,
                kind: str,
                content: dict[str, object],
                evidence: dict[str, object],
                usage: dict[str, object],
            ) -> None:
                """轮次落账(尽力而为)+ 参与者维度聚合(用量/发言账)。"""
                try:
                    if worker_no >= 1:
                        p = effective[worker_no - 1]
                        acc = cast("dict[str, int]", p["usage"])
                        acc["llm_calls"] += int(cast("int", usage.get("llm_calls") or 0))
                        acc["total_tokens"] += int(cast("int", usage.get("total_tokens") or 0))
                        usage_totals["llm_calls"] += int(cast("int", usage.get("llm_calls") or 0))
                        usage_totals["total_tokens"] += int(
                            cast("int", usage.get("total_tokens") or 0)
                        )
                        if kind == "speech":
                            p["speeches"] = int(cast("int", p["speeches"])) + 1
                            p["last"] = {
                                "stance": content.get("stance"),
                                "verdict": content.get("verdict"),
                                "summary": content.get("summary"),
                                "sql": evidence.get("sql"),
                            }
                    await api.collab_turn(
                        task_id=task_id,
                        tenant_key=tenant_key,
                        run_id=run_id,
                        round_no=round_no,
                        worker_no=worker_no,
                        kind=kind,
                        # 行内携带参与者标签/角色(P2-1):有效名册经剔除后压缩
                        # 编号,web 不得按拓扑下标反推归属
                        content={
                            **content,
                            "label": str(effective[worker_no - 1]["label"]),
                            "role": str(effective[worker_no - 1]["role"]),
                        }
                        if worker_no >= 1
                        else content,
                        evidence=evidence,
                        usage=usage,
                        trace_id=f"{trace_root}-w{worker_no}" if worker_no >= 1 else "",
                    )
                except Exception as e:  # noqa: BLE001 —— 账本尽力而为,不阻断圆桌
                    log.warning("圆桌轮次上账异常(尽力而为): %s", e)

            async def _participant(no: int, sys_prompt: str) -> dict[str, object]:
                """一次圆桌参与者执行:一次受限 ReAct(人格/章程 + 黑板快照经
                系统提示注入;工具 = 任务授权 ∩ Bot 名单),用量按参与者归集。"""
                p = effective[no - 1]
                req_inner: dict[str, object] = {
                    "trace_id": str(p["trace"]),
                    "tenant_key": tenant_key,
                    "datasource_key": datasource_key,
                    "user_id": user_id,
                    "question": step_goal,
                    "history": [],
                    "tools": p["tools"],
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
                v = react_verdict(final)
                if v == "PASS" and (final.get("error") or not react_execution_evidence(final)):
                    # 终态带 error 或无执行证据:PASS 不算数(090 证据闸同源)
                    v = "ERROR"
                elif v in ("", "UNKNOWN"):
                    v = "ERROR"
                summary = str(final.get("summary") or "") or str(final.get("error") or "")
                sql = str(final.get("sql") or final.get("generated_sql") or "")
                return {
                    "verdict": v,
                    "summary": summary,
                    "sql": sql,
                    "has_execution": bool(react_execution_evidence(final)),
                    "trace_id": str(p["trace"]),
                    "usage": usage,
                }

            # 名册装配(cfg.participants = api 决策载荷的逐参与者 Bot 运行时
            # 解析结果;缺载荷时按拓扑声明裸装配,声明了 bot_key 而无解析结果的
            # 条目 fail-closed 剔除——声明收窄不因载荷缺位静默蒸发)
            raw_parts = cfg.get("participants")
            declared_parts: list[dict[str, object]] = (
                [
                    dict(cast("dict[str, object]", p))
                    for p in cast("list[object]", raw_parts)
                    if isinstance(p, dict)
                ]
                if isinstance(raw_parts, list)
                else []
            )
            if not declared_parts:
                declared_parts = [
                    {
                        "role": str(cast("dict[str, object]", p).get("role") or ""),
                        "bot_key": cast("dict[str, object]", p).get("bot_key"),
                        "bot": None,
                    }
                    for p in cast("list[object]", topo.get("participants") or [])
                    if isinstance(p, dict)
                ]
            effective: list[dict[str, object]] = []
            excluded: list[str] = []
            # 执行面部署上限双钳(与轮次钳制同式,api 声明钳制为先;两进程
            # COLLAB_MAX_WORKERS 漂移时执行面不被穿透):超限条目走既有
            # fail-closed 剔除通道如实披露,不静默截断(剔除必披露口径)
            roster_cap = max(2, settings.collab_max_workers)
            if len(declared_parts) > roster_cap:
                for idx, p in enumerate(declared_parts[roster_cap:], start=roster_cap + 1):
                    bot_p = p.get("bot")
                    bot_dict = (
                        dict(cast("dict[str, object]", bot_p)) if isinstance(bot_p, dict) else {}
                    )
                    excluded.append(
                        str(
                            bot_dict.get("bot_key")
                            or p.get("bot_key")
                            or p.get("role")
                            or f"#{idx}"
                        )
                        + "(超执行面部署上限)"
                    )
                declared_parts = declared_parts[:roster_cap]
            for idx, p in enumerate(declared_parts, start=1):
                bot_p = p.get("bot")
                bot_dict = dict(cast("dict[str, object]", bot_p)) if isinstance(bot_p, dict) else {}
                declared_bot_key = str(p.get("bot_key") or "")
                if declared_bot_key and not bot_dict:
                    # 声明绑定但运行时解析失败(Bot 已删/停用/创建者不可用):
                    # 圆桌名册声明期锁定,收窄静默放宽即 fail-open——按零工具
                    # 同径剔除并披露(人格侧降级口径不适用于授权侧)
                    excluded.append(f"{declared_bot_key}(绑定解析失败)")
                    continue
                p_tools = _worker_tools(tools, {"bot": bot_dict}) if bot_dict else list(tools)
                role = str(p.get("role") or "")
                if not p_tools:
                    # 零工具参与者 fail-closed 不派发(与 bot 域「零工具不可运行」
                    # 同口径:名单空集不放大为不限制)
                    excluded.append(str(bot_dict.get("bot_key") or role or f"#{idx}"))
                    continue
                no = len(effective) + 1
                effective.append(
                    {
                        "no": no,
                        "role": role,
                        "label": str(bot_dict.get("bot_key") or "") or role or f"#{no}",
                        "persona": (
                            str(bot_dict.get("system_prompt") or "")
                            or ROLE_CHARTERS.get(role, ROLE_CHARTERS["analyst"])
                        ),
                        "tools": p_tools,
                        "bot_key": str(bot_dict.get("bot_key") or "") or None,
                        "trace": f"{trace_root}-w{no}" if trace_root else f"roundtable-w{no}",
                        "usage": {"llm_calls": 0, "total_tokens": 0},
                        "speeches": 0,
                        "last": None,
                    }
                )
            reservation_prefix = (
                f"参与者未派发(fail-closed):{';'.join(excluded)}; " if excluded else ""
            )
            if len(effective) < 2:
                # 名册有效规模不足:不开桌(fail-closed),部分完成语义披露
                result: dict[str, object] = {
                    "verdict": "ERROR",
                    "summary": "[]",
                    "conclusions": [],
                    "pending_subgoals": [step_goal],
                    "dissent": True,
                    "worker_count": len(effective),
                    "reservation": f"{reservation_prefix}有效参与者不足,未开桌",
                    "rounds_used": 0,
                    "consensus": False,
                }
                await api.collab_finish(
                    task_id=task_id,
                    tenant_key=tenant_key,
                    run_id=run_id,
                    verdict="ERROR",
                    pending_count=1,
                    dissent=True,
                    summary=str(result.get("reservation") or "")[:8000],
                )
                result["usage"] = dict(usage_totals)
                return result
            for p in effective:
                await api.collab_worker_start(
                    task_id=task_id,
                    tenant_key=tenant_key,
                    run_id=run_id,
                    worker_no=int(cast("int", p["no"])),
                    subgoal=f"圆桌参与者[{p['label']}]({p['role']})"[:2000],
                    bot_key=cast("str | None", p["bot_key"]),
                    trace_id=str(p["trace"]),
                )
            rounds_declared = int(str(topo.get("max_rounds") or 0))
            rounds_effective = min(
                rounds_declared or settings.collab_max_rounds, settings.collab_max_rounds
            )
            result = await run_roundtable(
                payload={"subgoal": step_goal, "context_summary": context},
                llm=_UsageCountingLlm(llm, usage_totals),
                roster=effective,
                participant_runner=_participant,
                turn_recorder=_recorder,
                max_rounds=rounds_effective,
                execution_budget=len(effective) * rounds_effective,
            )
            # 参与者聚合回写(尽力而为):末位发言 PASS=DONE/否则 FAILED;
            # 全程弃权=SKIPPED(诚实弃权非失败)
            for p in effective:
                speeches = int(cast("int", p["speeches"]))
                last = cast("dict[str, object] | None", p["last"])
                conclusion: dict[str, object]
                evidence_p: dict[str, object]
                if speeches == 0 or last is None:
                    status = "SKIPPED"
                    conclusion = {"abstained": True, "stance": "弃权"}
                    evidence_p = {"trace_id": str(p["trace"])}
                else:
                    status = "DONE" if str(last.get("verdict") or "") == "PASS" else "FAILED"
                    conclusion = {
                        "abstained": False,
                        "stance": last.get("stance"),
                        "verdict": last.get("verdict"),
                        "summary": last.get("summary"),
                    }
                    evidence_p = {
                        "trace_id": str(p["trace"]),
                        "sql": str(last.get("sql") or "")[:2000],
                    }
                await api.collab_worker_result(
                    task_id=task_id,
                    tenant_key=tenant_key,
                    run_id=run_id,
                    worker_no=int(cast("int", p["no"])),
                    status=status,
                    conclusion=conclusion,
                    evidence=evidence_p,
                    usage=cast("dict[str, object]", p["usage"]),
                    trace_id=str(p["trace"]),
                )
            reservation = str(result.get("reservation") or "")
            result["reservation"] = f"{reservation_prefix}{reservation}".strip()[:1000] or None
            if result["reservation"]:
                # 保留意见随摘要同行收口:PASS 路径的触顶/截断语境不许无痕。
                # 可见性边界:运行账摘要(8k)可靠;步骤证据(2k)/事件(500)
                # 面受截断长度限制,长结论下后缀可能不完整
                result["summary"] = f"{result.get('summary')}\n#保留:{result['reservation']}"
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

        if str(topo.get("kind") or "decomposition") == "roundtable":
            return await _run_roundtable()

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
