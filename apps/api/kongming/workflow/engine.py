"""workflow 执行引擎(零框架):拓扑序逐节点执行;上下文不可变
(节点产出深拷贝冻结,模板渲染路径亦深拷贝);条件选路(on 标记);
forEach 循环(迭代上限硬顶);全局超时(deadline 逐节点检查)+步数
上限(循环迭代计入);fail-fast(节点 FAILED 即终,快照保留)。

零 eval 铁律:条件比较/模板替换全走 models 手写纯函数。
"""

import asyncio
import copy
import logging
import time
from dataclasses import dataclass, field
from typing import Any, cast

from kongming.workflow.models import (
    DagValidationError,
    NodeDef,
    compare,
    render_template,
    resolve_ref,
)
from kongming.workflow.ports import AgentRunner, HttpRunner, SqlRunner, SsrFBlocked

log = logging.getLogger(__name__)

_HARD_ITER_CAP = 100  # 循环迭代硬顶(spec)


class NodeFailed(Exception):
    """节点级失败(fail-fast 收敛为 run FAILED)。"""


class _StepLimit(BaseException):
    """控制流(非错误):BaseException 防被 except Exception 误捕
    (收敛网不误伤步数/超时闸——与 CancelledError 同哲学)。"""


class _DeadlineExceeded(BaseException):
    """同上(控制流)。"""


@dataclass
class NodeRun:
    node_id: str
    status: str  # SUCCESS / FAILED / SKIPPED
    # default_factory 走参数化泛型别名(bare dict 推断出 Unknown 元素)
    outputs: dict[str, Any] = field(default_factory=dict[str, Any])
    error: str = ""
    duration_ms: int = 0


@dataclass
class RunOutcome:
    status: str  # SUCCESS / FAILED / TIMEOUT / STEP_LIMIT
    node_runs: list[NodeRun]
    error: str = ""
    duration_ms: int = 0


class _Engine:
    """单次执行的内部状态(上下文/步数/deadline)。"""

    def __init__(
        self,
        runners: tuple[SqlRunner, HttpRunner, AgentRunner],
        *,
        trace_id: str,
        tenant_key: str,
        user_id: str,
        deadline: float,
        step_cap: int,
        node_runs: list[NodeRun] | None = None,
    ) -> None:
        self._sql, self._http, self._agent = runners
        self._trace_id = trace_id
        self._tenant_key = tenant_key
        self._user_id = user_id
        self._deadline = deadline
        self._step_cap = step_cap
        self._steps = 0
        # 循环子节点逐次记入顶层快照(与 run_dag 同一列表,归因粒度到子节点)
        self._node_runs: list[NodeRun] = node_runs if node_runs is not None else []

    @property
    def deadline(self) -> float:
        """全局预算截止(monotonic 时钟);编排层读取剩余预算用。"""
        return self._deadline

    def _tick(self) -> None:
        self._steps += 1
        if self._steps > self._step_cap:
            raise _StepLimit(f"总步数超上限 {self._step_cap}")
        if time.monotonic() > self._deadline:
            raise _DeadlineExceeded("全局超时")

    async def execute_node(self, node: NodeDef, context: dict[str, Any]) -> dict[str, Any]:
        """单节点执行(循环子段复用);返回 outputs(未冻结,调用方冻结)。"""
        self._tick()
        cfg = node.config
        if node.type == "sql":
            return await self._run_sql(cfg, context)
        if node.type == "http":
            return await self._run_http(cfg, context)
        if node.type == "condition":
            return self._run_condition(cfg, context)
        if node.type == "loop":
            return await self._run_loop(node, context)
        if node.type == "transform":
            return {"value": render_template(cfg.get("template"), context)}
        if node.type == "agent":
            return await self._run_agent(cfg, context)
        raise NodeFailed(f"未知节点类型: {node.type}")

    async def _run_sql(self, cfg: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        """sql 节点:渲染模板 → 回调执行 → 未过检即失败,行数截断。"""
        sql = str(render_template(cfg.get("sql", ""), context))
        ds = str(cfg.get("datasource_key") or "default")
        result = await self._sql.run_sql(
            sql=sql,
            datasource_key=ds,
            trace_id=self._trace_id,
            tenant_key=self._tenant_key,
            user_id=self._user_id,
        )
        if result.get("verdict") != "PASS":
            raise NodeFailed(f"SQL 未过检: {result.get('reason')}")
        rows = cast("list[object]", result.get("rows") or [])
        return {
            "columns": result.get("columns") or [],
            "rows": rows[:1000],  # 快照截断(与执行 limit 对齐)
            "row_count": len(rows),
        }

    async def _run_http(self, cfg: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        """http 节点:渲染 url/method/headers/body 后回调执行;SSRF 拒绝归为节点失败。"""
        url = str(render_template(cfg.get("url", ""), context))
        method = str(cfg.get("method") or "GET").upper()
        if method not in ("GET", "POST", "PUT", "DELETE", "PATCH"):
            raise NodeFailed(f"非法 method: {method}")
        raw_headers = cfg.get("headers")
        headers: dict[str, Any] = (
            render_template(raw_headers, context) if isinstance(raw_headers, dict) else {}
        )
        body_raw = cfg.get("body")
        body = str(render_template(body_raw, context)) if body_raw is not None else None
        try:
            result = await self._http.run_http(
                url=url,
                method=method,
                headers={str(k): str(v) for k, v in headers.items()},
                body=body,
            )
        except SsrFBlocked as e:
            raise NodeFailed(f"SSRF 防护拒绝: {e}") from e
        return {"status": result.get("status"), "body": result.get("body")}

    @staticmethod
    def _run_condition(cfg: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        """condition 节点:渲染两侧操作数后比较;非法比较符归为节点失败。"""
        left = render_template(cfg.get("left"), context)
        right = render_template(cfg.get("right"), context)
        op = str(cfg.get("op") or "")
        try:
            outcome_flag = compare(op, left, right)
        except DagValidationError as e:
            raise NodeFailed(str(e)) from e
        return {"result": outcome_flag}

    async def _run_agent(self, cfg: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        """agent 节点:渲染指令后回调执行;success 非真即节点失败。"""
        instruction = str(render_template(cfg.get("instruction", ""), context))
        ds = str(cfg.get("datasource_key") or "default")
        result = await self._agent.run_agent(
            instruction=instruction,
            datasource_key=ds,
            trace_id=self._trace_id,
            tenant_key=self._tenant_key,
            user_id=self._user_id,
        )
        if not result.get("success"):
            raise NodeFailed(str(result.get("error") or "agent 执行失败"))
        rows = cast("list[object]", result.get("rows") or [])
        return {
            "summary": result.get("summary") or "",
            "sql": result.get("sql") or "",
            "rows": rows[:100],
        }

    async def _run_loop(self, node: NodeDef, context: dict[str, Any]) -> dict[str, Any]:
        items = resolve_ref(str(node.config.get("items_ref") or ""), context)
        if not isinstance(items, list):
            raise NodeFailed("loop.items_ref 未解析为数组")
        # isinstance 收窄产物元素型未知,收窄后仅做 len/遍历(元素原样透传)
        items = cast("list[object]", items)
        body_raw = node.config.get("body")
        if not isinstance(body_raw, list) or not body_raw:
            raise NodeFailed("loop.body 必须是非空子节点数组")
        from kongming.workflow.models import validate_dag

        body_nodes = validate_dag({"nodes": body_raw})  # 子段同规校验
        try:
            max_iter = min(int(node.config.get("max_iterations") or 0), _HARD_ITER_CAP)
        except (TypeError, ValueError):
            raise NodeFailed("loop.max_iterations 非法") from None
        if max_iter < 1:
            raise NodeFailed("loop.max_iterations 须 ≥1")
        if len(items) > max_iter:
            raise NodeFailed(f"循环数组 {len(items)} 项超上限 {max_iter}(硬顶 {_HARD_ITER_CAP})")
        collected: list[Any] = []
        sub_by_id = {n.id: n for n in body_nodes}
        for idx, item in enumerate(items):
            collected.append(
                await self._run_loop_item(node, idx, item, context, body_nodes, sub_by_id)
            )
        return {"items": collected, "iterations": len(items)}

    async def _run_loop_item(
        self,
        node: NodeDef,
        idx: int,
        item: object,
        context: dict[str, Any],
        body_nodes: list[NodeDef],
        sub_by_id: dict[str, NodeDef],
    ) -> Any:
        """执行单次循环体,返回末节点输出值。"""
        self._tick()  # 迭代计入步数
        sub_context = dict(context)
        sub_context["_loop"] = {"item": item, "index": idx}
        for sub in _topo_order(body_nodes):
            if sub.id in sub_context:
                continue
            await self._run_sub_node(sub, sub_context, sub_by_id, f"{node.id}.{idx}.{sub.id}")
        last = body_nodes[-1].id
        tail = sub_context.get(last, {}).get("outputs", {})
        return copy.deepcopy(tail.get("value", tail))

    async def _run_sub_node(
        self,
        sub: NodeDef,
        sub_context: dict[str, Any],
        sub_by_id: dict[str, NodeDef],
        sub_id: str,
    ) -> None:
        """执行循环内单个子节点并把结果写入子上下文与快照(失败记行后穿透)。"""
        # 子段选路与顶层同构(condition 门控+SKIPPED 传播)
        if _should_skip(sub, sub_context, sub_by_id):
            self._node_runs.append(NodeRun(node_id=sub_id, status="SKIPPED"))
            sub_context[sub.id] = {"_status": "SKIPPED", "outputs": {}}
            return
        sub_started = time.monotonic()
        try:
            outputs = await self.execute_node(sub, sub_context)
        except NodeFailed as e:
            # 失败按带迭代标记的子节点 id 归因(re-raise 由顶层
            # 收敛 loop 节点行——双行并存,子级可精确溯源)
            self._node_runs.append(
                NodeRun(
                    node_id=sub_id,
                    status="FAILED",
                    error=str(e),
                    duration_ms=int((time.monotonic() - sub_started) * 1000),
                )
            )
            raise
        except Exception as e:  # noqa: BLE001  # 未知异常同记行后穿透(顶层统一收敛)
            self._node_runs.append(
                NodeRun(
                    node_id=sub_id,
                    status="FAILED",
                    error=f"{type(e).__name__}: {str(e)[:300]}",
                    duration_ms=int((time.monotonic() - sub_started) * 1000),
                )
            )
            raise
        sub_context[sub.id] = {
            "_status": "SUCCESS",
            "outputs": copy.deepcopy(outputs),
        }
        self._node_runs.append(
            NodeRun(
                node_id=sub_id,
                status="SUCCESS",
                outputs=copy.deepcopy(outputs),
                duration_ms=int((time.monotonic() - sub_started) * 1000),
            )
        )

    async def run_node(self, node: NodeDef, context: dict[str, Any]) -> tuple[str, str] | None:
        """按剩余预算执行顶层节点并记快照;成功返回 None,否则返回 (终态, 错误)。"""
        node_started = time.monotonic()
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            return "TIMEOUT", "全局超时"
        # 节点级预算(挂死节点可中断);task 显式持有以区分超时来源:
        # wait_for 到期 ⇒ task 被其取消(cancelled ⇒ 真·全局预算超时);
        # 节点内源 TimeoutError(socket/驱动语句超时)透传 ⇒ task 未被
        # 取消——按节点失败归类,不得误标 TIMEOUT
        task = asyncio.create_task(self.execute_node(node, context))
        try:
            outputs = await asyncio.wait_for(task, timeout=remaining)
        except TimeoutError as e:
            if task.cancelled():
                self._record_failed(node, "节点超全局预算中断", node_started)
                return "TIMEOUT", "全局超时"
            self._record_failed(node, f"节点内部超时: {str(e)[:500]}", node_started)
            return "FAILED", f"节点 {node.id} 内部超时: {str(e)[:300]}"
        except NodeFailed as e:
            self._record_failed(node, str(e)[:500], node_started)
            return "FAILED", f"节点 {node.id} 失败: {str(e)[:300]}"
        except Exception as e:  # noqa: BLE001 # 未知异常收敛
            # httpx ConnectError/ReadTimeout、ValueError(非法
            # 端口)、RecursionError 等未知异常不得逃逸为 500 零落库——
            # 收敛节点 FAILED(run FAILED,已完成快照保留,spec 立约)
            log.warning("节点未知异常收敛 FAILED node=%s", node.id, exc_info=True)
            self._record_failed(node, f"{type(e).__name__}: {str(e)[:500]}", node_started)
            return "FAILED", f"节点 {node.id} 异常: {type(e).__name__}: {str(e)[:300]}"
        # 上下文不可变:产出深拷贝冻结(spec「上下文不可变」)
        context[node.id] = {"_status": "SUCCESS", "outputs": copy.deepcopy(outputs)}
        self._node_runs.append(
            NodeRun(
                node_id=node.id,
                status="SUCCESS",
                outputs=copy.deepcopy(outputs),
                duration_ms=int((time.monotonic() - node_started) * 1000),
            )
        )
        return None

    def _record_failed(self, node: NodeDef, error: str, started: float) -> None:
        """顶层节点失败快照行(错误原文截断 500 字)。"""
        self._node_runs.append(
            NodeRun(
                node_id=node.id,
                status="FAILED",
                error=error,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        )


def _topo_order(nodes: list[NodeDef]) -> list[NodeDef]:
    """Kahn 拓扑序(validate_dag 已保无环,此处纯排序)。"""
    indeg = {n.id: len(n.depends_on) for n in nodes}
    by_id = {n.id: n for n in nodes}
    queue = [i for i, d in indeg.items() if d == 0]
    out: list[NodeDef] = []
    while queue:
        cur = queue.pop(0)
        out.append(by_id[cur])
        for n in nodes:
            if cur in n.depends_on:
                indeg[n.id] -= 1
                if indeg[n.id] == 0:
                    queue.append(n.id)
    return out


def _should_skip(node: NodeDef, context: dict[str, Any], by_id: dict[str, NodeDef]) -> bool:
    """跳过判定:任一依赖 SKIPPED 传播;条件门仅当依赖是 condition 节点
    (按本节点 config.on[缺省 "true"] 与 dep.outputs.result 比对)——
    其他类型节点产出 bool 型 result 键不触发选路。"""
    for dep in node.depends_on:
        dep_state: dict[str, Any] = context.get(dep) or {}
        if dep_state.get("_status") == "SKIPPED":
            return True
        dep_def = by_id.get(dep)
        if dep_def is None or dep_def.type != "condition":
            continue  # 条件门仅对 condition 前驱生效
        dep_outputs: dict[str, Any] = dep_state.get("outputs") or {}
        if "result" in dep_outputs and isinstance(dep_outputs.get("result"), bool):
            on = str(node.config.get("on") or "true")
            want = on == "true"
            if bool(dep_outputs["result"]) != want:
                return True
    return False


async def _drive_topology(
    engine: "_Engine",
    nodes: list[NodeDef],
    context: dict[str, Any],
    by_id: dict[str, NodeDef],
    node_runs: list[NodeRun],
) -> tuple[str, str]:
    """按拓扑序驱动节点,返回终态与错误摘要(fail-fast / 步数与超时收敛)。"""
    status, error = "SUCCESS", ""
    try:
        for node in _topo_order(nodes):
            if _should_skip(node, context, by_id):
                node_runs.append(NodeRun(node_id=node.id, status="SKIPPED"))
                context[node.id] = {"_status": "SKIPPED", "outputs": {}}
                continue
            verdict = await engine.run_node(node, context)
            if verdict is not None:
                status, error = verdict
                break
    except _StepLimit as e:
        status, error = "STEP_LIMIT", str(e)
    except _DeadlineExceeded as e:
        status, error = "TIMEOUT", str(e)
    return status, error


async def run_dag(
    nodes: list[NodeDef],
    inputs: dict[str, Any],
    *,
    runners: tuple[SqlRunner, HttpRunner, AgentRunner],
    trace_id: str,
    tenant_key: str,
    user_id: str,
    timeout_sec: int = 120,
    step_cap: int = 200,
) -> RunOutcome:
    """引擎入口:拓扑序逐节点执行;fail-fast;超时/步数结构化终态。"""
    started = time.monotonic()
    node_runs: list[NodeRun] = []
    engine = _Engine(
        runners,
        trace_id=trace_id,
        tenant_key=tenant_key,
        user_id=user_id,
        deadline=started + timeout_sec,
        step_cap=step_cap,
        node_runs=node_runs,
    )
    context: dict[str, Any] = {"inputs": copy.deepcopy(inputs)}
    by_id = {n.id: n for n in nodes}
    outcome_status, outcome_error = await _drive_topology(engine, nodes, context, by_id, node_runs)
    return RunOutcome(
        status=outcome_status,
        node_runs=node_runs,
        error=outcome_error,
        duration_ms=int((time.monotonic() - started) * 1000),
    )
