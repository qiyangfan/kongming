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

from kongming_agent import deps
from kongming_agent.apiclient import ToolExecuteOutcome
from kongming_agent.graph.nodes import compute_stats
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
                "content": initial_user_prompt(
                    state["question"],
                    state.get("history", [])[-5:],
                    state.get("memory_hint", ""),
                ),
            }
        ]
    system_prompt = build_react_system(state.get("bot_system_prompt") or "")
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
    out: dict[str, object] = {"rounds": rounds, "pending_tool_calls": []}
    for call in calls:
        emit_event(
            "tool_call",
            {"tool_name": call.name, "status": "running", "args_digest": _digest(call)},
        )
        sig = ""
        if call.args is not None:
            sig = call.name + "|" + json.dumps(
                call.args, ensure_ascii=False, sort_keys=True, default=str
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
        else:
            outcome = await deps.get_api().tool_execute(
                trace_id=state["trace_id"],
                tenant_key=state["tenant_key"],
                datasource_key=state.get("datasource_key", "default"),
                user_id=state.get("user_id"),
                tool_name=call.name,
                args=call.args,
                channel=state.get("channel", "chat"),  # 025
            )
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
    return out


def _digest(call: LlmToolCall) -> str:
    return json.dumps(call.args, ensure_ascii=False, default=str)[:300] if call.args else ""


def _result_digest(obs: dict[str, object]) -> str:
    result = obs.get("result")
    if not isinstance(result, dict):
        return str(obs.get("status") or "")
    if "row_count" in result:
        return f"查询完成,共 {result.get('row_count')} 行"
    if "announcement_id" in result:
        return "公告已发布" if result.get("created") else "公告已存在(幂等)"
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
        trimmed = {
            **result,
            "rows": rows_val[:MAX_OBSERVE_ROWS],
            "rows_truncated": len(rows_val) > MAX_OBSERVE_ROWS,
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
        columns=[str(c) for c in columns] if isinstance(columns, list) else [],
        rows=[list(r) for r in rows if isinstance(r, list)] if isinstance(rows, list) else [],
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
        capped: dict[str, object] = {
            "error": "已达工具调用轮次上限,请缩小问题范围后重试",
            "retry_count": rounds,
        }
        if isinstance(execution, ExecutionResult):
            capped["execution"] = execution
            capped["summary"] = ""
            capped["stats"] = compute_stats(execution.columns, execution.rows)
            if state.get("last_sql"):
                capped["generated_sql"] = state["last_sql"]
        return capped
    if execution is None:
        # 无 SQL 执行的纯文本回答:成功终态,空结果集
        execution = ExecutionResult(verdict="PASS", columns=[], rows=[])
    stats = compute_stats(execution.columns, execution.rows)
    out: dict[str, object] = {
        "execution": execution,
        "summary": state.get("final_answer") or "查询无结果。",
        "stats": stats,
        "retry_count": rounds,
    }
    if state.get("last_sql"):
        out["generated_sql"] = state["last_sql"]
    return out
