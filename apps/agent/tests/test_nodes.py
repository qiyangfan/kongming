"""节点单测:mock LLM(deps.get_llm)+ mock 控制面回调(deps.get_api)。

覆盖(nl2sql/nl2sql-agent spec):指代消解 prompt 组装与 5 轮截断、schema 提示
拼装与降级、SQL 清洗、selfheal 错误喂回、预检四规则、三态映射、洞察降级链。
"""
# pyright: reportPrivateUsage=false

from typing import cast

import pytest

from kongming_agent import deps
from kongming_agent.apiclient import (
    MetaColumn,
    MetaTable,
    RagContext,
    SchemaMeta,
    SqlExecuteOutcome,
)
from kongming_agent.graph import nodes
from kongming_agent.graph.state import AgentState, ExecutionResult, ValidationResult
from kongming_agent.llm import LlmError


class FakeLlm:
    """按队列脚本应答;记录全部调用供断言。"""

    def __init__(self, answers: list[str] | None = None, *, boom: bool = False) -> None:
        self.answers = list(answers or [])
        self.boom = boom
        self.calls: list[tuple[str, str, float]] = []

    async def chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.2,
        **_kw: object,  # 021:task/trace_id/tenant_key 吸收
    ) -> str:
        self.calls.append((system, user, temperature))
        if self.boom:
            raise LlmError("LLM 调用失败: 模拟故障")
        return self.answers.pop(0) if self.answers else ""


class FakeApi:
    def __init__(
        self,
        meta: SchemaMeta | None = None,
        outcomes: list[SqlExecuteOutcome] | None = None,
        rag: RagContext | None = None,
    ) -> None:
        self.meta = meta
        self.outcomes = list(outcomes or [])
        self.rag = rag
        self.rag_calls: list[dict[str, object]] = []
        self.execute_calls: list[dict[str, object]] = []

    async def rag_retrieve(self, **kw: object) -> RagContext | None:
        self.rag_calls.append(kw)
        return self.rag

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
    assert out["history_sql"] == ""  # 双失败路径同样清空历史段(防残留)


async def test_retrieve_formats_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, FakeLlm(), FakeApi(meta=_meta()))
    out = await nodes.retrieve(_state())
    assert "tenant_default.goods" in str(out["schema_hint"])


async def test_retrieve_prefers_rag_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """召回命中:提示用召回结果,并透传历史 SQL;不回退内省(009 D4/D12)。"""
    api = FakeApi(
        meta=_meta(),
        rag=RagContext(schema_hint="tenant_sakila.film(title)", history_sql="SELECT 1"),
    )
    _install(monkeypatch, FakeLlm(), api)
    out = await nodes.retrieve(
        _state(datasource_key="sakila_ui", resolved_question="sakila 有多少部电影")
    )
    assert out["schema_hint"] == "tenant_sakila.film(title)"
    assert out["history_sql"] == "SELECT 1"
    # 召回问句=understand 改写问句(指代消解后),非原始问句(多轮裸指代召回会漏)
    assert api.rag_calls[0]["question"] == "sakila 有多少部电影"
    assert api.rag_calls[0]["datasource_key"] == "sakila_ui"


async def test_retrieve_rag_empty_falls_back_introspection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """知识库零命中(schema_hint 空):回退实时内省;history_sql 独立保留
    (R2-S1:「知识未抓取但已有成功历史」形态下 few-shot 不应被整体丢弃)。"""
    api = FakeApi(meta=_meta(), rag=RagContext(schema_hint="", history_sql="SELECT 9"))
    _install(monkeypatch, FakeLlm(), api)
    out = await nodes.retrieve(_state())
    assert "tenant_default.goods" in str(out["schema_hint"])  # 内省兜底
    assert out["history_sql"] == "SELECT 9"  # 历史命中与表说明命中互相独立


async def test_retrieve_introspect_fail_keeps_semantic_hints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """审查循环 F5:召回成功(schema 空)+内省失败分支不得丢语义段——
    relations/metric 与内省成败无关,与兄弟分支同口径透传。"""
    from kongming_agent.apiclient import RagContext

    api = FakeApi(
        meta=None,
        rag=RagContext(
            schema_hint="",
            history_sql="SELECT 9",
            relations_hint="orders -> customers",
            metric_hints="GMV=SUM(amount)",
        ),
    )
    _install(monkeypatch, FakeLlm(), api)
    out = await nodes.retrieve(_state())
    assert out["schema_hint"] == ""
    assert out["history_sql"] == "SELECT 9"
    assert out["relations_hint"] == "orders -> customers"
    assert out["metric_hints"] == "GMV=SUM(amount)"


async def test_retrieve_history_only_still_feeds_generate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """纯历史命中形态(schema 空+history 有):内省兜底+few-shot 共存(R2-S1
    核心场景:知识从未抓取,问答成功回写后,相似问句应受益于历史 SQL)。"""
    api = FakeApi(meta=_meta(), rag=RagContext(schema_hint="", history_sql="SELECT 1"))
    _install(monkeypatch, FakeLlm(), api)
    out = await nodes.retrieve(_state())
    assert "tenant_default.goods" in str(out["schema_hint"])
    assert out["history_sql"] == "SELECT 1"


async def test_retrieve_rag_http_failure_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """召回服务失败(rag=None):回退内省(上游纯知识库形态的差异点)。"""
    api = FakeApi(meta=_meta(), rag=None)
    _install(monkeypatch, FakeLlm(), api)
    out = await nodes.retrieve(_state())
    assert "tenant_default.goods" in str(out["schema_hint"])


# ============ generate ============


async def test_generate_history_sql_section_placement(monkeypatch: pytest.MonkeyPatch) -> None:
    """历史成功 SQL 段(009):位于表结构之后;系统模板不动;无命中不出现。"""
    fake = FakeLlm(["SELECT 1"])
    _install(monkeypatch, fake, FakeApi())
    await nodes.generate(
        _state(schema_hint="表 t(a)", history_sql="SELECT count(*) FROM t")
    )
    _, user, _ = fake.calls[0]
    assert "历史成功 SQL" in user
    assert user.index("可用表结构") < user.index("参考历史成功 SQL") < user.index("生成 SQL")
    assert "SELECT count(*) FROM t" in user

    # 无历史命中:段不出现(005 形态回归)
    fake2 = FakeLlm(["SELECT 2"])
    _install(monkeypatch, fake2, FakeApi())
    await nodes.generate(_state(schema_hint="表 t(a)"))
    assert "历史成功 SQL" not in fake2.calls[0][1]


async def test_generate_history_survives_selfheal_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """自愈轮:错误段与历史段共存(历史段在前,错误喂回在后)。"""
    fake = FakeLlm(["SELECT 1"])
    _install(monkeypatch, fake, FakeApi())
    await nodes.generate(
        _state(schema_hint="表 t(a)", history_sql="SELECT 1", error="上次语法错误")
    )
    user = fake.calls[0][1]
    assert "参考历史成功 SQL" in user and "上次生成的 SQL 有误" in user


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
    """043:空结构占位文案重写——如实声明+严禁臆测+哨兵出口;「尽力生成」教唆绝迹。"""
    fake = FakeLlm(["SELECT 1"])
    _install(monkeypatch, fake, FakeApi())
    await nodes.generate(_state(schema_hint=""))
    user = fake.calls[0][1]
    assert "未能检索到与问题相关的表结构" in user  # 如实声明,不静默
    assert "严禁臆测表名或列名" in user
    assert "NO_RELEVANT_TABLE" in user  # 与哨兵机制联动
    assert "尽力生成" not in user


# ============ 043 生成纪律(哨兵短路/喂回引导) ============


async def test_generate_sentinel_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    """哨兵输出→短路产物:SQL 空、end_reason=no_data、用户友好文案;不触发执行。"""
    fake = FakeLlm(["NO_RELEVANT_TABLE"])
    api = FakeApi()
    _install(monkeypatch, fake, api)
    out = await nodes.generate(_state(schema_hint="表 t(a)"))
    assert out["generated_sql"] == ""
    assert out["end_reason"] == "no_data"
    assert out["error"] == "当前数据源未找到回答该问题所需的数据,可切换数据源或调整问法后重试"
    assert api.execute_calls == []


async def test_generate_sentinel_wrapped_forms_detected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """形态防御:markdown 包裹/大小写变体识别;哨兵仅作子串出现不误触。"""
    for raw in (
        "```sql\nNO_RELEVANT_TABLE\n```",
        "```\nNO_RELEVANT_TABLE\n```",
        "  no_relevant_table  ",
    ):
        fake = FakeLlm([raw])
        _install(monkeypatch, fake, FakeApi())
        out = await nodes.generate(_state(schema_hint="表 t(a)"))
        assert out.get("end_reason") == "no_data", raw
    # 子串形态(解释句夹带):不短路,按普通输出走既有防御链(validate→防火墙兜底)
    fake = FakeLlm(["我认为此处应输出 NO_RELEVANT_TABLE，但 SELECT 1 可行"])
    _install(monkeypatch, fake, FakeApi())
    out = await nodes.generate(_state(schema_hint="表 t(a)"))
    assert "end_reason" not in out
    assert "NO_RELEVANT_TABLE" in str(out["generated_sql"])


async def test_generate_selfheal_dne_error_appends_table_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """喂回增强:上次错误含 does not exist(大小写不敏感)→ 附清单引导+哨兵出口。"""
    fake = FakeLlm(["SELECT 1"])
    _install(monkeypatch, fake, FakeApi())
    await nodes.generate(
        _state(schema_hint="表 t(a)", error='relation "public.staff" Does Not Exist')
    )
    user = fake.calls[0][1]
    assert '上次生成的 SQL 有误：relation "public.staff" Does Not Exist' in user
    assert "所引用的表或列不在『可用表结构』清单中" in user
    assert "只输出 NO_RELEVANT_TABLE" in user


def test_selfheal_guidance_missing_from_clause() -> None:
    """047:未定义别名(missing FROM-clause)→ 补 JOIN/改别名指令(017 事故形态)。"""
    g = nodes._selfheal_guidance('missing FROM-clause entry for table "s"')
    assert "s" in g
    assert "JOIN" in g or "别名" in g
    assert "NO_RELEVANT_TABLE" not in g  # 别名错可修,不是弃答出口


def test_selfheal_guidance_column_vs_relation() -> None:
    """047:列不存在→列纪律(不引向表哨兵);表不存在→哨兵出口;其余→空。"""
    col = nodes._selfheal_guidance('column "email" does not exist')
    assert "列" in col
    assert "只输出 NO_RELEVANT_TABLE" not in col  # 表存在,出口是改列不是弃答
    rel = nodes._selfheal_guidance('relation "public.staff" does not exist')
    assert "NO_RELEVANT_TABLE" in rel
    # 函数/类型等关系以外的 does-not-exist:泛化喂回,不误引弃答出口
    assert nodes._selfheal_guidance('function "foo(integer)" does not exist') == ""
    assert nodes._selfheal_guidance('type "q Quarter" does not exist') == ""
    assert nodes._selfheal_guidance("syntax error at or near") == ""


async def test_generate_selfheal_alias_error_gets_targeted_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """047:sakila_017 事故端到端——别名错进 generate 喂回获得针对性指令。"""
    fake = FakeLlm(["SELECT 1"])
    _install(monkeypatch, fake, FakeApi())
    await nodes.generate(
        _state(
            schema_hint="表 tenant_sakila.inventory(i)",
            error='missing FROM-clause entry for table "s"',
        )
    )
    user = fake.calls[0][1]
    assert "上次生成的 SQL 有误" in user
    assert "JOIN" in user or "别名" in user


async def test_generate_selfheal_column_error_no_sentinel_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """047:列不存在喂回不携带表哨兵出口(否则模型弃答可答之题)。"""
    fake = FakeLlm(["SELECT 1"])
    _install(monkeypatch, fake, FakeApi())
    await nodes.generate(
        _state(schema_hint="表 t(a)", error='column "email" does not exist')
    )
    user = fake.calls[0][1]
    assert "不得用其他列近似顶替" in user or "清单内" in user
    assert "只输出 NO_RELEVANT_TABLE" not in user


async def test_generate_selfheal_other_error_feedback_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """其余错误形态喂回不变(不附清单引导)。"""
    fake = FakeLlm(["SELECT 1"])
    _install(monkeypatch, fake, FakeApi())
    await nodes.generate(_state(schema_hint="表 t(a)", error="SQL 预检未过：MULTI_STATEMENT"))
    user = fake.calls[0][1]
    assert "上次生成的 SQL 有误：SQL 预检未过：MULTI_STATEMENT" in user
    assert "清单" not in user


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
    # insights 值型为 object,按生产代码实型(list)收窄
    assert len(cast("list[object]", parsed.get("insights", []))) == n_insights


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
    # stats 值型为 object,按生产代码实型(dict)收窄
    assert cast("dict[str, object]", out["stats"])["qty_max"] == 7


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
    assert cast("dict[str, object]", out["stats"])["qty_max"] == 30


async def test_summarize_context_grounded_with_stats(monkeypatch: pytest.MonkeyPatch) -> None:
    """018:喂给 LLM 的上下文含总行数+本地统计+前5行(摘要数值接地)。"""
    fake = FakeLlm(['{"summary":"x"}'])
    _install(monkeypatch, fake, FakeApi())
    exe = ExecutionResult(verdict="PASS", columns=["qty"], rows=[[3], [7], [10]])
    await nodes.summarize(_state(execution=exe))
    user = fake.calls[0][1]
    assert "总行数：3" in user
    assert "qty_max" in user and "10" in user  # 本地统计进上下文
    assert "前5行" in user


async def test_summarize_context_includes_table_hint_with_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """037:上下文含 schema_hint 表说明段(表名(别名) 列 -- 表说明)。

    只取召回形态表头行(R1-B1):示例行剔除省 token,内省回退形态
    (「表 x.y：…」+「  - 」列行,无别名且全限定名与称谓规则相悖)不注入。
    """
    fake = FakeLlm(['{"summary":"x"}'])
    _install(monkeypatch, fake, FakeApi())
    exe = ExecutionResult(verdict="PASS", columns=["qty"], rows=[[3]])
    schema_hint = (
        "tenant_default.goods(商品表)(id integer, name varchar) -- 商品信息\n"
        "  示例:SELECT count(*) FROM tenant_default.goods\n"
        "tenant_default.orders(id bigint) -- 订单主表\n"
        "表 tenant_default.inventory：库存\n"
        "  - id bigint\n"
    )
    await nodes.summarize(_state(execution=exe, schema_hint=schema_hint))
    user = fake.calls[0][1]
    assert "goods(商品表)" in user  # 别名随表说明段注入
    assert "-- 订单主表" in user  # 无 alias 表:表说明中文可见(称谓回退依据)
    assert "示例:" not in user  # 示例行剔除省 token
    assert "表 tenant_default.inventory" not in user  # 内省形态不注入(R1-B1)
    assert "- id bigint" not in user
    # 无 alias 表不产出「表名 -> 列清单」形态(正则解析垃圾,实施期抓回)
    assert "orders ->" not in user


async def test_summarize_context_no_schema_hint_no_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """schema_hint 空:不注入表说明段(018 上下文形态零回归)。"""
    fake = FakeLlm(['{"summary":"x"}'])
    _install(monkeypatch, fake, FakeApi())
    exe = ExecutionResult(verdict="PASS", columns=["qty"], rows=[[3]])
    await nodes.summarize(_state(execution=exe))
    user = fake.calls[0][1]
    assert "所涉表" not in user
