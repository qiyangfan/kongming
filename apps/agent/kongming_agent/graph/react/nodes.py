"""ReAct 内核节点(019):react_llm(LLM 决策)→ tool_dispatch(回调执行)→ 循环。

纪律:
- 观察截断:喂 LLM 的 tool 消息 rows 只带前 MAX_OBSERVE_ROWS 行(全量留
  last_execution 供终态)——防大结果集打爆 token;
- 参数畸形(args=None)不回调,直接作为错误观察喂回 LLM 自纠;
- confirm_required 观察带「停止调用」指令,引导 LLM 终止循环说明待确认;
- tool_call 流式事件经 emit_event(running→done/rejected 成对);
- 终态字段与 8 节点图同构(execution/summary/generated_sql/retry_count),
  _build_query_out 零改动复用。
"""

import json
import logging
from typing import cast

from langgraph.types import interrupt

from kongming_agent import deps, numeric
from kongming_agent import todos as todo_mod
from kongming_agent.apiclient import ToolExecuteOutcome
from kongming_agent.graph.nodes import compute_stats, fallback_summary
from kongming_agent.graph.react.prompts import (
    build_react_system,
    initial_user_prompt,
)
from kongming_agent.graph.react.state import ReactState
from kongming_agent.graph.state import ExecutionResult
from kongming_agent.graph.streams import emit_event
from kongming_agent.llm import LlmToolCall
from kongming_agent.settings import Settings

log = logging.getLogger(__name__)

MAX_OBSERVE_ROWS = 20  # 喂 LLM 的观察行数截断(全量结果留终态)
_OBSERVE_TEXT_MAX = 2000  # 单条观察文本总长截断


def openai_tools(tools: list[dict[str, object]]) -> list[dict[str, object]]:
    """api 注入的 ToolSpec 列表 → OpenAI tools 参数(纯函数)。"""
    return [
        {
            "type": "function",
            "function": {
                "name": str(t.get("name") or ""),
                "description": str(t.get("description") or ""),
                "parameters": t.get("parameters") or {"type": "object", "properties": {}},
            },
        }
        for t in tools
    ]


# ── 节点1:LLM 决策 ──
async def react_llm(state: ReactState) -> dict[str, object]:
    messages = state.get("messages")
    if not messages:
        messages = [
            {
                "role": "user",
                # 入口 state 恒写入 question(_initial_react_state);缺键属编程错误
                "content": initial_user_prompt(
                    state["question"],  # pyright: ignore[reportTypedDictNotRequiredAccess]
                    state.get("history", [])[-5:],
                    state.get("memory_hint", ""),
                ),
            }
        ]
    # 能力-契约一致(079):纪律段按注入工具集派生——空=无工具纪律段,
    # 全套件=历史逐字,裁剪集=不教模型调用未注入的工具
    _tools = state.get("tools") or []
    system_prompt = build_react_system(
        state.get("bot_system_prompt") or "",
        has_tools=bool(_tools),
        tool_names=[str(t.get("name") or "") for t in _tools],
    )
    reply = await deps.get_llm().chat_with_tools(
        system_prompt,
        messages,  # type: ignore[arg-type]  # dict 列表形态与 SDK TypedDict 对齐
        openai_tools(state.get("tools", [])),
        task="REACT",
        trace_id=state.get("trace_id"),
        tenant_key=state.get("tenant_key"),
        model_override=state.get("model_override") or None,  # 025
    )
    if reply.tool_calls:
        assistant_msg: dict[str, object] = {
            "role": "assistant",
            "content": reply.content or "",
            "tool_calls": [
                {
                    "id": c.call_id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": c.raw_args or "{}"},
                }
                for c in reply.tool_calls
            ],
        }
        return {
            "messages": [*messages, assistant_msg],
            "pending_tool_calls": reply.tool_calls,
        }
    return {
        "messages": [
            *messages,
            {"role": "assistant", "content": reply.content or ""},
        ],
        "final_answer": reply.content or "",
        "pending_tool_calls": [],
    }


# ── 节点2:工具执行(回调控制面,零凭证)──
async def tool_dispatch(state: ReactState) -> dict[str, object]:
    messages = list(state.get("messages") or [])
    calls = state.get("pending_tool_calls") or []
    rounds = state.get("rounds", 0) + 1
    # 重复熔断(019 UI 冒烟实证):同 trace 内同工具同参数已成功过的调用,
    # 不回调控制面(零副作用零审计噪音),直接观察引导 LLM 基于已有信息作答
    executed_sigs: set[str] = set(state.get("executed_sigs") or [])
    compute_values: list[float] = list(state.get("compute_values") or [])
    todo_items = todo_mod.todos_from_dicts(state.get("todos") or [])
    out: dict[str, object] = {"rounds": rounds, "pending_tool_calls": []}
    # 多子目标建单(058):问句可保守拆出 ≥2 目标时落账;单目标不建(零回归)
    if not todo_items:
        goals = todo_mod.split_goals(state.get("question") or "")
        if goals:
            todo_items = todo_mod.create_todos(goals)
    for call in calls:
        emit_event(
            "tool_call",
            {"tool_name": call.name, "status": "running", "args_digest": _digest(call)},
        )
        sig = ""
        if call.args is not None:
            sig = (
                call.name
                + "|"
                + json.dumps(call.args, ensure_ascii=False, sort_keys=True, default=str)
            )
        if call.args is None:
            # 参数 JSON 畸形:不回调,直接观察喂回 LLM 自纠
            obs: dict[str, object] = {
                "status": "error",
                "reason": f"工具参数 JSON 畸形,请修正后重试: {call.raw_args[:200]}",
            }
            outcome_status = "rejected"
        elif sig and sig in executed_sigs:
            obs = {
                "status": "error",
                "reason": "该调用(同工具同参数)已成功执行过,结果相同。"
                "请基于已有信息直接作答或换一个思路,不要重复调用。",
            }
            outcome_status = "rejected"
        elif call.name in ("compute_delta", "compute_share"):
            # 057:确定性派生量本地执行(纯函数+本轮白名单),不回调控制面
            # 白名单含本轮同批 execute_sql 刚落库的结果(out.last_execution)
            obs, outcome_status, add_vals = _run_local_compute(call, state, compute_values, out)
            compute_values.extend(add_vals)
            if outcome_status == "done" and sig:
                executed_sigs.add(sig)
        else:
            # 入口 state 恒含 trace_id/tenant_key(_initial_react_state 写入)
            outcome = await deps.get_api().tool_execute(
                trace_id=state["trace_id"],  # pyright: ignore[reportTypedDictNotRequiredAccess]
                tenant_key=state["tenant_key"],  # pyright: ignore[reportTypedDictNotRequiredAccess]
                datasource_key=state.get("datasource_key", "default"),
                user_id=state.get("user_id"),
                tool_name=call.name,
                args=call.args,
                channel=state.get("channel", "chat"),  # 025
            )
            if outcome.status == "confirm_required" and state.get("channel") == "task":
                # add-graph-checkpoint:任务通道高危动作挂图(HITL 第三形态)——
                # 在途确认入状态,经 confirm_gate(interrupt)挂图上呈审批;
                # 不喂观察不记账本(动作未执行),兑现观察由 tool_resolve 按决策回填
                out["confirm_inflight"] = {
                    "confirm_id": outcome.confirm_id or "",
                    "tool_name": call.name,
                    "call_id": call.call_id,
                    "summary": f"调用工具 {call.name}",
                }
                emit_event(
                    "tool_call",
                    {
                        "tool_name": call.name,
                        "status": "confirm_required",
                        "args_digest": _digest(call),
                        "result_digest": "待人工确认,图挂起",
                    },
                )
                break
            obs = _observe(outcome)
            # confirm_required 按 done 下发(spec:result_digest 含「待确认」),
            # rejected 仅防御层拒绝/执行错误语义
            outcome_status = (
                "done" if outcome.status in ("executed", "confirm_required") else "rejected"
            )
            if outcome.status == "executed":
                # 重复熔断只收「真成功」的调用:execute_sql 工具通道 status 恒为
                # executed,SQL 层 verdict(PASS/BLOCKED/ERROR)在 result 内——
                # BLOCKED/ERROR 的调用若入集,LLM 重发同一条 SQL 会收到
                # 「已成功执行过」的假观察,诱导编造数据(审查循环 S1)
                result = outcome.result or {}
                really_ok = (
                    str(result.get("verdict") or "PASS") == "PASS"
                    if call.name == "execute_sql"
                    else True
                )
                if really_ok and sig:
                    executed_sigs.add(sig)
                if call.name == "execute_sql":
                    _capture_execution(out, call, outcome)
                    # 057:成功结果数值入本轮白名单(多查询时 ERROR 不得抹掉先前数字)
                    if really_ok:
                        compute_values.extend(_numeric_cells(out.get("last_execution")))
        # 058:工具结果回写账本(成功且非空才 DONE;失败/空/不适用如实标记)
        todo_items = _apply_todo_for_outcome(
            todo_items, call, obs, outcome_status, empty=_is_empty_tool_result(obs)
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call.call_id,
                "content": json.dumps(obs, ensure_ascii=False)[:_OBSERVE_TEXT_MAX],
            }
        )
        emit_event(
            "tool_call",
            {
                "tool_name": call.name,
                "status": outcome_status,
                "args_digest": _digest(call),
                "result_digest": str(obs.get("reason") or _result_digest(obs))[:300],
            },
        )
    out["messages"] = messages
    out["executed_sigs"] = sorted(executed_sigs)
    out["compute_values"] = compute_values
    out["todos"] = todo_mod.todos_as_dicts(todo_items)
    return out


# ── 图内确认点(任务通道;add-graph-checkpoint,HITL 第三形态)──
def confirm_gate(state: ReactState) -> dict[str, object]:
    """到达确认点即挂图:interrupt 抛出 → 检查点已落控制面,编排上报挂起。
    重入(批准/拒绝后 Command(resume) 恢复)时本节点从头重放——前置**零
    副作用**(只读在途确认),interrupt() 直接返回决策值,不重发工具调用。"""
    inflight = state.get("confirm_inflight") or {}
    if not inflight:
        return {}  # 不可达(路由闸保证);防御空转
    decision = interrupt(
        {
            "kind": "tool_confirm",
            "confirm_id": str(inflight.get("confirm_id") or ""),
            "tool_name": str(inflight.get("tool_name") or ""),
            "summary": str(inflight.get("summary") or ""),
        }
    )
    return {"confirm_decision": decision if isinstance(decision, dict) else {"raw": decision}}


async def tool_resolve(state: ReactState) -> dict[str, object]:
    """确认兑现:任务域决策 → 内部 resolve-confirm 回调(批准=全量复校后执行,
    拒绝/过期=如实拒绝观察);观察以原 tool_call_id 喂回 LLM(恢复循环),
    在途确认清账。控制面不可达=兑现失败如实入观察(不虚报已执行)。"""
    inflight = state.get("confirm_inflight") or {}
    decision = state.get("confirm_decision") or {}
    outcome = await deps.get_api().tool_resolve_confirm(
        tenant_key=str(state.get("tenant_key") or ""),
        user_id=state.get("user_id"),
        confirm_id=str(inflight.get("confirm_id") or ""),
        decision="approve" if decision.get("decision") == "approved" else "reject",
    )
    if outcome is None:
        obs: dict[str, object] = {"status": "error", "reason": "控制面不可达: 确认兑现失败"}
    else:
        status = str(outcome.get("status") or "error")
        if status == "executed":
            result = outcome.get("result")
            obs = {
                "status": "executed",
                "result": result if isinstance(result, dict) else {},
            }
        else:
            obs = {
                "status": status,
                "reason": str(outcome.get("reason") or "确认未兑现"),
            }
    return {
        "messages": [
            {
                "role": "tool",
                "tool_call_id": str(inflight.get("call_id") or ""),
                "content": json.dumps(obs, ensure_ascii=False)[:_OBSERVE_TEXT_MAX],
            }
        ],
        "confirm_inflight": None,
        "confirm_decision": None,
    }


def _numeric_cells(execution: object) -> list[float]:
    if not isinstance(execution, ExecutionResult):
        return []
    out: list[float] = []
    for row in execution.rows:
        for cell in row:
            if isinstance(cell, (int, float)) and not isinstance(cell, bool):
                out.append(float(cell))
    return out


def _todo_id_of(call: LlmToolCall) -> str | None:
    args = call.args or {}
    tid = args.get("todo_id")
    return str(tid) if isinstance(tid, str) and tid else None


def _is_empty_tool_result(obs: dict[str, object]) -> bool:
    result = obs.get("result")
    if not isinstance(result, dict):
        return False
    typed = cast("dict[str, object]", result)
    rows = typed.get("rows")
    if isinstance(rows, list) and len(cast("list[object]", rows)) == 0:
        return True
    rc = typed.get("row_count")
    return isinstance(rc, int) and rc == 0


def _apply_todo_for_outcome(
    todo_items: list[todo_mod.TodoItem],
    call: LlmToolCall,
    obs: dict[str, object],
    outcome_status: str,
    *,
    empty: bool = False,
) -> list[todo_mod.TodoItem]:
    """工具结果 → 账本回写(058):只有真成功且非空才 DONE。"""
    if not todo_items:
        return todo_items
    success = outcome_status == "done" and str(obs.get("status") or "") == "executed"
    reason = str(obs.get("reason") or "")
    verdict = ""
    result = obs.get("result")
    if isinstance(result, dict):
        verdict = str(cast("dict[str, object]", result).get("verdict") or "")
    if verdict in ("BLOCKED", "ERROR"):
        success = False
        reason = reason or verdict
    not_applicable = "不适用" in reason or "NO_RELEVANT" in reason
    return todo_mod.apply_tool_result(
        todo_items,
        success=success,
        evidence=todo_mod.evidence_digest(call.call_id, call.name, success),
        empty=empty and success,
        not_applicable=not_applicable and success,
        reason=reason,
        todo_id=_todo_id_of(call),
    )


def _turn_whitelist(
    state: ReactState,
    compute_values: list[float],
    batch_out: dict[str, object] | None = None,
) -> list[float]:
    """本轮数值白名单:结果集数值 ∪ stats ∪ compute 返回 ∪ 结构性常量。

    batch_out 非空时优先取本批 tool_dispatch 刚捕获的 execute_sql 结果
    (LLM 同批下「查数+计算」是常态,不能只看上轮 state)。
    """
    execution: ExecutionResult | None = None
    if batch_out is not None:
        batch_exec = batch_out.get("last_execution")
        if isinstance(batch_exec, ExecutionResult):
            execution = batch_exec
    if execution is None:
        state_exec = state.get("last_execution")
        if isinstance(state_exec, ExecutionResult):
            execution = state_exec
    rows = execution.rows if execution is not None else []
    columns = execution.columns if execution is not None else []
    stats = compute_stats(columns, rows) if rows else {}
    n_rows = len(rows)
    return numeric.build_whitelist(
        columns=columns,
        rows=rows,
        stats=stats,
        compute_values=compute_values,
        structural=[float(n_rows), float(numeric.PREVIEW_ROWS)],
    )


def _as_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise TypeError(f"参数须为数值: {value!r}")
    return float(value)


def _run_local_compute(
    call: LlmToolCall,
    state: ReactState,
    compute_values: list[float],
    batch_out: dict[str, object] | None = None,
) -> tuple[dict[str, object], str, list[float]]:
    """compute_delta/compute_share 本地确定性执行(057)。

    入参必须 ∈ 本轮白名单(结果/stats/既有计算值),否则拒绝并给纠正指引。
    """
    args = call.args or {}
    wl = _turn_whitelist(state, compute_values, batch_out)
    try:
        if call.name == "compute_delta":
            result = numeric.compute_delta(
                _as_float(args["base"]), _as_float(args["target"]), whitelist=wl
            )
        else:
            result = numeric.compute_share(
                _as_float(args["part"]), _as_float(args["total"]), whitelist=wl
            )
    except (KeyError, TypeError, ValueError) as e:
        return (
            {"status": "error", "reason": f"计算参数无效,请传入数字: {e}"},
            "rejected",
            [],
        )
    if result.get("status") != "executed":
        return (
            {"status": "error", "reason": str(result.get("reason") or "计算失败")},
            "rejected",
            [],
        )
    return (
        {"status": "executed", "result": result},
        "done",
        numeric.compute_result_values(result),
    )


def _digest(call: LlmToolCall) -> str:
    return json.dumps(call.args, ensure_ascii=False, default=str)[:300] if call.args else ""


def _result_digest(obs: dict[str, object]) -> str:
    result = obs.get("result")
    if not isinstance(result, dict):
        return str(obs.get("status") or "")
    # isinstance 收窄产物键型仍 Unknown,守卫后 cast 再用
    typed = cast("dict[str, object]", result)
    if "row_count" in typed:
        return f"查询完成,共 {typed.get('row_count')} 行"
    if "announcement_id" in typed:
        return "公告已发布" if typed.get("created") else "公告已存在(幂等)"
    return str(obs.get("status") or "")


def _observe(outcome: ToolExecuteOutcome) -> dict[str, object]:
    """工具结果 → 喂 LLM 的观察(截断);confirm_required 引导终止循环。"""
    if outcome.status == "confirm_required":
        return {
            "status": "confirm_required",
            "confirm_id": outcome.confirm_id,
            "reason": "该操作需用户确认,已挂起。请停止后续工具调用,"
            "直接向用户说明有待确认的事项及其内容。",
        }
    if outcome.status != "executed":
        return {"status": outcome.status, "reason": outcome.reason or outcome.status}
    result = outcome.result or {}
    # execute_sql 大结果集截断(rows 只喂前 N 行)
    rows_val = result.get("rows")
    if isinstance(rows_val, list):
        # isinstance 收窄产物元素型仍 Unknown,cast 后再切(运行时同一对象)
        rows_typed = cast("list[object]", rows_val)
        trimmed = {
            **result,
            "rows": rows_typed[:MAX_OBSERVE_ROWS],
            "rows_truncated": len(rows_typed) > MAX_OBSERVE_ROWS,
        }
        return {"status": "executed", "result": trimmed}
    return {"status": "executed", "result": result}


def _capture_execution(
    out: dict[str, object], call: LlmToolCall, outcome: ToolExecuteOutcome
) -> None:
    """最后一次 execute_sql 结果与 SQL 留终态(verdict 含 BLOCKED 语义)。"""
    result = outcome.result or {}
    reason = result.get("reason")
    columns = result.get("columns")
    rows = result.get("rows")
    duration = result.get("duration_ms")
    out["last_execution"] = ExecutionResult(
        verdict=str(result.get("verdict") or "ERROR"),
        reason=reason if isinstance(reason, str) else None,
        # isinstance 收窄产物元素型仍 Unknown,cast 收窄(cast 运行时恒等)
        columns=(
            [str(c) for c in cast("list[object]", columns)] if isinstance(columns, list) else []
        ),
        rows=[
            list(cast("list[object]", r)) for r in cast("list[object]", rows) if isinstance(r, list)
        ]
        if isinstance(rows, list)
        else [],
        duration_ms=int(duration) if isinstance(duration, (int, float)) else 0,
    )
    if call.args:
        out["last_sql"] = str(call.args.get("sql") or "")


# ── 节点3:终态构造(与 _build_query_out 字段契约同构)──
def finalize(state: ReactState) -> dict[str, object]:
    execution = state.get("last_execution")
    rounds = state.get("rounds", 0)
    # 轮次上限触顶(R1-S6:dispatch 后直接收束):未产出最终答案且轮次已满
    # → 强制终止结构化错误(spec)。早前轮次已成功的执行结果随终态携带
    # (error 优先呈现,rows 作参考;不再调 LLM 摘要——触顶不该再花一次
    # 模型调用),部分结果不丢弃(A-B6)。
    if state.get("final_answer") is None and rounds >= Settings().max_tool_rounds:
        # 058:预算退出不得批量改 DONE;未完成清单随错误文案披露
        todo_items = todo_mod.mark_pending_on_budget_exit(
            todo_mod.todos_from_dicts(state.get("todos") or [])
        )
        err = "已达工具调用轮次上限,请缩小问题范围后重试"
        if todo_items and todo_mod.has_open_items(todo_items):
            err = f"{err};{todo_mod.render_open_disclosure(todo_items)}"
        capped: dict[str, object] = {
            "error": err,
            "retry_count": rounds,
            "todos": todo_mod.todos_as_dicts(todo_items),
        }
        if isinstance(execution, ExecutionResult):
            capped["execution"] = execution
            capped["summary"] = ""
            capped["stats"] = compute_stats(execution.columns, execution.rows)
            if last_sql := state.get("last_sql"):
                capped["generated_sql"] = last_sql
        return capped
    if execution is None:
        # 无 SQL 执行的纯文本回答:成功终态,空结果集
        execution = ExecutionResult(verdict="PASS", columns=[], rows=[])
    stats = compute_stats(execution.columns, execution.rows)
    # 057:finalize 同口径数值审计(与 summarize 共用纯函数;不重试 LLM)。
    # 仅对有结果集/列的数据收口审计——纯文本(无执行)无数可审,原文通过
    # (否则「共有 4 件」等非结果数字会被整句剥空)。
    compute_values = state.get("compute_values") or []
    raw_answer = state.get("final_answer") or "查询无结果。"
    has_data = bool(execution.columns or execution.rows)
    if has_data:
        audited = numeric.audit_summarize_output(
            raw_answer,
            [],
            columns=execution.columns,
            rows=execution.rows,
            stats=stats,
            compute_values=compute_values,
            structural=[float(len(execution.rows)), float(numeric.PREVIEW_ROWS)],
        )
        # 审计剥离后为空时不得回退未审原文(会把非法数字原样放行);与 summarize
        # 同口径走确定性 stats 兜底
        summary: str = audited.summary or fallback_summary(stats)
    else:
        summary = raw_answer
    out: dict[str, object] = {
        "execution": execution,
        "summary": summary,
        "stats": stats,
        "retry_count": rounds,
        "compute_values": compute_values,
    }
    # 058:收口与账本一致(禁全称完成,披露未完成);无账本/全 DONE 零回归
    todo_items = todo_mod.mark_pending_on_budget_exit(
        todo_mod.todos_from_dicts(state.get("todos") or [])
    )
    if todo_items:
        summary, _viol = todo_mod.assert_finalize_honest(todo_items, str(out["summary"]))
        out["summary"] = summary
        out["todos"] = todo_mod.todos_as_dicts(todo_items)
    if last_sql := state.get("last_sql"):
        out["generated_sql"] = last_sql
    return out
