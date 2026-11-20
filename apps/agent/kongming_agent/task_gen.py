"""任务编排：先规划步骤，再逐项执行并回写结果。

每步的 ReAct 调用有轮次上限，任务状态由控制面保存；恢复时继续使用既有回调。
"""

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, cast

from kongming_agent import task_prompts as tp
from kongming_agent.apiclient import ApiClient
from kongming_agent.graph.checkpoints import thread_id_for_task
from kongming_agent.llm import LlmError, reset_usage_collector, set_usage_collector
from kongming_agent.settings import Settings

log = logging.getLogger(__name__)

_MAX_STEPS = 12
# 任务级累计 token 硬顶(失控兜底;与控制面 max_tokens 预算双保险)
_MAX_TOTAL_TOKENS = 100_000

# 步骤执行器:单步有界 ReAct(问题→终态 dict);由组合根以既有内核装配
StepRunner = Callable[[str, dict[str, object]], Awaitable[dict[str, object]]]
# 协作执行器:声明协作拓扑的步骤改走协作编排(组合根装配 run_collab);
# 签名 (step_id, step_goal, context_digest, collab_cfg) → 聚合结论 dict
CollabRunner = Callable[[str, str, str, dict[str, object]], Awaitable[dict[str, object]]]


def _extract_steps(raw: str) -> list[dict[str, object]]:
    """规划输出 → 步骤清单(剥围栏 + 首尾括号;畸形拒绝)。"""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3]
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        raise ValueError("plan 输出无数组")
    obj = json.loads(text[start : end + 1])
    if not isinstance(obj, list):
        raise TypeError("plan 输出非数组")
    out: list[dict[str, object]] = []
    for item in cast("list[object]", obj[:_MAX_STEPS]):
        itemd = cast("dict[str, object] | None", item if isinstance(item, dict) else None)
        if itemd is None:
            continue
        goal = str(itemd.get("goal") or "").strip()
        if not goal:
            continue
        # 控制面单步目标上限 2000(PlanStepIn):LLM 复述长目标时静默截断,
        # 否则计划回写 422 → 任务确定性 FAILED(不可自愈)
        out.append({"goal": goal[:2000], "require_confirm": bool(itemd.get("require_confirm"))})
    return out  # 空清单交由控制面语义处置(计划回写被拒/续跑无账 → FAILED,不假 DONE)


@dataclass
class _StepExecution:
    """单步执行结果:终态载荷、本步用量与是否协作分支。"""

    final: dict[str, object]
    tokens: int
    is_collab: bool


@dataclass
class _RunLog:
    """任务循环账本:完成/未完成清单与累计用量。"""

    not_done: list[str] = field(default_factory=list[str])
    completed: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])
    tokens_total: int = 0


def _collab_tokens(final: dict[str, object]) -> int:
    """协作步骤的 worker 用量回注步级预算。"""
    usage = final.get("usage")
    if not isinstance(usage, dict):
        return 0
    return int(cast("int", cast("dict[str, object]", usage).get("total_tokens") or 0))


class TaskOrchestrator:
    """一次任务执行的编排器(每请求实例化;依赖显式注入便于剧本化测试)。"""

    def __init__(
        self,
        *,
        payload: dict[str, object],
        llm: Any,
        api: ApiClient,
        settings: Settings,
        step_runner: StepRunner,
        collab_runner: CollabRunner | None = None,
    ) -> None:
        self._payload = payload
        self._llm = llm
        self._api = api
        self._settings = settings
        self._run_step = step_runner
        self._run_collab = collab_runner
        self._task_id = str(payload.get("task_id") or "")
        self._epoch = int(cast("int", payload.get("epoch") or 0))  # 派发代际(stale_epoch 判定依据)
        self._tenant_key = str(payload.get("tenant_key") or "default")

    async def run(self) -> dict[str, object]:
        try:
            return await self._run_inner()
        except LlmError as e:
            log.warning("任务编排 LLM 异常: %s", e)
            await self._finish("FAILED", f"任务编排 LLM 异常: {e}")
            return {"final_status": "FAILED", "error": str(e)}
        except ValueError as e:
            # 规划输出畸形(非 JSON/无数组):确定性失败如实收口,不裸 500
            log.warning("任务规划输出畸形: %s", e)
            await self._finish("FAILED", f"规划输出畸形: {e}")
            return {"final_status": "FAILED", "error": str(e)}

    async def _prepare_steps(
        self, goal: str, datasource_key: str, trace_root: str
    ) -> tuple[list[dict[str, object]], dict[str, object] | None]:
        """规划并落账步骤;返回 (步骤清单, 提前终态结果)。"""
        # 规划段 LLM 用量走 fire-and-forget 通道,不计入任务预算账本
        plan_raw = await self._llm.chat(
            tp.PLAN_SYSTEM_PROMPT,
            tp.PLAN_USER_TEMPLATE.format(goal=goal, datasource_key=datasource_key),
            temperature=0.2,
            trace_id=trace_root,
            tenant_key=self._tenant_key,
        )
        try:
            steps = _extract_steps(plan_raw)
        except (ValueError, TypeError) as e:
            return [], await self._finish("FAILED", f"规划输出畸形: {e}")
        created = await self._api.task_plan(
            task_id=self._task_id, tenant_key=self._tenant_key, steps=steps
        )
        if created is None:
            return [], await self._finish("FAILED", "计划回写被拒")
        if created:
            return created, None
        # 续跑(计划已在账):读既有步骤;读不到 = 规划期中断/回调失败,
        # fail-closed 如实 FAILED(空清单跑 DONE 是假完成)
        existing = await self._api.task_steps(task_id=self._task_id, tenant_key=self._tenant_key)
        if not existing:
            return [], await self._finish("FAILED", "续跑无已落账步骤(规划期中断),任务失败")
        return [
            {
                "id": s["id"],
                "goal": s["goal"],
                "require_confirm": s["require_confirm"],
                # 账本状态随行:skip 决策区分「已 DONE 正常推进」与「真跳过」
                "status": s.get("status") or "",
            }
            for s in existing
        ], None

    def _decision_exit(
        self, action: str, decision: dict[str, object] | None
    ) -> dict[str, object] | None:
        """控制面决策的提前退出通道(pause/await_approval/abort)。"""
        if action == "pause":
            return {"final_status": "PAUSED", "reason": str((decision or {}).get("reason"))}
        if action == "await_approval":
            return {"final_status": "AWAITING_APPROVAL"}
        if action == "abort":
            # 控制面判定任务不可再执行(用户取消/终态):如实返回现态,
            # 不再回写终态(终态归属控制面)
            return {
                "final_status": str((decision or {}).get("task_status") or "FAILED"),
                "reason": str((decision or {}).get("reason") or ""),
            }
        return None

    async def _begin_step(
        self, step: dict[str, object]
    ) -> tuple[str, str, dict[str, object] | None]:
        """拉取控制面步骤决策,返回 (步骤 id, 目标文案, 决策)。"""
        step_id = str(step.get("id") or "")
        step_goal = str(step.get("goal") or "")
        decision = await self._api.task_step_start(
            task_id=self._task_id,
            tenant_key=self._tenant_key,
            step_id=step_id,
            epoch=self._epoch,
        )
        return step_id, step_goal, decision

    async def _run_one_step(
        self,
        step: dict[str, object],
        step_id: str,
        step_goal: str,
        goal: str,
        completed: list[tuple[str, str]],
        tools: list[dict[str, object]],
        datasource_key: str,
        user_id: str | None,
        trace_root: str,
        decision: dict[str, object] | None,
    ) -> tuple[dict[str, object] | None, _StepExecution | None]:
        """执行单个步骤;返回 (提前终态结果, 步骤执行记录)。"""
        is_collab = (
            isinstance((decision or {}).get("collab"), dict) and self._run_collab is not None
        )
        # 每步只上报新增用量,由控制面负责累加。
        final, step_tokens = await self._run_step_branch(
            step,
            step_id,
            step_goal,
            _context_digest(goal, completed),
            is_collab,
            self._step_context(tools, datasource_key, user_id, step_id, trace_root, decision),
            (decision or {}).get("collab"),
        )
        # 图内挂起:到达确认点图已挂(检查点在控制面),
        # 上账(建/复用确认行+任务挂起)后如实返回,终态归属控制面审批流
        suspended = final.get("suspended")
        if isinstance(suspended, dict):
            return await self._suspend_step(step_id, cast("dict[str, object]", suspended)), None
        return None, _StepExecution(final=final, tokens=step_tokens, is_collab=is_collab)

    async def _run_step_branch(
        self,
        step: dict[str, object],
        step_id: str,
        step_goal: str,
        digest: str,
        is_collab: bool,
        context: dict[str, object],
        collab_cfg: object,
    ) -> tuple[dict[str, object], int]:
        """按是否声明协作拓扑择路执行单步,返回终态与本次新增用量。"""
        collector: list[dict[str, object]] = []
        token = set_usage_collector(collector)
        try:
            if is_collab:
                # 声明协作拓扑的步骤走协作编排(聚合结论驱动步骤终态)
                final = await cast("CollabRunner", self._run_collab)(
                    step_id, step_goal, digest, cast("dict[str, object]", collab_cfg)
                )
            else:
                final = await self._run_step(str(step.get("goal") or ""), context)
        finally:
            reset_usage_collector(token)
        step_tokens = sum(int(cast("int", r.get("total_tokens") or 0)) for r in collector)
        if is_collab:
            step_tokens += _collab_tokens(final)
        return final, step_tokens

    def _step_context(
        self,
        tools: list[dict[str, object]],
        datasource_key: str,
        user_id: str | None,
        step_id: str,
        trace_root: str,
        decision: dict[str, object] | None,
    ) -> dict[str, object]:
        """装配单步执行的图上下文(tools 与检查点/续跑归属字段)。"""
        return {
            "tools": tools,
            "datasource_key": datasource_key,
            "user_id": user_id,
            # 图级检查点上下文:线程派生与内部回调归属;
            # 恢复提示=已决确认随 begin_step 下发
            "task_id": self._task_id,
            "step_id": step_id,
            "tenant_key": self._tenant_key,
            "trace_id": trace_root,
            "resume_confirm": (decision or {}).get("resume_confirm"),
        }

    async def _suspend_step(self, step_id: str, suspended: dict[str, object]) -> dict[str, object]:
        """图内挂起上账;控制面拒绝(返回 None)时收敛为 FAILED 终态。"""
        rep = await self._api.task_step_suspended(
            task_id=self._task_id,
            tenant_key=self._tenant_key,
            step_id=step_id,
            epoch=self._epoch,
            confirm_id=str(suspended.get("confirm_id") or ""),
            tool_name=str(suspended.get("tool_name") or ""),
            summary=str(suspended.get("summary") or ""),
            thread_id=thread_id_for_task(self._task_id, step_id),
        )
        if rep is None:
            return await self._finish("FAILED", "图内挂起上账被拒(链路异常)")
        return {"final_status": "AWAITING_APPROVAL"}

    def _record_step(
        self, step_goal: str, run: _StepExecution, log: _RunLog, trace_root: str
    ) -> tuple[str, dict[str, object], str | None]:
        """累计用量并归入完成/未完成账本;返回步骤终态三元组。"""
        log.tokens_total += run.tokens
        if run.is_collab:
            status, evidence, fail_reason = _collab_outcome(run.final, trace_root)
        else:
            status, evidence, fail_reason = _step_outcome(run.final, trace_root)
        if log.tokens_total >= _MAX_TOTAL_TOKENS:
            status, fail_reason = "FAILED", "任务累计 token 预算耗尽"
        if status != "DONE":
            log.not_done.append(f"{step_goal[:80]}:{fail_reason or status}")
        else:
            log.completed.append((step_goal, str(run.final.get("summary") or "")[:400]))
        return status, evidence, fail_reason

    async def _submit_step(
        self,
        step_id: str,
        run: _StepExecution,
        status: str,
        evidence: dict[str, object],
        fail_reason: str | None,
    ) -> dict[str, object] | None:
        """回写步骤结果;返回提前终态结果(None = 继续)。"""
        result = await self._api.task_step_result(
            task_id=self._task_id,
            tenant_key=self._tenant_key,
            step_id=step_id,
            epoch=self._epoch,
            status=status,
            summary=str(run.final.get("summary") or run.final.get("final_answer") or ""),
            evidence=evidence,
            fail_reason=fail_reason,
            checkpoint={
                "summary": str(run.final.get("summary") or "")[:2000],
                "resume_origin": str(run.final.get("resume_origin") or "fresh"),
            },
            tokens_used=run.tokens,
        )
        if result is None:
            return await self._finish("FAILED", "步骤结果回写被拒")
        decision = str(result.get("decision") or "")
        if decision == "stale_epoch":
            # 派发代际失配:本循环已被新派发取代,静默退出(不收口不重试;
            # 在途步已由新派发回队重跑,终态归属在位执行者)
            return {"final_status": "STALE", "reason": "派发代际失配退出"}
        if decision.startswith("reject:"):
            return await self._finish("FAILED", decision)
        return None

    async def _process_step(
        self,
        step: dict[str, object],
        log: _RunLog,
        goal: str,
        tools: list[dict[str, object]],
        datasource_key: str,
        user_id: str | None,
        trace_root: str,
    ) -> dict[str, object] | None:
        """处理单个步骤的拉取式决策、执行与回写;返回提前终态结果。"""
        step_id, step_goal, decision = await self._begin_step(step)
        action = str((decision or {}).get("decision") or "skip")
        early = self._decision_exit(action, decision)
        if early is not None:
            return early
        if action != "execute":
            # skip:控制面判定不可执行。账本已 DONE 的跳过=正常推进;
            # 其余(审批拒绝 SKIPPED/状态漂移)如实计入未完成
            if str(step.get("status") or "") != "DONE":
                log.not_done.append(f"{step_goal[:80]}:控制面跳过")
            return None
        early, run = await self._run_one_step(
            step,
            step_id,
            step_goal,
            goal,
            log.completed,
            tools,
            datasource_key,
            user_id,
            trace_root,
            decision,
        )
        if early is not None or run is None:
            return early
        return await self._finish_step(step_id, step_goal, run, log, trace_root)

    async def _finish_step(
        self,
        step_id: str,
        step_goal: str,
        run: _StepExecution,
        log: _RunLog,
        trace_root: str,
    ) -> dict[str, object] | None:
        """记录步骤产出并回写控制面(返回提前终态结果)。"""
        status, evidence, fail_reason = self._record_step(step_goal, run, log, trace_root)
        return await self._submit_step(step_id, run, status, evidence, fail_reason)

    async def _execute_steps(
        self,
        steps: list[dict[str, object]],
        goal: str,
        tools: list[dict[str, object]],
        datasource_key: str,
        user_id: str | None,
        trace_root: str,
    ) -> dict[str, object]:
        """逐步执行(拉取式:控制面每步下发决策),全部 DONE 方可 DONE。"""
        log = _RunLog()
        for step in steps:
            early = await self._process_step(
                step, log, goal, tools, datasource_key, user_id, trace_root
            )
            if early is not None:
                return early
        # spec:存在非 DONE 步骤 SHALL NOT 全称完成,收口逐项如实说明
        if log.not_done:
            return await self._finish("FAILED", "部分步骤未完成:" + ";".join(log.not_done)[:500])
        return await self._finish("DONE", None)

    async def _run_inner(self) -> dict[str, object]:
        goal = str(self._payload.get("goal") or "")
        trace_root = str(self._payload.get("trace_root") or "")
        tools = cast("list[dict[str, object]]", self._payload.get("tools") or [])
        datasource_key = str(self._payload.get("datasource_key") or "default")
        user_id = cast("str | None", self._payload.get("user_id"))
        steps, early = await self._prepare_steps(goal, datasource_key, trace_root)
        if early is not None:
            return early
        return await self._execute_steps(steps, goal, tools, datasource_key, user_id, trace_root)

    async def _finish(self, status: str, reason: str | None) -> dict[str, object]:
        ok = await self._api.task_finish(
            task_id=self._task_id,
            tenant_key=self._tenant_key,
            status=status,
            fail_reason=reason,
            epoch=self._epoch,
        )
        if not ok:
            # 控制面拒绝(状态冲突/已被推进):不得宣称完成,如实上报未确认
            log.warning("任务终态回写被拒: task_id=%s status=%s", self._task_id, status)
            return {"final_status": "FAILED", "error": f"终态回写未确认(控制面拒绝): {status}"}
        return {"final_status": status, "error": reason}


def react_verdict(final: dict[str, object]) -> str:
    """提取 ReAct 终态 verdict。测试替身可直接提供 verdict；真实调用从 execution 结果读取。"""
    v = final.get("verdict")
    if isinstance(v, str) and v:
        return v
    execution = final.get("execution")
    if isinstance(execution, dict):
        return str(cast("dict[str, object]", execution).get("verdict") or "")
    return str(getattr(execution, "verdict", "") or "")


def react_execution_evidence(final: dict[str, object]) -> bool:
    """工具执行证据判定(证据口径,单一定义):真实 react 终态的 execution
    载荷须带非空结果集(列或行)方构成完成证据——finalize 对纯文本直答会合成
    空结果集 PASS(graph/react/nodes 兜底),语言模型自述不算数。剧本化假体
    无 execution 键时不受此闸(verdict 即剧本结论)。"""
    execution = final.get("execution")
    if isinstance(execution, dict):
        execd = cast("dict[str, object]", execution)
        return bool(execd.get("columns") or execd.get("rows"))
    if execution is not None:
        result = cast("Any", execution)
        return bool(result.columns or result.rows)
    return True


def _step_outcome(
    final: dict[str, object], trace_root: str
) -> tuple[str, dict[str, object], str | None]:
    """react 终态 → 步骤三态(证据驱动):verdict PASS 且有执行结果 → DONE
    (证据=trace+summary+sql);其余 FAILED(原因如实)。无工具执行→FAILED
    (有证据才完成;规划应产出工具可支撑的步骤)。"""
    verdict = react_verdict(final)
    summary = str(final.get("summary") or "")
    sql = str(final.get("sql") or final.get("generated_sql") or "")
    evidence: dict[str, object] = {"trace_id": trace_root, "summary": summary[:2000]}
    if sql:
        evidence["sql"] = sql[:2000]
    # 终态带 error = 轮次触顶兜底(finalize 唯一设 error 键的路径):即便途中
    # 有过真实结果集,最终结论从未产出——kernel 预算退出语义「不得批量改 DONE」
    error = str(final.get("error") or "")
    if verdict == "PASS" and not error:
        if not react_execution_evidence(final):
            # finalize 对纯文本直答合成空结果集 PASS:自述不构成完成证据
            return "FAILED", evidence, "步骤无工具执行证据(纯文本直答不构成完成)"
        return "DONE", evidence, None
    fail = error or summary or verdict or "步骤未产出工具执行证据"
    return "FAILED", evidence, fail[:500]


def _collab_outcome(
    final: dict[str, object], trace_root: str
) -> tuple[str, dict[str, object], str | None]:
    """协作聚合结论 → 步骤三态(步骤终态由聚合结论驱动):
    verdict PASS → DONE(证据=trace+聚合结论+协作语义披露);其余 FAILED
    (原因含未派发清单/保留意见/分歧态,诚实口径)。"""
    verdict = str(final.get("verdict") or "")
    summary = str(final.get("summary") or "")
    pending = cast("list[str]", final.get("pending_subgoals") or [])
    reservation = str(final.get("reservation") or "")
    evidence: dict[str, object] = {
        "trace_id": trace_root,
        "summary": summary[:2000],
        "collab": {
            "worker_count": int(cast("int", final.get("worker_count") or 0)),
            "dissent": bool(final.get("dissent")),
            "pending": [str(p) for p in pending][:10],
        },
    }
    if verdict == "PASS":
        return "DONE", evidence, None
    reasons = [f"协作裁决 {verdict}"]
    if pending:
        reasons.append("未派发:" + ";".join(str(p) for p in pending)[:200])
    if reservation:
        reasons.append(reservation[:300])
    return "FAILED", evidence, ";".join(reasons)[:500]


def _context_digest(goal: str, completed: list[tuple[str, str]]) -> str:
    """受控上下文摘要:任务目标 + 最近已完成结论(非全量历史,体积有界)。"""
    parts = [f"任务目标:{goal[:400]}"]
    for g, s in completed[-3:]:
        parts.append(f"已完成「{g[:120]}」:{s[:300]}")
    return "\n".join(parts)[:1800]


async def run_task(
    *,
    payload: dict[str, object],
    llm: Any,
    api: ApiClient,
    settings: Settings,
    step_runner: StepRunner,
    collab_runner: CollabRunner | None = None,
) -> dict[str, object]:
    """模块入口(端点薄壳调用;依赖显式注入便于剧本化测试)。"""
    return await TaskOrchestrator(
        payload=payload,
        llm=llm,
        api=api,
        settings=settings,
        step_runner=step_runner,
        collab_runner=collab_runner,
    ).run()
