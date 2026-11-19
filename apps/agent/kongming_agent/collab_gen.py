"""多智能体协作编排。

每个 worker 以受限 ReAct 执行,输入在派发时固定,结果只交编排器汇总,
不直接传给其他 worker;worker 数、嵌套深度、执行预算均有限制。
预算耗尽或结论不一致时,如实返回未完成项与分歧,不伪造共识。"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, cast

from kongming_agent.task_context import TASK_CONTEXT_HINT

log = logging.getLogger(__name__)

# worker 数上限(单一定义:prompt 要求、派发截断共用;解析不截断以披露未派发清单)
DEFAULT_MAX_WORKERS = 3
# 嵌套深度上限(缺省不嵌套;超深构造即拒绝,提额须部署显式配置)
DEFAULT_MAX_DEPTH = 1
# critic 修订轮上限(缺省 1;轮次尽后如实附保留意见,不无限循环)
DEFAULT_MAX_REVISION_ROUNDS = 1

# worker 执行器类型(组合根以既有 ReAct 内核装配;入参为子目标+上下文+槽位:
# slot=名册成员标签(collab-worker-roster)或 None=无绑定(顶层单绑定/裸 worker/
# 治理动作),授权/凭证仍零持有)
WorkerRunner = Callable[[str, str, str | None], Awaitable[dict[str, object]]]

_TOPOLOGY_SYSTEM = (
    "你是任务分解专家。把给定子目标拆成"
    f" 1-{DEFAULT_MAX_WORKERS} 个可独立执行(只读数据查询)的 worker 子目标。"
    '只输出一个 JSON 数组,每项 {"subgoal": "..."}。不要输出任何其他文字。'
)

_TOPOLOGY_USER = """# 父子目标
{subgoal}
# 已有上下文摘要
{context}

请输出 worker 子目标数组:"""

# 名册形态(collab-worker-roster):分配制规划——子目标逐一分配给给定成员,
# worker 字段必须从成员清单逐字选取;纯名册语义(不虚构名册外执行者)
_ROSTER_TOPOLOGY_SYSTEM = (
    "你是任务分解专家。把给定子目标拆成"
    " 1-{max_workers} 个可独立执行(只读数据查询)的子目标,"
    "并把每个子目标分配给成员清单中的一员。"
    '只输出一个 JSON 数组,每项 {{"subgoal": "...", "worker": "<成员名>"}},'
    "worker 必须从成员清单中逐字选取,同一成员可承接多个子目标。"
    "不要输出任何其他文字。"
)

_ROSTER_TOPOLOGY_USER = """# 父子目标
{subgoal}
# 已有上下文摘要
{context}
# 成员清单
{roster}

请输出分配了成员的子目标数组:"""

# 分歧验证 worker:重派一个核实子任务(输入=双方结论;输出照常结构化)
_VERIFY_SYSTEM = (
    "你是分歧核实员。两位分析员对同一问题给出了不同结论,"
    "请独立核实并给出你的结论(以数据/证据为准)。"
    '输出 JSON:{"summary": "你的核实结论", "supports": "A|B|neither"}。'
    "supports 指你的结论支持哪一方(证据不足以支持任一方时填 neither)。"
    "不要输出任何其他文字。"
)
_VERIFY_USER = """# 原问题
{subgoal}
# 结论 A
{summary_a}
# 结论 B
{summary_b}
# 已有上下文
{context}

请核实并输出:"""

# critic 审查:一致性/证据充分性;不通过触发修订(有限轮)
_CRITIC_SYSTEM = (
    "你是结论审查员(critic)。审查聚合结论的一致性与证据充分性:"
    "结论之间是否互相矛盾、关键论断是否缺少证据支撑。"
    '只输出 JSON:{"pass": true/false, "issues": [{"subgoal": "...", "feedback": "..."}]}。'
    "pass=false 时 issues 必须逐条给出需重做的子目标与原因。不要输出任何其他文字。"
)
_CRITIC_USER = """# 父子目标
{subgoal}
# 聚合结论
{conclusions}

请审查并输出:"""


class CollabDepthExceeded(Exception):
    """协作嵌套超深(失控防护;worker 不得装配为子协作,除非显式提额)。"""


def _json_object(raw: str) -> dict[str, object] | None:
    """LLM 输出 → JSON 对象(剥围栏+首尾花括号;畸形返回 None)。"""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return cast("dict[str, object]", obj) if isinstance(obj, dict) else None


def _parse_flagged(review: dict[str, object]) -> dict[str, str]:
    """从 critic 审查结果提取可重做条目:子目标 → 反馈(截断 500 字)。"""
    issues = cast("list[object]", review.get("issues") or [])
    flagged: dict[str, str] = {}
    for item in issues:
        if not isinstance(item, dict):
            continue
        issue = cast("dict[str, object]", item)
        subgoal = str(issue.get("subgoal") or "").strip()
        feedback = str(issue.get("feedback") or "").strip()
        if subgoal:
            flagged[subgoal] = feedback[:500]
    return flagged


def _parse_topology(raw: str) -> list[dict[str, object]]:
    """规划输出 → worker 子目标清单(剥围栏+首尾括号;畸形拒绝;空清单合法)。
    不在此截断:派发上限与预算截断在 run 统一施加,未派发项如实披露。"""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3]
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        raise ValueError("topology 输出无数组")
    obj = json.loads(text[start : end + 1])
    if not isinstance(obj, list):
        raise ValueError("topology 输出非数组")

    out: list[dict[str, object]] = []
    for item in cast("list[object]", obj):
        itemd = cast("dict[str, object] | None", item if isinstance(item, dict) else None)
        if itemd is None:
            continue
        subgoal = str(itemd.get("subgoal") or "").strip()
        if not subgoal:
            continue
        entry: dict[str, object] = {"subgoal": subgoal}
        # 分配制输出(collab-worker-roster):worker 字段原样透传,名册校验在
        # 编排器映射段(此处不持名册知识,畸形值不在此丢弃以保披露完整)
        worker = str(itemd.get("worker") or "").strip()
        if worker:
            entry["worker"] = worker
        out.append(entry)
    return out


class CollabOrchestrator:
    """协作编排器:拆解、派发 worker、聚合与有限复核;依赖注入,预算受限。"""

    def __init__(
        self,
        *,
        payload: dict[str, object],
        llm: Any,  # noqa: ANN401 —— LlmClient 显式类型在调用方;剧本化测试注入假体
        worker_runner: WorkerRunner,
        depth: int = 1,
        worker_budget: int | None = None,
        max_workers_cap: int = DEFAULT_MAX_WORKERS,
        max_depth: int = DEFAULT_MAX_DEPTH,
        max_revision_rounds: int = DEFAULT_MAX_REVISION_ROUNDS,
        dispatch: str = "serial",
        critic: bool = False,
        roster: list[str] | None = None,
    ) -> None:
        if depth > max_depth:
            raise CollabDepthExceeded(f"协作深度 {depth} 超上限 {max_depth}(嵌套未授权)")
        self._payload = payload
        self._llm = llm
        self._run_worker = worker_runner
        cap = max(1, max_workers_cap)
        budget = cap if worker_budget is None else max(0, worker_budget)
        # 有效上限 = min(部署 cap, 预算);预算为总执行次数(含验证/修订重派)
        self._max_workers = min(cap, budget)
        self._budget_left = budget
        self._max_revision_rounds = max(0, max_revision_rounds)
        self._parallel = dispatch == "parallel"
        self._critic_enabled = critic
        self._roster = [str(m) for m in roster if str(m)] if roster else None

    async def run(self) -> dict[str, object]:
        """执行协作:拆解 → 派发 → 聚合 → 分歧裁决 → critic 审查/修订。"""
        subgoal = str(self._payload.get("subgoal") or "")
        context = str(self._payload.get("context_summary") or "")
        if self._max_workers <= 0:
            # 预算继承不足:不拆解不派发,空收口并披露未完成(部分完成语义)
            return {
                "verdict": "ERROR",
                "summary": "[]",
                "conclusions": [],
                "pending_subgoals": [subgoal],
                "dissent": False,
                "worker_count": 0,
                "reservation": "预算继承不足,未派发任何 worker",
            }
        workers_spec, failure = await self._plan_workers(subgoal, context)
        if failure is not None:
            return failure
        dispatched, pending = self._split_dispatched(cast("list[dict[str, object]]", workers_spec))

        conclusions = await self._dispatch(dispatched, context)
        reservation: str | None = None

        # 分歧裁决:PASS 结论互异 → 重派验证(预算内)或如实标注(不静默调和)
        dissent, reservation = await self._resolve_dissent(subgoal, context, conclusions)
        verdict = self._verdict_of(conclusions)

        # critic 审查 + 有限轮修订(审查失败如实附保留意见,不阻断收口)
        if self._critic_enabled and conclusions:
            verdict, conclusions, reservation = await self._critic_loop(
                subgoal, context, conclusions, verdict, reservation
            )

        return {
            "verdict": verdict,
            "summary": json.dumps(conclusions, ensure_ascii=False),
            "conclusions": conclusions,
            "pending_subgoals": pending,
            "dissent": dissent,
            "worker_count": len(conclusions),
            "reservation": reservation,
        }

    async def _plan_workers(
        self, subgoal: str, context: str
    ) -> tuple[list[dict[str, object]] | None, dict[str, object] | None]:
        """拆解子目标;拆解失败时名册形态空收口披露,匿形态回落单 worker。"""
        try:
            if self._roster:
                topo_raw = await self._llm.chat(
                    _ROSTER_TOPOLOGY_SYSTEM.format(max_workers=self._max_workers),
                    _ROSTER_TOPOLOGY_USER.format(
                        subgoal=subgoal,
                        context=context,
                        roster="\n".join(f"- {m}" for m in self._roster),
                    ),
                    temperature=0.2,
                )
            else:
                topo_raw = await self._llm.chat(
                    _TOPOLOGY_SYSTEM,
                    _TOPOLOGY_USER.format(subgoal=subgoal, context=context),
                    temperature=0.2,
                )
            return _parse_topology(topo_raw), None
        except Exception as e:  # noqa: BLE001 —— 拆解失败按名册/匿形态分别收口
            if self._roster:
                # 纯名册语义:不回落合成 worker(名册外执行者禁入),父子目标
                # 如实进未派发清单(诚实收口,不静默降级)
                log.warning("名册拆解失败,空收口披露: %s", e)
                return None, {
                    "verdict": "ERROR",
                    "summary": "[]",
                    "conclusions": [],
                    "pending_subgoals": [subgoal],
                    "dissent": False,
                    "worker_count": 0,
                    "reservation": f"名册拆解失败: {type(e).__name__}",
                }
            log.warning("协作拆解失败,回落单 worker: %s", e)
            return [{"subgoal": subgoal}], None

    def _split_dispatched(
        self, specs: list[dict[str, object]]
    ) -> tuple[list[dict[str, object]], list[str]]:
        """按名册/上限切出派发清单,未派发子目标如实记入 pending。"""
        pending: list[str] = []
        if self._roster:
            # 分配制映射:名册内 → 派发(带成员槽位);名册外/缺分配 → 未派发
            # 披露(不静默丢弃、不改派合成 worker);截断披露通道与匿形态同式
            roster_set = set(self._roster)
            mapped: list[dict[str, object]] = []
            for spec in specs:
                worker = str(spec.get("worker") or "")
                text = str(spec.get("subgoal") or "")
                if worker in roster_set:
                    mapped.append(spec)
                else:
                    pending.append(f"{text}(分配对象 {worker or '缺失'} 不在名册)")
            dispatched = mapped[: self._max_workers]
            self._budget_left -= len(dispatched)
            pending.extend(str(s.get("subgoal") or "") for s in mapped[self._max_workers :])
            return dispatched, pending
        dispatched = specs[: self._max_workers]
        self._budget_left -= len(dispatched)
        # 预算/上限截掉的未派发子目标如实披露(不谎称完成)
        pending = [str(s.get("subgoal") or "") for s in specs[self._max_workers :]]
        return dispatched, pending

    async def _dispatch(
        self, specs: list[dict[str, object]], context: str
    ) -> list[dict[str, object]]:
        """派发执行(serial 循环 / parallel gather;worker 输入均在派发时冻结,
        失败 worker 如实记 ERROR 不中断他 worker;槽位随条目透传,无名册恒 None)。"""
        if self._parallel:

            async def _one(spec: dict[str, object]) -> dict[str, object]:
                # slot 仅名册形态透传:匿名形态无成员语义,拆解输出混入的
                # worker 字段(幻觉)不得成为槽位/结论标签(无名册零漂移)
                return await self._execute_one(
                    str(spec.get("subgoal") or ""),
                    context,
                    spec.get("worker") if self._roster else None,
                )

            raws = await asyncio.gather(*(_one(s) for s in specs))
            return list(raws)
        out: list[dict[str, object]] = []
        for spec in specs:
            out.append(
                await self._execute_one(
                    str(spec.get("subgoal") or ""),
                    context,
                    spec.get("worker") if self._roster else None,
                )
            )
        return out

    async def _execute_one(
        self, w_subgoal: str, context: str, slot: object | None = None
    ) -> dict[str, object]:
        ctx = context[:2000]
        if len(context) > 2000:
            ctx += f"\n{TASK_CONTEXT_HINT}"
        try:
            final = await self._run_worker(w_subgoal, ctx, str(slot) if slot is not None else None)
        except Exception as e:  # noqa: BLE001 —— worker 失败如实记录,不中断他 worker
            log.warning("协作 worker 执行失败(子目标=%s): %s", w_subgoal, e)
            return {
                "subgoal": w_subgoal,
                "verdict": "ERROR",
                "summary": f"worker 执行失败: {type(e).__name__}",
            }
        entry: dict[str, object] = {
            "subgoal": w_subgoal,
            "verdict": str(final.get("verdict") or "UNKNOWN"),
            "summary": str(final.get("summary") or "")[:2000],
            "sql": str(final.get("sql") or "")[:2000],
        }
        if slot is not None:
            # 名册成员执行:结论条目携带成员标签(按成员归集/账本对照)
            entry["worker"] = str(slot)
        return entry

    def _budget_take(self, n: int = 1) -> bool:
        """预算扣减(验证/修订重派同计入);不足返回 False(不派发)。"""
        if self._budget_left < n:
            return False
        self._budget_left -= n
        return True

    @staticmethod
    def _verdict_of(conclusions: list[dict[str, object]]) -> str:
        """交叉裁决(单一定义):工作结论全部 PASS 方为 PASS(任一失败即不通过);
        分歧核实条目(verification=True)只作裁决证据,不计入交叉判定。"""
        work = [c for c in conclusions if not c.get("verification")]
        if work and all(c.get("verdict") == "PASS" for c in work):
            return "PASS"
        return "ERROR"

    async def _resolve_dissent(
        self, subgoal: str, context: str, conclusions: list[dict[str, object]]
    ) -> tuple[bool, str | None]:
        """分歧裁决:重派验证子任务(预算内)后按核实结果采纳一方;
        预算不足/核实不支持任一方/核实失败 → 如实标注分歧(不静默调和)。"""
        passed = [c for c in conclusions if c.get("verdict") == "PASS"]
        if len(passed) < 2 or len({str(c.get("summary") or "") for c in passed}) < 2:
            return False, None
        a, b = passed[0], passed[1]
        if not self._budget_take(1):
            return True, "存在分歧且预算不足,未能派发核实(分歧如实保留)"
        # 重派验证 = 独立重跑一次 + LLM 仲裁采信哪方;核实条目只作裁决证据,
        # 分歧裁决失败不影响原工作结论。
        verify = await self._execute_one(f"核实分歧:{subgoal}", context)
        try:
            verdict_raw = await self._llm.chat(
                _VERIFY_SYSTEM,
                _VERIFY_USER.format(
                    subgoal=subgoal[:1000],
                    summary_a=str(a.get("summary") or "")[:800],
                    summary_b=str(b.get("summary") or "")[:800],
                    context=context[:1000],
                ),
                temperature=0.2,
            )
            arb = _json_object(verdict_raw) or {}
        except Exception as e:  # noqa: BLE001 —— 裁决失败如实标注
            log.warning("分歧裁决 LLM 异常: %s", e)
            arb = {}
        conclusion_entry: dict[str, object] = {
            "subgoal": f"核实分歧:{subgoal}"[:2000],
            "verdict": str(verify.get("verdict") or "UNKNOWN"),
            "summary": str(verify.get("summary") or "")[:2000],
            "verification": True,
        }
        supports = str(arb.get("supports") or "neither")
        if supports in ("A", "B"):
            winner = a if supports == "A" else b
            conclusion_entry["summary"] = (
                f"核实采纳 {supports} 方: {str(winner.get('summary') or '')[:1600]}"
            )[:2000]
        else:
            conclusion_entry["summary"] = (
                "核实不足以支持任一方,分歧如实保留:"
                f"A={str(a.get('summary') or '')[:400]} vs B={str(b.get('summary') or '')[:400]}"
            )[:2000]
        conclusions.append(conclusion_entry)
        if supports in ("A", "B"):
            return False, None
        return True, "分歧经核实仍无法采信任一方(如实标注,不调和)"

    async def _review(
        self, subgoal: str, conclusions: list[dict[str, object]]
    ) -> tuple[dict[str, object] | None, str | None]:
        """执行一次 critic 审查;异常或畸形输出转为保留意见文案。"""
        try:
            critic_raw = await self._llm.chat(
                _CRITIC_SYSTEM,
                _CRITIC_USER.format(
                    subgoal=subgoal[:1000],
                    conclusions=json.dumps(conclusions, ensure_ascii=False)[:4000],
                ),
                temperature=0.2,
            )
        except Exception as e:  # noqa: BLE001 —— 审查异常如实标注,不阻断
            log.warning("critic 审查异常: %s", e)
            return None, "critic 审查异常(结论未经审查)"
        review = _json_object(critic_raw)
        if review is None:
            return None, "critic 审查输出畸形(结论未经有效审查)"
        return review, None

    async def _apply_revisions(
        self, flagged: dict[str, str], context: str, conclusions: list[dict[str, object]]
    ) -> bool:
        """按 critic 条目重派 worker;返回 True 表示预算不足提前中止。"""
        for g, fb in flagged.items():
            if not self._budget_take(1):
                return True
            redo = await self._execute_one(
                g, f"{context[:1000]}\n# critic 反馈(上一轮结论被退回)\n{fb}"
            )
            for i, c in enumerate(conclusions):
                if str(c.get("subgoal") or "") == g:
                    conclusions[i] = redo
                    break
            else:
                conclusions.append(redo)
        return False

    async def _critic_loop(
        self,
        subgoal: str,
        context: str,
        conclusions: list[dict[str, object]],
        verdict: str,
        reservation: str | None,
    ) -> tuple[str, list[dict[str, object]], str | None]:
        """critic 不通过时，在剩余预算内重派被标记的 worker；复核失败只作为保留意见返回。"""
        notes: list[str] = [reservation] if reservation else []
        rounds = max(1, self._max_revision_rounds)
        for round_no in range(rounds):
            review, note = await self._review(subgoal, conclusions)
            if note is not None:
                notes.append(note)
                break
            if review is not None and bool(review.get("pass")):
                break
            flagged = _parse_flagged(review or {})
            if not flagged:
                # 审查不通过但无具体条目:无修订抓手,如实保留意见
                notes.append("critic 不通过但未给出可重做条目")
                break
            if await self._apply_revisions(flagged, context, conclusions):
                # 部分重派已落账:先按当前结论重算归因,再如实保留预算缺口
                verdict = self._verdict_of(conclusions)
                notes.append("critic 修订预算不足,未重派项如实保留")
                break
            verdict = self._verdict_of(conclusions)
            if verdict == "PASS":
                break
            if round_no + 1 < rounds:
                # 轮次未尽:带修订后结论进入下一轮审查(N>1 时修订循环真实生效)
                continue
            notes.append(f"critic 修订(至多 {rounds} 轮)后仍未全通过")
            break
        if verdict != "PASS" and notes:
            reservation = ";".join(dict.fromkeys(n for n in notes if n))[:1000]
        return verdict, conclusions, reservation


async def run_collab(
    *,
    payload: dict[str, object],
    llm: Any,  # noqa: ANN401
    worker_runner: WorkerRunner,
    depth: int = 1,
    worker_budget: int | None = None,
    max_workers_cap: int = DEFAULT_MAX_WORKERS,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_revision_rounds: int = DEFAULT_MAX_REVISION_ROUNDS,
    dispatch: str = "serial",
    critic: bool = False,
    roster: list[str] | None = None,
) -> dict[str, object]:
    """模块入口(端点薄壳调用)。不持 api 句柄:协作内工具调用经 worker 的
    ReAct 回调,orchestrator 不直接回调控制面(账本回写在组合根装配)。"""
    return await CollabOrchestrator(
        payload=payload,
        llm=llm,
        worker_runner=worker_runner,
        depth=depth,
        worker_budget=worker_budget,
        max_workers_cap=max_workers_cap,
        max_depth=max_depth,
        max_revision_rounds=max_revision_rounds,
        dispatch=dispatch,
        critic=critic,
        roster=roster,
    ).run()
