"""节点单测:mock LLM(deps.get_llm)+ mock 控制面回调(deps.get_api)。

覆盖(nl2sql/nl2sql-agent spec):指代消解 prompt 组装与 5 轮截断、schema 提示
拼装与降级、SQL 清洗、selfheal 错误喂回、预检四规则、三态映射、洞察降级链。
"""

import pytest

from kongming_agent import deps
from kongming_agent.apiclient import MetaColumn, MetaTable, SchemaMeta, SqlExecuteOutcome
from kongming_agent.graph import nodes
from kongming_agent.graph.state import AgentState, ExecutionResult, ValidationResult
from kongming_agent.llm import LlmError


class FakeLlm:
    """按队列脚本应答;记录全部调用供断言。"""

    def __init__(self, answers: list[str] | None = None, *, boom: bool = False) -> None:
        self.answers = list(answers or [])
        self.boom = boom
        self.calls: list[tuple[str, str, float]] = []

    async def chat(self, system: str, user: str, *, temperature: float = 0.2) -> str:
        self.calls.append((system, user, temperature))
        if self.boom:
            raise LlmError("LLM 调用失败: 模拟故障")
        return self.answers.pop(0) if self.answers else ""


class FakeApi:
    def __init__(
        self, meta: SchemaMeta | None = None, outcomes: list[SqlExecuteOutcome] | None = None
    ) -> None:
        self.meta = meta
        self.outcomes = list(outcomes or [])
        self.execute_calls: list[dict[str, object]] = []

    async def fetch_schema(self, **kw: object) -> SchemaMeta | None:
        return self.meta

    async def sql_execute(self, **kw: object) -> SqlExecuteOutcome:
        self.execute_calls.append(kw)
        if not self.outcomes:
            raise AssertionError("FakeApi.outcomes 已空,execute 不应再被调用")
        return self.outcomes.pop(0)


@pytest.fixture
def llm(monkeypatch: pytest.MonkeyPatch) -> FakeLlm:
    fake = FakeLlm()
    monkeypatch.setattr(deps, "get_llm", lambda: fake)
    return fake


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    fake = FakeApi()
    monkeypatch.setattr(deps, "get_api", lambda: fake)
    return fake


def _install(monkeypatch: pytest.MonkeyPatch, llm: FakeLlm, api: FakeApi) -> None:
    monkeypatch.setattr(deps, "get_llm", lambda: llm)
    monkeypatch.setattr(deps, "get_api", lambda: api)


def _state(**kw: object) -> AgentState:
    base: AgentState = {
        "trace_id": "t-1",
        "tenant_key": "default",
        "question": "有多少商品",
        "retry_count": 0,
    }
    base.update(kw)  # type: ignore[typeddict-item]
    return base


def _meta() -> SchemaMeta:
    return SchemaMeta(
        tables=[
            MetaTable(
                schema="tenant_default",
                name="goods",
                comment="商品表",
                columns=[
                    MetaColumn(name="id", data_type="uuid", comment=""),
                    MetaColumn(name="name", data_type="varchar", comment="名称"),
                ],
            )
        ],
        truncated=False,
    )


# ============ understand ============


async def test_understand_multi_turn_prompt_and_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeLlm(["那他总共花了多少钱"])
    _install(monkeypatch, fake, FakeApi())
    history = [f"第{i}个问题" for i in range(8)]  # 8 轮,应截断到最近 5
    out = await nodes.understand(_state(question="那他呢", history=history))
    assert out["resolved_question"] == "那他总共花了多少钱"
    system, user, _ = fake.calls[0]
    assert "指代" in system
    assert "第1轮：第3个问题" in user  # 截断后最早的是第 3 个
    assert "第5轮：第7个问题" in user
    assert "第0个问题" not in user
    assert "当前问题：那他呢" in user


async def test_understand_single_turn_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeLlm(["商品总数"])
    _install(monkeypatch, fake, FakeApi())
    out = await nodes.understand(_state(question="有多少商品"))
    assert out["resolved_question"] == "商品总数"
    assert "提取查询意图" in fake.calls[0][0]


async def test_understand_empty_output_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeLlm(["   "])
    _install(monkeypatch, fake, FakeApi())
    out = await nodes.understand(_state())
    assert out["resolved_question"] == "有多少商品"  # 空输出兜底原问句


# ============ retrieve ============


def test_format_schema_hint_full() -> None:
    hint = nodes.format_schema_hint(_meta())
    assert "表 tenant_default.goods：商品表" in hint
    assert "- id uuid" in hint
    assert "- name varchar（名称）" in hint


def test_format_schema_hint_truncated_mark() -> None:
    meta = SchemaMeta(tables=_meta().tables, truncated=True)
    assert "仅列前 1 张" in nodes.format_schema_hint(meta)


async def test_retrieve_degrades_to_empty_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, FakeLlm(), FakeApi(meta=None))
    out = await nodes.retrieve(_state())
    assert out["schema_hint"] == ""  # 内省失败降级空提示,不抛错


async def test_retrieve_formats_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, FakeLlm(), FakeApi(meta=_meta()))
    out = await nodes.retrieve(_state())
    assert "tenant_default.goods" in str(out["schema_hint"])


# ============ generate ============


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("SELECT 1", "SELECT 1"),
        ("```sql\nSELECT 1\n```", "SELECT 1"),
        ("```\nSELECT 1\n```", "SELECT 1"),
        ("  SELECT 1  ", "SELECT 1"),
        ("SQL\nSELECT 1", "SELECT 1"),
    ])
def test_clean_sql(raw: str, expected: str) -> None:
    assert nodes.clean_sql(raw) == expected


async def test_generate_appends_error_feedback(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeLlm(["SELECT count(*) FROM goods"])
    _install(monkeypatch, fake, FakeApi())
    out = await nodes.generate(
        _state(schema_hint="表 tenant_default.goods", error="column xxx does not exist")
    )
    assert out["generated_sql"] == "SELECT count(*) FROM goods"
    assert out["error"] == ""  # 错误已消费即清空
    user = fake.calls[0][1]
    assert "上次生成的 SQL 有误：column xxx does not exist" in user
    assert "可用表结构：\n表 tenant_default.goods" in user


async def test_generate_empty_hint_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeLlm(["SELECT 1"])
    _install(monkeypatch, fake, FakeApi())
    await nodes.generate(_state(schema_hint=""))
    assert "无法获取表结构" in fake.calls[0][1]  # 降级显式声明,不静默


# ============ validate ============


def test_validate_passes_select() -> None:
    out = nodes.validate(_state(generated_sql="SELECT count(*) FROM goods"))
    assert out["validation"] == ValidationResult(passed=True)


def test_validate_passes_union_and_cte() -> None:
    for sql in (
        "SELECT 1 UNION ALL SELECT 2",
        "WITH a AS (SELECT 1) SELECT * FROM a",
    ):
        out = nodes.validate(_state(generated_sql=sql))
        v = out["validation"]
        assert isinstance(v, ValidationResult) and v.passed, sql


def test_validate_dml_is_permission_block() -> None:
    out = nodes.validate(_state(generated_sql="DELETE FROM goods"))
    v = out["validation"]
    assert isinstance(v, ValidationResult)
    assert not v.passed and v.permission_block
    assert out["end_reason"] == "blocked"


def test_validate_multi_statement_retryable() -> None:
    out = nodes.validate(_state(generated_sql="SELECT 1; SELECT 2"))
    v = out["validation"]
    assert isinstance(v, ValidationResult)
    assert not v.passed and not v.permission_block  # 形态问题可重试


def test_validate_parse_error_retryable() -> None:
    out = nodes.validate(_state(generated_sql="SELEC 从不 OOM"))
    v = out["validation"]
    assert isinstance(v, ValidationResult)
    assert not v.passed and not v.permission_block


def test_validate_token_error_retryable() -> None:
    """未闭合引号触发 sqlglot TokenError(非 ParseError 子类)——也必须进 selfheal。

    回归:005 评审实证 TokenError 逃逸会穿出图变 500,语法错误零重试。
    """
    out = nodes.validate(_state(generated_sql="SELECT * FROM goods WHERE name ILIKE '%连弩"))
    v = out["validation"]
    assert isinstance(v, ValidationResult)
    assert not v.passed and not v.permission_block
    assert v.reason.startswith("PARSE_ERROR")


def test_validate_empty_retryable() -> None:
    out = nodes.validate(_state(generated_sql="  "))
    v = out["validation"]
    assert isinstance(v, ValidationResult)
    assert not v.passed and v.reason == "EMPTY_SQL"


# ============ execute ============


async def test_execute_maps_pass_and_audit_question(monkeypatch: pytest.MonkeyPatch) -> None:
    outcome = SqlExecuteOutcome(verdict="PASS", columns=["n"], rows=[[3]], duration_ms=7)
    api = FakeApi(outcomes=[outcome])
    _install(monkeypatch, FakeLlm(), api)
    out = await nodes.execute(
        _state(generated_sql="SELECT count(*) FROM goods", resolved_question="改写后问句")
    )
    exe = out["execution"]
    assert isinstance(exe, ExecutionResult) and exe.verdict == "PASS"
    assert exe.columns == ["n"] and exe.rows == [[3]]
    # 审计带原始问句(非改写后),trace/tenant 原样透传
    assert api.execute_calls[0]["question"] == "有多少商品"
    assert api.execute_calls[0]["trace_id"] == "t-1"


async def test_execute_blocked_sets_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    api = FakeApi(outcomes=[SqlExecuteOutcome(verdict="BLOCKED", reason="BLOCKED_ROOT: Delete")])
    _install(monkeypatch, FakeLlm(), api)
    out = await nodes.execute(_state(generated_sql="DELETE FROM goods"))
    assert out["end_reason"] == "blocked"
    assert out["error"] == "BLOCKED_ROOT: Delete"


async def test_execute_error_goes_state(monkeypatch: pytest.MonkeyPatch) -> None:
    api = FakeApi(outcomes=[SqlExecuteOutcome(verdict="ERROR", reason="column x 不存在")])
    _install(monkeypatch, FakeLlm(), api)
    out = await nodes.execute(_state(generated_sql="SELECT x FROM goods"))
    exe = out["execution"]
    assert isinstance(exe, ExecutionResult) and exe.verdict == "ERROR"
    assert "end_reason" not in out  # ERROR 不终态,留给 selfheal


# ============ selfheal ============


def test_selfheal_counts_and_prefers_validation_reason() -> None:
    out = nodes.selfheal(
        _state(
            validation=ValidationResult(passed=False, reason="MULTI_STATEMENT"),
            execution=ExecutionResult(verdict="ERROR", reason="旧错误"),
            retry_count=1,
        )
    )
    assert out["retry_count"] == 2
    assert "MULTI_STATEMENT" in str(out["error"])


def test_selfheal_uses_execution_reason() -> None:
    out = nodes.selfheal(
        _state(
            validation=ValidationResult(passed=True),
            execution=ExecutionResult(verdict="ERROR", reason="column x 不存在"),
        )
    )
    assert out["error"] == "column x 不存在"
    assert out["retry_count"] == 1


def test_selfheal_budget_exhausted_no_increment() -> None:
    """重试预算(初始尝试不计)耗尽:不增计数,标 retry_exhausted。"""
    out = nodes.selfheal(
        _state(
            execution=ExecutionResult(verdict="ERROR", reason="column x 不存在"),
            retry_count=nodes.MAX_RETRY,
        )
    )
    assert out["end_reason"] == "retry_exhausted"
    assert "retry_count" not in out  # 计数=实际发起的重试次数,不虚增


# ============ summarize ============


def test_compute_stats_numeric_and_bool() -> None:
    stats = nodes.compute_stats(
        ["name", "qty", "flag"],
        [["a", 5, True], ["b", 15, False]],
    )
    assert stats["rows"] == 2
    assert stats["qty_max"] == 15 and stats["qty_min"] == 5 and stats["qty_avg"] == 10.0
    assert "flag_max" not in stats  # bool 显式排除
    assert "name_max" not in stats


@pytest.mark.parametrize(
    ("raw", "summary", "n_insights"),
    [
        ('{"summary":"共3行","insights":[{"type":"max","text":"最多"}]}', "共3行", 1),
        ("```json\n{\"summary\":\"包标记\"}\n```", "包标记", 0),
        ('前缀废话 {"summary":"嵌在文里"} 后缀', "嵌在文里", 0),
        ("完全不是 JSON", "", 0),
    ])
def test_parse_summary_json(raw: str, summary: str, n_insights: int) -> None:
    parsed = nodes.parse_summary_json(raw)
    assert parsed.get("summary", "") == summary
    assert len(parsed.get("insights", [])) == n_insights


def test_fallback_summary() -> None:
    assert nodes.fallback_summary({"rows": 2, "qty_max": 9, "qty_min": 1, "qty_avg": 5}) == (
        "共 2 行，qty 最大值 9，最小值 1，平均 5。"
    )
    assert nodes.fallback_summary({"rows": 4}) == "共 4 行结果。"


async def test_summarize_no_rows_short_circuit() -> None:
    out = await nodes.summarize(_state(execution=ExecutionResult(verdict="PASS", rows=[])))
    assert out["summary"] == "查询无结果。"


async def test_summarize_llm_failure_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, FakeLlm(boom=True), FakeApi())
    exe = ExecutionResult(verdict="PASS", columns=["qty"], rows=[[3], [7]])
    out = await nodes.summarize(_state(execution=exe))
    assert out["insights"] == []
    assert "qty" in str(out["summary"])  # stats 兜底
    assert out["stats"]["qty_max"] == 7


async def test_summarize_non_json_degrades_to_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, FakeLlm(["就是三行数据"]), FakeApi())
    exe = ExecutionResult(verdict="PASS", columns=["qty"], rows=[[1], [2], [3]])
    out = await nodes.summarize(_state(execution=exe))
    assert "共 3 行" in str(out["summary"])  # 非 JSON 降级不报错


async def test_summarize_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    answer = '{"summary":"库存共 30 件","insights":[{"type":"max","text":"连弩最多"}]}'
    _install(monkeypatch, FakeLlm([answer]), FakeApi())
    exe = ExecutionResult(verdict="PASS", columns=["name", "qty"], rows=[["连弩", 30]])
    out = await nodes.summarize(_state(execution=exe))
    assert out["summary"] == "库存共 30 件"
    assert out["insights"] == [{"type": "max", "text": "连弩最多"}]
    assert out["stats"]["qty_max"] == 30
