"""多智能体协作编排(091):宿主=任务步骤;worker=一次受限 ReAct 调用。

D1:orchestrator 是循环不是图——规划(拆 worker 子目标)→ 派发(每次派发 =
一次既有 ReAct 内核调用,上下文在派发时冻结)→ 聚合(交叉裁决/分歧验证/
critic 审查)→ 回写结构化结果。worker 间无直连:worker 输入在派发时冻结,
输出只进聚合,不回流为后续 worker 输入——「通信经 orchestrator」是结构保证。
失控防护(D4,三重上限):深度超限拒绝嵌套(缺省不嵌套,部署配置可调)、
worker 数上限(prompt 要求/解析/派发单一定义共用,部署 cap 参数化)、预算
显式继承(可派发 worker 数由组合根从任务步骤预算换算传入,不足则少派/不派
并披露未完成清单;分歧验证与 critic 修订重派同计入预算)。派发策略 serial/
parallel 由拓扑声明(parallel=asyncio.gather,worker 输入仍在派发时冻结,
结构保证不变)。聚合裁决:全 PASS 才 PASS;分歧重派验证或如实标注(不静默
调和);critic 审查不通过触发有限轮修订,轮次尽后如实附保留意见。触顶/预算
尽 → 部分完成语义(090 诚实收口),不新建终止语义。token 级记账在组合根以
worker 用量摘要落实(本模块零 api 句柄,授权面零扩大)。
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, cast

log = logging.getLogger(__name__)

# worker 数上限(单一定义:prompt 要求、派发截断共用;解析不截断以披露未派发清单)
DEFAULT_MAX_WORKERS = 3
# 嵌套深度上限(缺省不嵌套;超深构造即拒绝,提额须部署显式配置)
DEFAULT_MAX_DEPTH = 1
# critic 修订轮上限(缺省 1;轮次尽后如实附保留意见,不无限循环)
DEFAULT_MAX_REVISION_ROUNDS = 1

# worker 执行器类型(组合根以既有 ReAct 内核装配;签名仅子目标+上下文,无授权/凭证)
WorkerRunner = Callable[[str, str], Awaitable[dict[str, object]]]

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


def _parse_topology(raw: str) -> list[dict[str, object]]:
    """规划输出 → worker 子目标清单(剥围栏+首尾括号;畸形拒绝;空清单合法)。
    不在此截断:派发上限与预算截断在 run() 统一施加,未派发项如实披露。"""
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
        out.append({"subgoal": subgoal})
    return out


class CollabOrchestrator:
    """一次步骤内的协作编排(每步实例化;依赖显式注入便于剧本化测试)。

    worker_runner:测试与运行共用的 worker 执行器(见 WorkerRunner);
    depth:当前嵌套深度(任务步骤直启=1),超 max_depth 构造即拒绝;
    worker_budget:可派发 worker 执行总次数(初次派发+分歧验证+critic 修订
    重派全计入;预算硬继承,组合根从任务步骤预算换算),None 表示不设预算
    (仍受 worker 上限约束);
    max_workers_cap/max_depth/max_revision_rounds:部署级上限(缺省=模块
    常量;组合根自 Settings 注入,缺省值不漂移);
    dispatch:"serial"(缺省)|"parallel"(asyncio.gather,输入冻结不变);
    critic:是否启用 critic 审查与有限轮修订。
    """

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
        try:
            topo_raw = await self._llm.chat(
                _TOPOLOGY_SYSTEM,
                _TOPOLOGY_USER.format(subgoal=subgoal, context=context),
                temperature=0.2,
            )
            workers_spec: list[dict[str, object]] = _parse_topology(topo_raw)
        except Exception as e:  # noqa: BLE001 —— 拆解失败回落单 worker 直执
            log.warning("协作拆解失败,回落单 worker: %s", e)
            workers_spec = [{"subgoal": subgoal}]
        dispatched = workers_spec[: self._max_workers]
        self._budget_left -= len(dispatched)
        # 预算/上限截掉的未派发子目标如实披露(不谎称完成)
        pending = [str(s.get("subgoal") or "") for s in workers_spec[self._max_workers :]]

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

    async def _dispatch(
        self, specs: list[dict[str, object]], context: str
    ) -> list[dict[str, object]]:
        """派发执行(serial 循环 / parallel gather;worker 输入均在派发时冻结,
        失败 worker 如实记 ERROR 不中断他 worker)。"""
        if self._parallel:

            async def _one(spec: dict[str, object]) -> dict[str, object]:
                return await self._execute_one(str(spec.get("subgoal") or ""), context)

            raws = await asyncio.gather(*(_one(s) for s in specs))
            return list(raws)
        out: list[dict[str, object]] = []
        for spec in specs:
            out.append(await self._execute_one(str(spec.get("subgoal") or ""), context))
        return out

    async def _execute_one(self, w_subgoal: str, context: str) -> dict[str, object]:
        try:
            final = await self._run_worker(w_subgoal, context[:2000])
        except Exception as e:  # noqa: BLE001 —— worker 失败如实记录,不中断他 worker
            log.warning("协作 worker 执行失败(子目标=%s): %s", w_subgoal, e)
            return {
                "subgoal": w_subgoal,
                "verdict": "ERROR",
                "summary": f"worker 执行失败: {type(e).__name__}",
            }
        return {
            "subgoal": w_subgoal,
            "verdict": str(final.get("verdict") or "UNKNOWN"),
            "summary": str(final.get("summary") or "")[:2000],
            "sql": str(final.get("sql") or "")[:2000],
        }

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
        # 不参与交叉 verdict 计算(裁决链路失败不应拖垮原工作结论)
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

    async def _critic_loop(
        self,
        subgoal: str,
        context: str,
        conclusions: list[dict[str, object]],
        verdict: str,
        reservation: str | None,
    ) -> tuple[str, list[dict[str, object]], str | None]:
        """critic 审查 + 有限轮修订:不通过则重派被标记 worker(预算内),
        轮次尽后如实附保留意见;审查链路异常不阻断收口(降级为如实标注)。"""
        notes: list[str] = [reservation] if reservation else []
        rounds = max(1, self._max_revision_rounds)
        for round_no in range(rounds):
            try:
                critic_raw = await self._llm.chat(
                    _CRITIC_SYSTEM,
                    _CRITIC_USER.format(
                        subgoal=subgoal[:1000],
                        conclusions=json.dumps(conclusions, ensure_ascii=False)[:4000],
                    ),
                    temperature=0.2,
                )
                review = _json_object(critic_raw)
            except Exception as e:  # noqa: BLE001 —— 审查异常如实标注,不阻断
                log.warning("critic 审查异常: %s", e)
                notes.append("critic 审查异常(结论未经审查)")
                break
            if review is None:
                notes.append("critic 审查输出畸形(结论未经有效审查)")
                break
            if bool(review.get("pass")):
                break
            issues = cast("list[object]", review.get("issues") or [])
            flagged: dict[str, str] = {}
            for item in issues:
                if not isinstance(item, dict):
                    continue
                issue = cast("dict[str, object]", item)
                g = str(issue.get("subgoal") or "").strip()
                fb = str(issue.get("feedback") or "").strip()
                if g:
                    flagged[g] = fb[:500]
            if not flagged:
                # 审查不通过但无具体条目:无修订抓手,如实保留意见
                notes.append("critic 不通过但未给出可重做条目")
                break
            budget_short = False
            for g, fb in flagged.items():
                if not self._budget_take(1):
                    budget_short = True
                    break
                redo = await self._execute_one(
                    g, f"{context[:1000]}\n# critic 反馈(上一轮结论被退回)\n{fb}"
                )
                for i, c in enumerate(conclusions):
                    if str(c.get("subgoal") or "") == g:
                        conclusions[i] = redo
                        break
                else:
                    conclusions.append(redo)
            if budget_short:
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
    ).run()
