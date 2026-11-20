"""协作组合根工厂(接线):把 run_collab 编排装配进任务步骤执行。

职责:开账(fail-closed)→ worker=一次受限 ReAct(人格注入、工具求交、
逐 worker trace 父子关联、用量收集)→ worker 账本/终态回写(尽力而为)→
协作终态回写。与 collab_gen(零 api 句柄的编排核心)分层:本模块是唯一
持 ApiClient 的组合层;授权不提权由「worker 工具 ⊆ 任务载荷授权工具」
的结构保证(见 _worker_tools)。
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from typing import Any, cast

from kongming_agent.apiclient import ApiClient
from kongming_agent.collab_gen import WorkerRunner, run_collab
from kongming_agent.llm import reset_usage_collector, set_usage_collector
from kongming_agent.roundtable_gen import ROLE_CHARTERS, run_roundtable
from kongming_agent.settings import Settings
from kongming_agent.task_context import TASK_CONTEXT_TOOL_SPEC
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
    """主席判定 LLM 适配:临时挂用量收集器,把主席份额归集进协作账本——
    核心直连 llm.chat 的用量仅走平台通道,运行账少计主席。"""

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


def _as_bot(raw: object) -> dict[str, object]:
    """把载荷里的 bot 字段归一化为字典;缺失或畸形视为空。"""
    return dict(cast("dict[str, object]", raw)) if isinstance(raw, dict) else {}


def _declared_workers(cfg: dict[str, object]) -> list[dict[str, object]]:
    """取出载荷声明的名册成员,丢弃非字典条目。"""
    raw = cfg.get("workers")
    if not isinstance(raw, list):
        return []
    return [
        dict(cast("dict[str, object]", w)) for w in cast("list[object]", raw) if isinstance(w, dict)
    ]


def _trim_roster(
    declared: list[dict[str, object]], cap: int
) -> tuple[list[dict[str, object]], list[str]]:
    """按执行面部署上限截断名册,超限条目转为剔除披露文案。"""
    if len(declared) <= cap:
        return declared, []
    excluded: list[str] = []
    for idx, w in enumerate(declared[cap:], start=cap + 1):
        bot = _as_bot(w.get("bot"))
        label = str(bot.get("bot_key") or w.get("bot_key") or w.get("name") or f"#{idx}")
        excluded.append(f"{label}(超执行面部署上限)")
    return declared[:cap], excluded


def _roster_member(w: dict[str, object], tools: list[dict[str, object]]) -> dict[str, object] | str:
    """装配单个名册成员;返回字符串表示 fail-closed 剔除的成员标签。"""
    bot = _as_bot(w.get("bot"))
    declared_bot_key = str(w.get("bot_key") or "")
    identity = str(w.get("name") or "") or declared_bot_key
    if declared_bot_key and not bot:
        # 声明绑定但运行时解析失败:名册声明期锁定,收窄静默放宽即 fail-open
        return f"{declared_bot_key}(绑定解析失败)"
    w_tools = _worker_tools(tools, {"bot": bot}) if bot else list(tools)
    if not w_tools:
        # 零工具成员不派发(与 bot 域「零工具不可运行」同口径)
        return identity
    return {
        "label": identity,
        "persona": str(bot.get("system_prompt") or ""),
        "tools": w_tools,
        "bot_key": str(bot.get("bot_key") or "") or None,
    }


def _assemble_roster(
    tools: list[dict[str, object]], cfg: dict[str, object], cap: int
) -> tuple[list[dict[str, object]], dict[str, dict[str, object]], list[str]]:
    """装配名册:返回截断后的声明列表、成员映射与剔除披露。"""
    declared, excluded = _trim_roster(_declared_workers(cfg), cap)
    roster: dict[str, dict[str, object]] = {}
    for w in declared:
        member = _roster_member(w, tools)
        if isinstance(member, str):
            excluded.append(member)
        else:
            roster[str(member["label"])] = member
    return declared, roster, excluded


def _participants_fallback(topo: dict[str, object]) -> list[dict[str, object]]:
    """缺参与者载荷时按拓扑声明裸装配(声明了 bot_key 而无解析结果)。"""
    return [
        {
            "role": str(cast("dict[str, object]", p).get("role") or ""),
            "bot_key": cast("dict[str, object]", p).get("bot_key"),
            "bot": None,
        }
        for p in cast("list[object]", topo.get("participants") or [])
        if isinstance(p, dict)
    ]


def _declared_participants(
    cfg: dict[str, object], topo: dict[str, object]
) -> list[dict[str, object]]:
    """取参与者声明;载荷缺位时回落到拓扑声明。"""
    raw = cfg.get("participants")
    declared: list[dict[str, object]] = (
        [
            dict(cast("dict[str, object]", p))
            for p in cast("list[object]", raw)
            if isinstance(p, dict)
        ]
        if isinstance(raw, list)
        else []
    )
    return declared or _participants_fallback(topo)


def _trim_participants(
    declared: list[dict[str, object]], cap: int
) -> tuple[list[dict[str, object]], list[str]]:
    """按执行面部署上限截断参与者,超限条目转为剔除披露文案。"""
    if len(declared) <= cap:
        return declared, []
    excluded: list[str] = []
    for idx, p in enumerate(declared[cap:], start=cap + 1):
        bot = _as_bot(p.get("bot"))
        label = str(bot.get("bot_key") or p.get("bot_key") or p.get("role") or f"#{idx}")
        excluded.append(f"{label}(超执行面部署上限)")
    return declared[:cap], excluded


def _participant_entry(
    p: dict[str, object], idx: int, no: int, tools: list[dict[str, object]], trace_root: str
) -> dict[str, object] | str:
    """装配单个参与者;返回字符串表示 fail-closed 剔除的成员标签。"""
    bot = _as_bot(p.get("bot"))
    declared_bot_key = str(p.get("bot_key") or "")
    if declared_bot_key and not bot:
        # 声明绑定但运行时解析失败:按零工具同径剔除并披露
        return f"{declared_bot_key}(绑定解析失败)"
    p_tools = _worker_tools(tools, {"bot": bot}) if bot else list(tools)
    role = str(p.get("role") or "")
    if not p_tools:
        return str(bot.get("bot_key") or role or f"#{idx}")
    return {
        "no": no,
        "role": role,
        "label": str(bot.get("bot_key") or "") or role or f"#{no}",
        "persona": (
            str(bot.get("system_prompt") or "") or ROLE_CHARTERS.get(role, ROLE_CHARTERS["analyst"])
        ),
        "tools": p_tools,
        "bot_key": str(bot.get("bot_key") or "") or None,
        "trace": f"{trace_root}-w{no}" if trace_root else f"roundtable-w{no}",
        "usage": {"llm_calls": 0, "total_tokens": 0},
        "speeches": 0,
        "last": None,
    }


def _assemble_participants(
    tools: list[dict[str, object]],
    cfg: dict[str, object],
    topo: dict[str, object],
    settings: Settings,
    trace_root: str,
) -> tuple[list[dict[str, object]], list[str]]:
    """装配圆桌参与者:返回有效名册与剔除披露。"""
    cap = max(2, settings.collab_max_workers)
    declared, excluded = _trim_participants(_declared_participants(cfg, topo), cap)
    effective: list[dict[str, object]] = []
    for idx, p in enumerate(declared, start=1):
        entry = _participant_entry(p, idx, len(effective) + 1, tools, trace_root)
        if isinstance(entry, str):
            excluded.append(entry)
        else:
            effective.append(entry)
    return effective, excluded


def _normalize_verdict(final: dict[str, object]) -> str:
    """把 ReAct 原始终态归一为编排器契约裁决(空/UNKNOWN/无证据 PASS 均算 ERROR)。"""
    verdict = react_verdict(final)
    if verdict == "PASS" and (final.get("error") or not react_execution_evidence(final)):
        return "ERROR"
    if verdict in ("", "UNKNOWN"):
        return "ERROR"
    return verdict


def _result_summary(final: dict[str, object]) -> str:
    """取结论摘要,缺失时回落到错误文案。"""
    return str(final.get("summary") or "") or str(final.get("error") or "")


def _result_sql(final: dict[str, object]) -> str:
    """取执行 SQL(兼容 generated_sql 旧键)。"""
    return str(final.get("sql") or final.get("generated_sql") or "")


def _merge_reservation(result: dict[str, object], prefix: str) -> None:
    """把剔除披露与编排保留意见合并进结果,并随摘要同行收口。"""
    reservation = str(result.get("reservation") or "")
    result["reservation"] = f"{prefix}{reservation}".strip()[:1000] or None
    if result["reservation"]:
        result["summary"] = f"{result.get('summary')}\n#保留:{result['reservation']}"


@dataclass(frozen=True)
class _Wiring:
    """一次任务装配期固定的依赖。"""

    api: ApiClient
    llm: Any
    settings: Settings
    react_runner: ReactRunner
    task_id: str
    tenant_key: str
    datasource_key: str
    user_id: str | None
    trace_root: str
    tools: list[dict[str, object]]


@dataclass
class _Session:
    """一次协作步骤的运行期状态(worker 计数与用量归集随步骤存活)。"""

    wiring: _Wiring
    run_id: str
    topo: dict[str, object]
    persona: str
    bot_key: str | None
    worker_tools: list[dict[str, object]]
    roster_map: dict[str, dict[str, object]]
    roster_reservation: str
    workers_cap: int
    usage_totals: dict[str, int]
    counter: int = 0

    def _next_worker(self) -> tuple[int, str]:
        """分配 worker 序号与 trace id。"""
        self.counter += 1
        no = self.counter
        if self.wiring.trace_root:
            return no, f"{self.wiring.trace_root}-w{no}"
        return no, f"collab-w{no}"

    def _worker_targets(
        self, slot: str | None
    ) -> tuple[str, str | None, list[dict[str, object]], str | None]:
        """按名册槽位解析人格/工具/账本 bot_key;未命中回落顶层绑定。"""
        if slot and slot in self.roster_map:
            member = self.roster_map[slot]
            return (
                str(member["persona"]),
                str(member["bot_key"] or "") or None,
                list(cast("list[dict[str, object]]", member["tools"])),
                str(member["label"]),
            )
        return self.persona, self.bot_key, self.worker_tools, None

    async def _run_react(
        self, trace_id: str, question: str, tools: list[dict[str, object]], sys_prompt: str
    ) -> tuple[dict[str, object], dict[str, object]]:
        """执行一次受限 ReAct 并归集本轮用量。"""
        collector: list[dict[str, object]] = []
        token = set_usage_collector(collector)
        try:
            final = await self.wiring.react_runner(
                _react_request(self.wiring, trace_id, question, tools, sys_prompt)
            )
        finally:
            reset_usage_collector(token)
        usage: dict[str, object] = {
            "llm_calls": len(collector),
            "total_tokens": sum(int(cast("int", r.get("total_tokens") or 0)) for r in collector),
        }
        return final, usage

    def _accumulate_usage(
        self, worker_no: int, effective: list[dict[str, object]], usage: dict[str, object]
    ) -> None:
        """把一轮用量同时计入参与者份额与步骤总账。"""
        p = effective[worker_no - 1]
        acc = cast("dict[str, int]", p["usage"])
        llm_calls = int(cast("int", usage.get("llm_calls") or 0))
        tokens = int(cast("int", usage.get("total_tokens") or 0))
        acc["llm_calls"] += llm_calls
        acc["total_tokens"] += tokens
        self.usage_totals["llm_calls"] += llm_calls
        self.usage_totals["total_tokens"] += tokens

    def _record_speech(
        self,
        worker_no: int,
        effective: list[dict[str, object]],
        kind: str,
        content: dict[str, object],
        evidence: dict[str, object],
    ) -> None:
        """speech 轮次额外更新参与者发言账与末次发言快照。"""
        if kind != "speech":
            return
        p = effective[worker_no - 1]
        p["speeches"] = int(cast("int", p["speeches"])) + 1
        p["last"] = {
            "stance": content.get("stance"),
            "verdict": content.get("verdict"),
            "summary": content.get("summary"),
            "sql": evidence.get("sql"),
        }

    async def _run_worker_react(
        self, trace_id: str, question: str, tools: list[dict[str, object]], sys_prompt: str
    ) -> tuple[dict[str, object], dict[str, object]]:
        """执行 worker 侧受限 ReAct 并按本轮用量归集(附任务上下文工具)。"""
        return await self._run_react(
            trace_id, question, [*tools, TASK_CONTEXT_TOOL_SPEC], sys_prompt
        )

    async def worker(self, subgoal: str, ctx: str, slot: str | None = None) -> dict[str, object]:
        """一次受限 ReAct worker:逐 worker 独立 trace + 用量收集 + 账本回写(尽力而为)。"""
        (w_persona, w_bot_key, w_tools, label) = self._worker_targets(slot)
        ledger_subgoal = f"[{label}] {subgoal}" if label else subgoal
        no, wid = self._next_worker()
        # 上下文摘要作为系统提示附加段;人格经 bot_system_prompt 注入
        sys_prompt = f"{w_persona}\n\n# 任务上下文摘要\n{ctx}" if w_persona or ctx else w_persona
        await self.wiring.api.collab_worker_start(
            task_id=self.wiring.task_id,
            tenant_key=self.wiring.tenant_key,
            run_id=self.run_id,
            worker_no=no,
            subgoal=ledger_subgoal,
            bot_key=w_bot_key,
            trace_id=wid,
        )
        result = await self._run_worker_react(wid, subgoal, w_tools, sys_prompt)
        self._add_usage(result[1])
        return await self._report_worker(no, wid, ledger_subgoal, result)

    def _add_usage(self, usage: dict[str, object]) -> None:
        """把一轮用量计入步骤总账。"""
        self.usage_totals["llm_calls"] += int(cast("int", usage["llm_calls"]))
        self.usage_totals["total_tokens"] += int(cast("int", usage["total_tokens"]))

    async def _report_worker(
        self,
        no: int,
        wid: str,
        ledger_subgoal: str,
        result: tuple[dict[str, object], dict[str, object]],
    ) -> dict[str, object]:
        """回写 worker 结果并返回终态三元组(verdict/summary/sql)。"""
        final, usage = result
        verdict = _normalize_verdict(final)
        summary = _result_summary(final)
        sql = _result_sql(final)
        await self.wiring.api.collab_worker_result(
            task_id=self.wiring.task_id,
            tenant_key=self.wiring.tenant_key,
            run_id=self.run_id,
            worker_no=no,
            status="DONE" if verdict == "PASS" else "FAILED",
            conclusion={
                "subgoal": ledger_subgoal[:2000],
                "verdict": verdict,
                "summary": summary[:2000],
            },
            evidence={"trace_id": wid, "sql": sql[:2000]},
            usage=usage,
            trace_id=wid,
        )
        return {"verdict": verdict, "summary": summary, "sql": sql}

    async def participant(
        self, effective: list[dict[str, object]], step_goal: str, no: int, sys_prompt: str
    ) -> dict[str, object]:
        """一次圆桌参与者执行:受限 ReAct + 用量按参与者归集。"""
        p = effective[no - 1]
        final, usage = await self._run_react(
            str(p["trace"]),
            step_goal,
            [*cast("list[dict[str, object]]", p["tools"]), TASK_CONTEXT_TOOL_SPEC],
            sys_prompt,
        )
        return {
            "verdict": _normalize_verdict(final),
            "summary": _result_summary(final),
            "sql": _result_sql(final),
            "has_execution": bool(react_execution_evidence(final)),
            "trace_id": str(p["trace"]),
            "usage": usage,
        }

    async def record_turn(
        self,
        effective: list[dict[str, object]],
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
                self._accumulate_usage(worker_no, effective, usage)
                self._record_speech(worker_no, effective, kind, content, evidence)
            await self.wiring.api.collab_turn(
                task_id=self.wiring.task_id,
                tenant_key=self.wiring.tenant_key,
                run_id=self.run_id,
                round_no=round_no,
                worker_no=worker_no,
                kind=kind,
                # 行内携带参与者标签/角色:有效名册经剔除后压缩编号,
                # web 不得按拓扑下标反推归属
                content={
                    **content,
                    "label": str(effective[worker_no - 1]["label"]),
                    "role": str(effective[worker_no - 1]["role"]),
                }
                if worker_no >= 1
                else content,
                evidence=evidence,
                usage=usage,
                trace_id=f"{self.wiring.trace_root}-w{worker_no}" if worker_no >= 1 else "",
            )
        except Exception as e:  # noqa: BLE001
            log.warning("圆桌轮次上账异常(尽力而为): %s", e)

    def _participant_result(
        self, p: dict[str, object]
    ) -> tuple[str, dict[str, object], dict[str, object]]:
        """按参与者末次发言生成终态、结论与证据。"""
        speeches = int(cast("int", p["speeches"]))
        last = cast("dict[str, object] | None", p["last"])
        if speeches == 0 or last is None:
            evidence: dict[str, object] = {"trace_id": str(p["trace"])}
            return "SKIPPED", {"abstained": True, "stance": "弃权"}, evidence
        status = "DONE" if str(last.get("verdict") or "") == "PASS" else "FAILED"
        conclusion: dict[str, object] = {
            "abstained": False,
            "stance": last.get("stance"),
            "verdict": last.get("verdict"),
            "summary": last.get("summary"),
        }
        evidence = {
            "trace_id": str(p["trace"]),
            "sql": str(last.get("sql") or "")[:2000],
        }
        return status, conclusion, evidence

    async def _write_participant_results(self, effective: list[dict[str, object]]) -> None:
        """参与者聚合回写(尽力而为):末位发言 PASS=DONE/否则 FAILED,全程弃权=SKIPPED。"""
        for p in effective:
            status, conclusion, evidence = self._participant_result(p)
            await self.wiring.api.collab_worker_result(
                task_id=self.wiring.task_id,
                tenant_key=self.wiring.tenant_key,
                run_id=self.run_id,
                worker_no=int(cast("int", p["no"])),
                status=status,
                conclusion=conclusion,
                evidence=evidence,
                usage=cast("dict[str, object]", p["usage"]),
                trace_id=str(p["trace"]),
            )

    async def _finish(self, result: dict[str, object]) -> None:
        """回写协作终态。"""
        await self.wiring.api.collab_finish(
            task_id=self.wiring.task_id,
            tenant_key=self.wiring.tenant_key,
            run_id=self.run_id,
            verdict=str(result.get("verdict") or "ERROR"),
            pending_count=len(cast("list[object]", result.get("pending_subgoals") or [])),
            dissent=bool(result.get("dissent")),
            summary=str(result.get("summary") or "")[:8000],
        )

    async def _finish_insufficient(
        self, step_goal: str, prefix: str, worker_count: int
    ) -> dict[str, object]:
        """名册有效规模不足:不开桌(fail-closed),部分完成语义披露。"""
        result: dict[str, object] = {
            "verdict": "ERROR",
            "summary": "[]",
            "conclusions": [],
            "pending_subgoals": [step_goal],
            "dissent": True,
            "worker_count": worker_count,
            "reservation": f"{prefix}有效参与者不足,未开桌",
            "rounds_used": 0,
            "consensus": False,
        }
        await self.wiring.api.collab_finish(
            task_id=self.wiring.task_id,
            tenant_key=self.wiring.tenant_key,
            run_id=self.run_id,
            verdict="ERROR",
            pending_count=1,
            dissent=True,
            summary=str(result.get("reservation") or "")[:8000],
        )
        result["usage"] = dict(self.usage_totals)
        return result

    async def run_roundtable(
        self, step_goal: str, context: str, cfg: dict[str, object]
    ) -> dict[str, object]:
        """圆桌分支:名册装配 → 逐参与者 worker 行 → 圆桌编排 → 聚合回写 → 收口。"""
        effective, excluded = _assemble_participants(
            self.wiring.tools, cfg, self.topo, self.wiring.settings, self.wiring.trace_root
        )
        reservation_prefix = f"参与者未派发(fail-closed):{';'.join(excluded)}; " if excluded else ""
        if len(effective) < 2:
            return await self._finish_insufficient(step_goal, reservation_prefix, len(effective))
        for p in effective:
            await self.wiring.api.collab_worker_start(
                task_id=self.wiring.task_id,
                tenant_key=self.wiring.tenant_key,
                run_id=self.run_id,
                worker_no=int(cast("int", p["no"])),
                subgoal=f"圆桌参与者[{p['label']}]({p['role']})"[:2000],
                bot_key=cast("str | None", p["bot_key"]),
                trace_id=str(p["trace"]),
            )
        rounds_declared = int(str(self.topo.get("max_rounds") or 0))
        rounds_effective = min(
            rounds_declared or self.wiring.settings.collab_max_rounds,
            self.wiring.settings.collab_max_rounds,
        )
        result = await run_roundtable(
            payload={"subgoal": step_goal, "context_summary": context},
            llm=_UsageCountingLlm(self.wiring.llm, self.usage_totals),
            roster=effective,
            participant_runner=partial(self.participant, effective, step_goal),
            turn_recorder=partial(self.record_turn, effective),
            max_rounds=rounds_effective,
            execution_budget=len(effective) * rounds_effective,
        )
        await self._write_participant_results(effective)
        _merge_reservation(result, reservation_prefix)
        result["usage"] = dict(self.usage_totals)
        await self._finish(result)
        return result

    async def not_dispatched(self, step_goal: str) -> dict[str, object]:
        """名册声明存在但全员剔除:不开工(fail-closed),诚实收口披露。"""
        reservation = f"{self.roster_reservation}有效名册成员不足,未派发任何 worker".strip()
        result: dict[str, object] = {
            "verdict": "ERROR",
            "summary": "[]",
            "conclusions": [],
            "pending_subgoals": [step_goal],
            "dissent": True,
            "worker_count": 0,
            "reservation": reservation,
        }
        await self.wiring.api.collab_finish(
            task_id=self.wiring.task_id,
            tenant_key=self.wiring.tenant_key,
            run_id=self.run_id,
            verdict="ERROR",
            pending_count=1,
            dissent=True,
            summary=reservation[:8000],
        )
        result["usage"] = dict(self.usage_totals)
        return result

    async def run_serial(self, step_goal: str, context: str) -> dict[str, object]:
        """串行/并行分支:交给 run_collab 编排,收口后回写终态。"""
        runner: WorkerRunner = self.worker
        # 执行预算与 api 侧 worker 序号上限同式(declared*2+1):余量供分歧核实/critic 重派
        result = await run_collab(
            payload={"subgoal": step_goal, "context_summary": context},
            llm=self.wiring.llm,
            worker_runner=runner,
            depth=1,
            worker_budget=self.workers_cap * 2 + 1,
            max_workers_cap=self.wiring.settings.collab_max_workers,
            max_depth=self.wiring.settings.collab_max_depth,
            max_revision_rounds=self.wiring.settings.collab_max_revision_rounds,
            dispatch=str(self.topo.get("dispatch") or "serial"),
            critic=bool(self.topo.get("critic")),
            roster=list(self.roster_map.keys()) or None,
        )
        result["usage"] = dict(self.usage_totals)
        _merge_reservation(result, self.roster_reservation)
        await self._finish(result)
        return result


def _react_request(
    wiring: _Wiring,
    trace_id: str,
    question: str,
    tools: list[dict[str, object]],
    sys_prompt: str,
) -> dict[str, object]:
    """装配一次受限 ReAct 请求体(租户/会话/续读绑定取自 wiring)。"""
    return {
        "trace_id": trace_id,
        "tenant_key": wiring.tenant_key,
        "datasource_key": wiring.datasource_key,
        "user_id": wiring.user_id,
        "question": question,
        "history": [],
        "tools": tools,
        "channel": "task",  # 将工具调用归因到任务编排。
        "bot_system_prompt": sys_prompt,
        "task_id": wiring.task_id,  # 续读绑定(worker 取自身任务上下文)
    }


def _workers_cap(topo: dict[str, object], settings_cap: int) -> int:
    """协作 worker 上限:拓扑下发值与部署设置取小(下发缺省/为 0 时回落设置值)。"""
    topology_cap = int(str(topo.get("max_workers") or 0))
    return min(topology_cap or settings_cap, settings_cap)


def _make_session(
    wiring: _Wiring,
    run_id: str,
    topo: dict[str, object],
    roster_map: dict[str, dict[str, object]],
    excluded: list[str],
    cfg: dict[str, object],
) -> _Session:
    """按开账拓扑与名册装配一次协作会话(名册剔除项转保留声明文案)。"""
    bot = _as_bot(cfg.get("bot"))
    roster_reservation = f"名册成员未派发(fail-closed):{';'.join(excluded)}; " if excluded else ""
    # 部署级上限(编排常量与 Settings 双源取一,api 侧已钳过一轮)
    workers_cap = _workers_cap(topo, wiring.settings.collab_max_workers)
    return _Session(
        wiring=wiring,
        run_id=run_id,
        topo=topo,
        persona=str(bot.get("system_prompt") or ""),
        bot_key=str(bot.get("bot_key") or "") or None,
        worker_tools=_worker_tools(wiring.tools, cfg),
        roster_map=roster_map,
        roster_reservation=roster_reservation,
        workers_cap=workers_cap,
        usage_totals={"llm_calls": 0, "total_tokens": 0},
    )


async def _run_step(
    wiring: _Wiring, step_id: str, step_goal: str, context: str, cfg: dict[str, object]
) -> dict[str, object]:
    """执行一次协作步骤:开账(fail-closed)→ 名册装配 → 分支编排 → 收口。"""
    opened = await wiring.api.collab_start(
        task_id=wiring.task_id, tenant_key=wiring.tenant_key, step_id=step_id, depth=1
    )
    run_id = str((opened or {}).get("run_id") or "")
    if opened is None or not run_id:
        # fail-closed(无游离协作面):开账被拒或 200 畸形体缺 run_id 均不执行
        return {
            "verdict": "ERROR",
            "summary": "协作开账被拒(控制面)",
            "pending_subgoals": [step_goal],
        }
    topo_raw = opened.get("topology")
    topo: dict[str, object] = (
        dict(cast("dict[str, object]", topo_raw)) if isinstance(topo_raw, dict) else {}
    )
    declared, roster_map, excluded = _assemble_roster(
        wiring.tools, cfg, max(1, wiring.settings.collab_max_workers)
    )
    session = _make_session(wiring, run_id, topo, roster_map, excluded, cfg)
    if str(topo.get("kind") or "decomposition") == "roundtable":
        return await session.run_roundtable(step_goal, context, cfg)
    if declared and not roster_map:
        return await session.not_dispatched(step_goal)
    return await session.run_serial(step_goal, context)


def make_collab_runner(
    *,
    api: ApiClient,
    llm: Any,
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
    wiring = _Wiring(
        api=api,
        llm=llm,
        settings=settings,
        react_runner=react_runner,
        task_id=task_id,
        tenant_key=tenant_key,
        datasource_key=datasource_key,
        user_id=user_id,
        trace_root=trace_root,
        tools=tools,
    )

    async def runner(
        step_id: str, step_goal: str, context: str, cfg: dict[str, object]
    ) -> dict[str, object]:
        return await _run_step(wiring, step_id, step_goal, context, cfg)

    return runner
