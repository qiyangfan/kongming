"""图全链路测试:scripted LLM + scripted 控制面回调,四条主路径。

- 首轮 PASS:understand→retrieve→generate→validate→execute→summarize 走通;
- ERROR→selfheal→PASS:重试经 selfheal 计数,错误喂回 generate;
- BLOCKED→fail:权限类不重试,LLM 调用次数不增;
- 重试耗尽→fail:图必然终止(不依赖 recursion_limit)。
"""

import pytest

from kongming_agent import deps
from kongming_agent.apiclient import MetaColumn, MetaTable, SchemaMeta, SqlExecuteOutcome
from kongming_agent.graph.build import MAX_RETRY, get_graph
from kongming_agent.graph.state import AgentState


class ScriptedLlm:
    """按调用序出答案:understand/generate(/selfheal 后再 generate)/summarize。"""

    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.users: list[str] = []

    async def chat(self, system: str, user: str, *, temperature: float = 0.2) -> str:
        self.users.append(user)
        if not self.answers:
            raise AssertionError("LLM 调用次数超出脚本——出现计划外重试")
        return self.answers.pop(0)


class ScriptedApi:
    def __init__(self, outcomes: list[SqlExecuteOutcome]) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    async def fetch_schema(self, **kw: object) -> SchemaMeta:
        return SchemaMeta(
            tables=[
                MetaTable(
                    schema="tenant_default",
                    name="goods",
                    comment="商品表",
                    columns=[MetaColumn(name="qty", data_type="int", comment="库存")],
                )
            ],
            truncated=False,
        )

    async def sql_execute(self, **kw: object) -> SqlExecuteOutcome:
        self.calls += 1
        if not self.outcomes:
            raise AssertionError("execute 调用次数超出脚本")
        return self.outcomes.pop(0)


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    def _make(llm_answers: list[str], api_outcomes: list[SqlExecuteOutcome]):  # type: ignore[no-untyped-def]
        llm = ScriptedLlm(llm_answers)
        api = ScriptedApi(api_outcomes)
        monkeypatch.setattr(deps, "get_llm", lambda: llm)
        monkeypatch.setattr(deps, "get_api", lambda: api)
        return llm, api

    return _make


def _initial() -> AgentState:
    return {
        "trace_id": "t-chain",
        "tenant_key": "default",
        "question": "商品总库存是多少",
        "user_id": None,
        "history": [],
        "datasource_key": "default",
        "retry_count": 0,
    }


async def test_first_round_pass(harness) -> None:  # type: ignore[no-untyped-def]
    llm, api = harness(
        ["商品总库存", "SELECT sum(qty) FROM goods", '{"summary":"共 30"}'],
        [SqlExecuteOutcome(verdict="PASS", columns=["sum"], rows=[[30]])],
    )
    final = await get_graph().ainvoke(_initial())
    assert final["execution"].verdict == "PASS"
    assert final["summary"] == "共 30"
    assert final["retry_count"] == 0
    assert api.calls == 1
    assert len(llm.users) == 3  # understand/generate/summarize 各一次
    assert "tenant_default.goods" in llm.users[1]  # schema 提示拼入 generate


async def test_error_selfheal_then_pass(harness) -> None:  # type: ignore[no-untyped-def]
    llm, api = harness(
        [
            "商品总库存",
            "SELECT sum(qtyy) FROM goods",  # 首轮错列名
            '{"summary":"共 30"}',
        ],
        [
            SqlExecuteOutcome(verdict="ERROR", reason="column qtyy does not exist"),
            SqlExecuteOutcome(verdict="PASS", columns=["sum"], rows=[[30]]),
        ],
    )
    # 第二次 generate 由 selfheal 喂回错误后产生正确 SQL——脚本第二位让给 selfheal 后的 generate
    llm.answers.insert(2, "SELECT sum(qty) FROM goods")
    final = await get_graph().ainvoke(_initial())
    assert final["execution"].verdict == "PASS"
    assert final["retry_count"] == 1
    assert api.calls == 2
    # selfheal 把执行错误喂回了第二次 generate
    assert "column qtyy does not exist" in llm.users[2]


async def test_blocked_never_retries(harness) -> None:  # type: ignore[no-untyped-def]
    llm, api = harness(
        ["删除商品", "DELETE FROM goods"],  # validate 本地即拦,execute 根本不会被调
        [],
    )
    final = await get_graph().ainvoke(_initial())
    assert final["end_reason"] == "blocked"
    assert final["retry_count"] == 0  # 权限类不耗配额
    assert api.calls == 0
    assert len(llm.users) == 2  # understand + generate,summarize 不跑


async def test_retry_exhaustion_terminates(harness) -> None:  # type: ignore[no-untyped-def]
    llm, api = harness(
        ["商品总库存"] + ["SELECT sum(qtyy) FROM goods"] * (MAX_RETRY + 1),
        [SqlExecuteOutcome(verdict="ERROR", reason="column qtyy does not exist")]
        * (MAX_RETRY + 1),
    )
    final = await get_graph().ainvoke(_initial())
    assert final["execution"].verdict == "ERROR"
    assert final["retry_count"] == MAX_RETRY  # 每轮经 selfheal 计数,耗尽即终止
    assert api.calls == MAX_RETRY + 1
    assert "summary" not in final  # 失败路径不出洞察
