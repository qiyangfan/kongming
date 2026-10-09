"""多智能体协作编排(091):宿主=任务步骤;worker=一次受限 ReAct 调用。

D1:orchestrator 是循环不是图——规划(拆 worker 子目标)→ 派发(每次派发 =
一次既有 ReAct 内核调用,上下文在派发时冻结)→ 聚合(交叉裁决/分歧标注)
→ 回写结构化结果。worker 间无直连:worker 输入在派发时冻结,输出只进聚合,
不回流为后续 worker 输入——「通信经 orchestrator」是结构保证。
失控防护(D4,三重上限):深度超限拒绝嵌套(缺省不嵌套,提额须显式改上限)、
worker 数上限(prompt 要求/解析/派发单一定义共用)、预算显式继承(可派发
worker 数由组合根从任务步骤预算换算传入,不足则少派/不派并披露未完成清单)。
v1 串行派发(结构上无并发);token 级记账在接线(组合根)时以 worker 用量
摘要落实。触顶/预算尽 → 部分完成语义(090 诚实收口),不新建终止语义。
"""

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, cast

log = logging.getLogger(__name__)

# worker 数上限(单一定义:prompt 要求、派发截断共用;解析不截断以披露未派发清单)
DEFAULT_MAX_WORKERS = 3
# 嵌套深度上限(缺省不嵌套;超深构造即拒绝,提额须显式改此常量并过审)
DEFAULT_MAX_DEPTH = 1

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


class CollabDepthExceeded(Exception):
    """协作嵌套超深(失控防护;worker 不得装配为子协作,除非显式提额)。"""


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
    depth:当前嵌套深度(任务步骤直启=1),超 DEFAULT_MAX_DEPTH 构造即拒绝;
    worker_budget:可派发 worker 数(预算硬继承,组合根从任务步骤预算换算);
    None 表示不设预算(仍受 DEFAULT_MAX_WORKERS 上限)。
    """

    def __init__(
        self,
        *,
        payload: dict[str, object],
        llm: Any,  # noqa: ANN401 —— LlmClient 显式类型在调用方;剧本化测试注入假体
        worker_runner: WorkerRunner,
        depth: int = 1,
        worker_budget: int | None = None,
    ) -> None:
        if depth > DEFAULT_MAX_DEPTH:
            raise CollabDepthExceeded(f"协作深度 {depth} 超上限 {DEFAULT_MAX_DEPTH}(嵌套未授权)")
        self._payload = payload
        self._llm = llm
        self._run_worker = worker_runner
        self._max_workers = DEFAULT_MAX_WORKERS if worker_budget is None else max(0, worker_budget)

    async def run(self) -> dict[str, object]:
        """执行协作:拆解 → 派发 → 聚合 → 裁决。返回聚合结论载荷。"""
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
            }
        try:
            topo_raw = await self._llm.chat(
                _TOPOLOGY_SYSTEM,
                _TOPOLOGY_USER.format(subgoal=subgoal, context=context),
                temperature=0.2,
            )
            workers_spec = _parse_topology(topo_raw)
        except Exception as e:  # noqa: BLE001 —— 拆解失败回落单 worker 直执
            log.warning("协作拆解失败,回落单 worker: %s", e)
            workers_spec = [{"subgoal": subgoal}]
        dispatched = workers_spec[: self._max_workers]
        # 预算/上限截掉的未派发子目标如实披露(不谎称完成)
        pending = [str(s.get("subgoal") or "") for s in workers_spec[self._max_workers :]]

        conclusions: list[dict[str, object]] = []
        for spec in dispatched:
            w_subgoal = str(spec.get("subgoal") or subgoal)
            try:
                final = await self._run_worker(w_subgoal, context[:2000])
            except Exception as e:  # noqa: BLE001 —— worker 失败如实记录,不中断他 worker
                log.warning("协作 worker 执行失败(子目标=%s): %s", w_subgoal, e)
                conclusions.append(
                    {
                        "subgoal": w_subgoal,
                        "verdict": "ERROR",
                        "summary": f"worker 执行失败: {type(e).__name__}",
                    }
                )
                continue
            conclusions.append(
                {
                    "subgoal": w_subgoal,
                    "verdict": str(final.get("verdict") or "UNKNOWN"),
                    "summary": str(final.get("summary") or "")[:2000],
                    "sql": str(final.get("sql") or "")[:2000],
                }
            )
        # 分歧裁决:同题多 worker 结论不一致 → 如实标注(不静默调和)
        summaries = [str(c.get("summary") or "") for c in conclusions if c.get("verdict") == "PASS"]
        if len(summaries) >= 2 and len(set(summaries)) > 1:
            dissent = True
        else:
            dissent = False
        # 交叉裁决:全部 PASS 方为 PASS(任一失败即不通过;明细见 conclusions)
        verdict = (
            "PASS"
            if conclusions and all(c.get("verdict") == "PASS" for c in conclusions)
            else "ERROR"
        )

        return {
            "verdict": verdict,
            "summary": json.dumps(conclusions, ensure_ascii=False),
            "conclusions": conclusions,
            "pending_subgoals": pending,
            "dissent": dissent,
            "worker_count": len(conclusions),
        }


async def run_collab(
    *,
    payload: dict[str, object],
    llm: Any,  # noqa: ANN401
    worker_runner: WorkerRunner,
    depth: int = 1,
    worker_budget: int | None = None,
) -> dict[str, object]:
    """模块入口(端点薄壳调用)。不持 api 句柄:协作内工具调用经 worker 的
    ReAct 回调,orchestrator 不直接回调控制面(接线持久化时再评审授权面)。"""
    return await CollabOrchestrator(
        payload=payload,
        llm=llm,
        worker_runner=worker_runner,
        depth=depth,
        worker_budget=worker_budget,
    ).run()
