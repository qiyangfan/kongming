"""流式问答端点测试(017 query-streaming):事件序列/终态同构/错误收敛。

契约:nl2sql-agent spec「流式事件发射」「流式问答端点」+
query-streaming spec「SSE 事件协议」。
"""

import json

import pytest
from httpx import ASGITransport, AsyncClient

from kongming_agent import deps
from kongming_agent.apiclient import (
    MetaColumn,
    MetaTable,
    RagContext,
    SchemaMeta,
    SqlExecuteOutcome,
)
from kongming_agent.llm import LlmError
from kongming_agent.main import app

TOKEN = {"X-Internal-Token": "test-internal-token"}


class _Llm:
    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)

    async def chat(self, system: str, user: str, *, temperature: float = 0.2) -> str:
        if not self.answers:
            raise LlmError("no scripted answer")
        return self.answers.pop(0)


class _Api:
    def __init__(self, outcome: SqlExecuteOutcome | None = None) -> None:
        self.outcome = outcome or SqlExecuteOutcome(
            verdict="PASS", columns=["qty"], rows=[[30]]
        )

    async def rag_retrieve(self, **kw: object) -> RagContext | None:
        return None

    async def fetch_schema(self, **kw: object) -> SchemaMeta:
        return SchemaMeta(
            tables=[
                MetaTable(
                    schema="tenant_default",
                    name="goods",
                    comment="",
                    columns=[MetaColumn(name="qty", data_type="int", comment="")],
                )
            ],
            truncated=False,
        )

    async def sql_execute(self, **kw: object) -> SqlExecuteOutcome:
        return self.outcome


def _client(
    monkeypatch: pytest.MonkeyPatch, llm: _Llm, api: _Api | None = None
) -> AsyncClient:
    monkeypatch.setattr(deps, "get_llm", lambda: llm)
    monkeypatch.setattr(deps, "get_api", lambda: (api or _Api()))
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _parse_sse(text: str) -> list[tuple[str, dict[str, object]]]:
    """SSE 响应体 → [(event, data)] 帧序列(与 api 侧解析同构的最小版)。"""
    frames: list[tuple[str, dict[str, object]]] = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block or block.startswith(":"):
            continue
        event = ""
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].strip())
        if event:
            frames.append((event, json.loads("\n".join(data_lines))))
    return frames


REQ = {
    "trace_id": "t-stream",
    "tenant_key": "default",
    "question": "商品总库存是多少",
    "history": [],
}


async def test_stream_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _client(monkeypatch, _Llm([])) as c:
        resp = await c.post("/query/stream", json=REQ)
    assert resp.status_code == 403


async def test_stream_happy_path_event_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """成功链路:stage 成对 → sql → summary → result 唯一终态;
    result data 与同步 /query 响应逐字段一致(终态一致性直证)。"""
    llm = _Llm(["商品总库存", "SELECT sum(qty) FROM goods", '{"summary":"共 30"}'])
    llm_sync = _Llm(["商品总库存", "SELECT sum(qty) FROM goods", '{"summary":"共 30"}'])
    async with _client(monkeypatch, llm) as c:
        stream_resp = await c.post("/query/stream", json=REQ, headers=TOKEN)
    async with _client(monkeypatch, llm_sync) as c:
        sync_resp = await c.post("/query", json=REQ, headers=TOKEN)
    assert stream_resp.status_code == 200
    assert stream_resp.headers["content-type"].startswith("text/event-stream")
    assert stream_resp.headers["cache-control"] == "no-cache"
    assert stream_resp.headers["x-accel-buffering"] == "no"
    frames = _parse_sse(stream_resp.text)

    events = [e for e, _ in frames]
    # 各节点 running/done 成对,sql 在 validate done 之后、result 之前
    assert events[0] == ("stage") and frames[0][1]["node"] == "understand"
    assert events[-1] == "result"
    assert events.count("result") == 1
    assert "sql" in events and "summary" in events
    sql_idx = events.index("sql")
    validate_done_idx = next(
        i
        for i, (e, d) in enumerate(frames)
        if e == "stage" and d.get("node") == "validate" and d.get("status") == "done"
    )
    assert sql_idx == validate_done_idx + 1
    # 每节点恰好 running/done 各一次
    for node in ("understand", "retrieve", "generate", "validate", "execute", "summarize"):
        stages = [d for e, d in frames if e == "stage" and d.get("node") == node]
        assert [s.get("status") for s in stages] == ["running", "done"], node
    # sql 事件内容=生成的 SQL;summary 事件=完整文案
    sql_frame = frames[sql_idx][1]
    assert sql_frame["sql"] == "SELECT sum(qty) FROM goods"
    summary_frame = frames[events.index("summary")][1]
    assert summary_frame["delta"] == "共 30"
    # 终态一致性:result data 与同步响应逐字段一致
    result_data = frames[-1][1]
    assert result_data == sync_resp.json()


async def test_stream_stage_running_before_done(monkeypatch: pytest.MonkeyPatch) -> None:
    """stage 事件序:每个节点的 running 先于 done(帧序即节点边界)。"""
    llm = _Llm(["商品总库存", "SELECT sum(qty) FROM goods", '{"summary":"共 30"}'])
    async with _client(monkeypatch, llm) as c:
        resp = await c.post("/query/stream", json=REQ, headers=TOKEN)
    frames = _parse_sse(resp.text)
    seen_running: set[str] = set()
    for e, d in frames:
        if e != "stage":
            continue
        node, status = str(d["node"]), str(d["status"])
        if status == "running":
            assert node not in seen_running  # 同节点不重复 running
            seen_running.add(node)
        else:
            assert node in seen_running  # done 前必有 running
            seen_running.discard(node)


async def test_stream_validate_rejected_no_sql_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """validate 未通过(语法类失败)不发 sql 事件;权限类直接终态。
    语法失败走 selfheal→重试耗尽,sql 在重试成功时才发。"""
    # 第一轮坏 SQL(PARSE_ERROR 进 selfheal),第二轮修正
    llm = _Llm(
        [
            "商品总库存",
            "SELECT sum(qty FROM goods",  # 未闭合括号 → PARSE_ERROR
            "SELECT sum(qty) FROM goods",
            '{"summary":"共 30"}',
        ]
    )
    async with _client(monkeypatch, llm) as c:
        resp = await c.post("/query/stream", json=REQ, headers=TOKEN)
    frames = _parse_sse(resp.text)
    events = [e for e, _ in frames]
    # selfheal stage 出现且携带重试序号 1
    selfheal_done = next(
        d
        for e, d in frames
        if e == "stage" and d.get("node") == "selfheal" and d.get("status") == "done"
    )
    assert selfheal_done["retry_count"] == 1
    # 第一次 validate 失败后无 sql;第二次通过后恰一条 sql
    validate_dones = [
        i
        for i, (e, d) in enumerate(frames)
        if e == "stage" and d.get("node") == "validate" and d.get("status") == "done"
    ]
    sql_between_first = any(
        e == "sql" for e, _ in frames[validate_dones[0] : validate_dones[1]]
    )
    assert not sql_between_first
    assert events.count("sql") == 1
    # 终态 result 成功(重试一次后 PASS)
    assert frames[-1][1]["success"] is True
    assert frames[-1][1]["retry_count"] == 1


async def test_stream_permission_block_no_sql_no_selfheal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """权限类拦截:无 sql 事件、无 selfheal stage,终态 verdict=BLOCKED。"""
    llm = _Llm(["删掉商品", "DELETE FROM goods"])
    async with _client(monkeypatch, llm) as c:
        resp = await c.post("/query/stream", json=REQ, headers=TOKEN)
    frames = _parse_sse(resp.text)
    events = [e for e, _ in frames]
    assert "sql" not in events
    assert all(
        not (e == "stage" and d.get("node") == "selfheal") for e, d in frames
    )
    assert events[-1] == "result"
    assert frames[-1][1]["verdict"] == "BLOCKED"
    assert frames[-1][1]["success"] is False


async def test_stream_llm_failure_result_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM 故障:与同步 /query 同口径收敛 success=false 的 result 终态
    (不走 error 事件——业务/服务降级非传输层失败)。"""
    async with _client(monkeypatch, _Llm([])) as c:
        resp = await c.post("/query/stream", json=REQ, headers=TOKEN)
    frames = _parse_sse(resp.text)
    assert [e for e, _ in frames].count("error") == 0
    result = frames[-1][1]
    assert result["success"] is False
    assert result["error"] == "LLM 服务暂不可用,请稍后重试"


async def test_stream_empty_result_summary_still_emitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """执行成功但零行:summarize 兜底「查询无结果。」仍以 summary 事件下发。"""
    llm = _Llm(["商品总库存", "SELECT sum(qty) FROM goods WHERE 1=0", '{"summary":""}'])
    api = _Api(outcome=SqlExecuteOutcome(verdict="PASS", columns=["qty"], rows=[]))
    async with _client(monkeypatch, llm, api) as c:
        resp = await c.post("/query/stream", json=REQ, headers=TOKEN)
    frames = _parse_sse(resp.text)
    events = [e for e, _ in frames]
    assert "summary" in events
    summary_idx = events.index("summary")
    assert frames[summary_idx][1]["delta"] == "查询无结果。"


class _BoomLlm:
    """非 LlmError 的意外异常(评审 R3:图意外异常收敛 error 终态)。"""

    async def chat(self, system: str, user: str, *, temperature: float = 0.2) -> str:
        raise RuntimeError("unexpected boom")


async def test_stream_unexpected_exception_error_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """图执行意外异常(非 LlmError):收敛 error 终态事件,不裸 500(R3)。"""
    monkeypatch.setattr(deps, "get_llm", lambda: _BoomLlm())
    monkeypatch.setattr(deps, "get_api", lambda: _Api())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/query/stream", json=REQ, headers=TOKEN)
    assert resp.status_code == 200
    frames = _parse_sse(resp.text)
    assert frames[-1][0] == "error"
    assert frames[-1][1]["error"] == "问答服务内部错误,请稍后重试"
