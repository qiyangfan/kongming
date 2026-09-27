"""图全链路测试:scripted LLM + scripted 控制面回调,四条主路径。

- 首轮 PASS:understand→retrieve→generate→validate→execute→summarize 走通;
- ERROR→selfheal→PASS:重试经 selfheal 计数,错误喂回 generate;
- BLOCKED→fail:权限类不重试,LLM 调用次数不增;
- 重试耗尽→fail:图必然终止(不依赖 recursion_limit)。
"""

from collections.abc import Callable
from typing import Any, cast

import pytest

from kongming_agent import deps
from kongming_agent.apiclient import (
    MetaColumn,
    MetaTable,
    RagContext,
    SchemaMeta,
    SqlExecuteOutcome,
)
from kongming_agent.graph.build import MAX_RETRY, get_graph
from kongming_agent.graph.state import AgentState

_NO_DATA_ERROR = "当前数据源未找到回答该问题所需的数据,可切换数据源或调整问法后重试"


class ScriptedLlm:
    """按调用序出答案:understand/generate(/selfheal 后再 generate)/summarize。"""

    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.users: list[str] = []

    async def chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.2,
        **_kw: object,  # 021:task/trace_id/tenant_key 吸收
    ) -> str:
        self.users.append(user)
        if not self.answers:
            raise AssertionError("LLM 调用次数超出脚本——出现计划外重试")
        return self.answers.pop(0)


class ScriptedApi:
    def __init__(self, outcomes: list[SqlExecuteOutcome]) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0
        self.rag_calls = 0

    async def rag_retrieve(self, **kw: object) -> RagContext | None:
        self.rag_calls += 1
        return None  # 零命中回退内省(009 回归路径)

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


# 测试夹具形态:按脚本装配 deps 的替身工厂(每用例一组 LLM 应答+执行结果)
Harness = Callable[[list[str], list[SqlExecuteOutcome]], tuple[ScriptedLlm, ScriptedApi]]


async def _invoke(state: AgentState) -> dict[str, Any]:
    """get_graph().ainvoke 成员型 strict 下部分未知(LangGraph 泛型),
    测试边界一次收窄为终态 state dict(自构造 state,键型已知)。"""
    graph = cast("Any", get_graph())
    return cast("dict[str, Any]", await graph.ainvoke(state))


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> Harness:
    def _make(
        llm_answers: list[str], api_outcomes: list[SqlExecuteOutcome]
    ) -> tuple[ScriptedLlm, ScriptedApi]:
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


async def test_first_round_pass(harness: Harness) -> None:
    llm, api = harness(
        ["商品总库存", "SELECT sum(qty) FROM goods", '{"summary":"共 30"}'],
        [SqlExecuteOutcome(verdict="PASS", columns=["sum"], rows=[[30]])],
    )
    final = await _invoke(_initial())
    assert final["execution"].verdict == "PASS"
    assert final["summary"] == "共 30"
    assert final["retry_count"] == 0
    assert api.calls == 1
    assert len(llm.users) == 3  # understand/generate/summarize 各一次
    assert "tenant_default.goods" in llm.users[1]  # schema 提示拼入 generate


async def test_error_selfheal_then_pass(harness: Harness) -> None:
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
    final = await _invoke(_initial())
    assert final["execution"].verdict == "PASS"
    assert final["retry_count"] == 1
    assert api.calls == 2
    # selfheal 把执行错误喂回了第二次 generate
    assert "column qtyy does not exist" in llm.users[2]


async def test_blocked_never_retries(harness: Harness) -> None:
    llm, api = harness(
        ["删除商品", "DELETE FROM goods"],  # validate 本地即拦,execute 根本不会被调
        [],
    )
    final = await _invoke(_initial())
    assert final["end_reason"] == "blocked"
    assert final["retry_count"] == 0  # 权限类不耗配额
    assert api.calls == 0
    assert len(llm.users) == 2  # understand + generate,summarize 不跑


async def test_sentinel_short_circuit_zero_execution(harness: Harness) -> None:
    """043:知识未覆盖哨兵短路——零执行零重试,失败终态带用户友好文案。"""
    llm, api = harness(["员工总数", "NO_RELEVANT_TABLE"], [])
    final = await _invoke(_initial())
    assert final["end_reason"] == "no_data"
    assert final["generated_sql"] == ""
    assert final["error"] == _NO_DATA_ERROR
    assert api.calls == 0  # 短路前零执行(sys_sql_audit 零新增记录)
    assert len(llm.users) == 2  # understand + generate;无重试、无 summarize
    assert "summary" not in final  # 失败路径不出洞察


async def test_sentinel_after_does_not_exist_guides_then_terminates(
    harness: Harness,
) -> None:
    """043:首轮编表执行报 does-not-exist → 喂回附清单引导 → 二轮哨兵短路不再连败。"""
    llm, api = harness(
        ["员工总数", "SELECT count(*) FROM staff"],  # 首轮凭先验编表
        [SqlExecuteOutcome(verdict="ERROR", reason='relation "public.staff" does not exist')],
    )
    llm.answers.insert(2, "NO_RELEVANT_TABLE")  # 二轮经引导后如实说明
    final = await _invoke(_initial())
    assert final["end_reason"] == "no_data"
    assert api.calls == 1  # 二轮短路,零新增执行
    assert "所引用的表或列不在『可用表结构』清单中" in llm.users[2]  # 喂回引导在位
    assert final["error"] == _NO_DATA_ERROR
    assert final["retry_count"] == 1  # 仅首轮后的那一次 selfheal 计数
    assert "summary" not in final


async def test_retry_exhaustion_terminates(harness: Harness) -> None:
    _, api = harness(
        ["商品总库存"] + ["SELECT sum(qtyy) FROM goods"] * (MAX_RETRY + 1),
        [SqlExecuteOutcome(verdict="ERROR", reason="column qtyy does not exist")] * (MAX_RETRY + 1),
    )
    final = await _invoke(_initial())
    assert final["execution"].verdict == "ERROR"
    assert final["retry_count"] == MAX_RETRY  # 每轮经 selfheal 计数,耗尽即终止
    assert api.calls == MAX_RETRY + 1
    assert "summary" not in final  # 失败路径不出洞察


# ---- 071:对话复语直通(零模型/零检索/零执行,第四态收敛)----


async def test_dialogue_reply_passthrough_zero_calls(harness: Harness) -> None:
    """071:复语「不用了」直通——不发起补全、不检索、不生成、不执行。"""
    llm, api = harness([], [])  # 脚本为空:任何 LLM/execute 调用即炸
    state = _initial()
    state["question"] = "不用了"
    state["history"] = ["有多少商品"]
    final = await _invoke(state)
    assert final["end_reason"] == "no_data"  # 既有第四态,不新增终态取值
    assert final["error"] == _NO_DATA_ERROR
    assert llm.users == []  # UNDERSTAND/SQL_GEN/SUMMARIZE 全未发起
    assert api.calls == 0  # 零执行(sys_sql_audit 零新增记录)
    assert api.rag_calls == 0  # 零检索回调
    assert "generated_sql" not in final or final.get("generated_sql", "") == ""
    assert "summary" not in final


async def test_dialogue_reply_greeting_first_turn(harness: Harness) -> None:
    """071:单轮无历史首句「你好」同样直通。"""
    llm, api = harness([], [])
    state = _initial()
    state["question"] = "你好"
    state["history"] = []
    final = await _invoke(state)
    assert final["end_reason"] == "no_data"
    assert llm.users == []
    assert api.calls == 0


async def test_dialogue_reply_not_blocked_by_prefix_query(harness: Harness) -> None:
    """071:复语起头的真实问法「取消订单的门店有哪些」照常走数据查询路径。"""
    llm, api = harness(
        [
            "取消订单的门店有哪些",
            "SELECT store FROM orders WHERE status='cancelled'",
            '{"summary":"1 家"}',
        ],
        [SqlExecuteOutcome(verdict="PASS", columns=["store"], rows=[["S1"]])],
    )
    state = _initial()
    state["question"] = "取消订单的门店有哪些"
    final = await _invoke(state)
    assert final.get("end_reason") != "no_data"
    assert len(llm.users) >= 2  # understand + generate 正常发起
    assert api.calls == 1  # 照常执行
