"""确定性 SQL 语义校验:表驱动违规、歧义不误拒与生成/自愈链集成。"""
# pyright: reportPrivateUsage=false

from typing import Any, cast

import pytest

from kongming_agent import deps
from kongming_agent.apiclient import (
    RagContext,
    SqlExecuteOutcome,
)
from kongming_agent.graph import nodes, semantic_guard
from kongming_agent.graph.build import get_graph
from kongming_agent.graph.state import AgentState
from kongming_agent.schemagraph import (
    GraphColumn,
    GraphForeignKey,
    GraphTable,
    SchemaGraph,
    graph_for_prompt,
)
from kongming_agent.sqlsemantic import (
    repair_bridge_join,
    repair_event_count_fanout,
    time_basis_clarification,
    validate_bridge_joins,
    validate_semantics,
)

S = "sakila"


def _table(
    name: str, cols: list[tuple[str, str]], pk: list[str], role: str, comment: str = ""
) -> GraphTable:
    return GraphTable(
        S,
        name,
        comment,
        tuple(GraphColumn(c, t) for c, t in cols),
        tuple(pk),
        role,
    )


def _fk(source: str, sc: str, target: str, tc: str) -> GraphForeignKey:
    return GraphForeignKey(S, source, (sc,), S, target, (tc,))


def _graph(*, complete: bool = True) -> SchemaGraph:
    tables = [
        _table(
            "rental",
            [
                ("rental_id", "integer"),
                ("rental_date", "timestamp without time zone"),
                ("inventory_id", "integer"),
                ("customer_id", "integer"),
                ("staff_id", "integer"),
            ],
            ["rental_id"],
            "event",
            "租赁记录",
        ),
        _table(
            "payment",
            [
                ("payment_id", "integer"),
                ("rental_id", "integer"),
                ("customer_id", "integer"),
                ("staff_id", "integer"),
                ("amount", "numeric"),
                ("payment_date", "timestamp without time zone"),
            ],
            ["payment_id"],
            "amount",
            "支付明细",
        ),
        _table(
            "staff",
            [("staff_id", "integer"), ("store_id", "integer")],
            ["staff_id"],
            "dimension",
            "员工档案",
        ),
        _table(
            "store",
            [("store_id", "integer"), ("manager_staff_id", "integer")],
            ["store_id"],
            "dimension",
        ),
        _table(
            "inventory",
            [("inventory_id", "integer"), ("film_id", "integer"), ("store_id", "integer")],
            ["inventory_id"],
            "dimension",
            "影片库存",
        ),
        _table(
            "customer",
            [("customer_id", "integer"), ("store_id", "integer")],
            ["customer_id"],
            "dimension",
            "客户档案",
        ),
        _table("mystery", [("id", "integer"), ("value", "numeric")], ["id"], "unknown", "语义不明"),
    ]
    fks = [
        _fk("payment", "rental_id", "rental", "rental_id"),
        _fk("payment", "customer_id", "customer", "customer_id"),
        _fk("payment", "staff_id", "staff", "staff_id"),
        _fk("rental", "customer_id", "customer", "customer_id"),
        _fk("rental", "staff_id", "staff", "staff_id"),
        _fk("rental", "inventory_id", "inventory", "inventory_id"),
        _fk("staff", "store_id", "store", "store_id"),
        _fk("store", "manager_staff_id", "staff", "staff_id"),
        _fk("inventory", "store_id", "store", "store_id"),
        _fk("inventory", "film_id", "mystery", "id"),
    ]
    return SchemaGraph(tuple(tables), tuple(fks), complete=complete)


_CORRECT = (
    "SELECT date_trunc('month', r.rental_date), st.store_id,"
    " COUNT(DISTINCT r.rental_id), SUM(p.amount)"
    " FROM sakila.rental r JOIN sakila.payment p ON p.rental_id = r.rental_id"
    " JOIN sakila.staff st ON r.staff_id = st.staff_id"
    " JOIN sakila.store s ON st.store_id = s.store_id"
    " GROUP BY 1, 2"
)

_CASES: list[tuple[str, str, str, bool, set[str]]] = [
    (
        "correct",
        _CORRECT,
        "按月统计门店租赁笔数和总支付金额",
        True,
        set(),
    ),
    (
        "amount_count",
        "SELECT COUNT(p.payment_id), SUM(p.amount) FROM sakila.rental r"
        " JOIN sakila.payment p ON p.rental_id = r.rental_id",
        "租赁笔数和总支付金额",
        False,
        {"AMOUNT_TABLE_COUNT"},
    ),
    (
        "fanout",
        "SELECT COUNT(*) FROM sakila.rental r JOIN sakila.payment p ON p.rental_id = r.rental_id",
        "租赁笔数",
        False,
        {"EVENT_COUNT_FANOUT"},
    ),
    (
        "amount_time",
        "SELECT date_trunc('month', p.payment_date), COUNT(DISTINCT r.rental_id)"
        " FROM sakila.rental r JOIN sakila.payment p ON p.rental_id = r.rental_id"
        " GROUP BY 1",
        "按月统计租赁笔数",
        False,
        {"AMOUNT_TIME_FOR_EVENT"},
    ),
    (
        "store_bypass",
        "SELECT i.store_id, COUNT(DISTINCT r.rental_id) FROM sakila.rental r"
        " JOIN sakila.inventory i ON i.inventory_id = r.inventory_id"
        " GROUP BY i.store_id",
        "每个门店的租赁笔数",
        False,
        {"ATTRIBUTION_BYPASS"},
    ),
    (
        "staff_bypass",
        "SELECT p.staff_id, COUNT(DISTINCT r.rental_id) FROM sakila.rental r"
        " JOIN sakila.payment p ON p.rental_id = r.rental_id GROUP BY p.staff_id",
        "每个员工的租赁笔数",
        False,
        {"ATTRIBUTION_BYPASS"},
    ),
    (
        "unknown_table",
        "SELECT 1 FROM sakila.ghost",
        "租赁笔数",
        False,
        {"TABLE_NOT_IN_GRAPH"},
    ),
    (
        "unknown_column",
        "SELECT r.nope FROM sakila.rental r",
        "租赁笔数",
        False,
        {"COLUMN_NOT_IN_GRAPH"},
    ),
    (
        "ambiguous_role_not_rejected",
        "SELECT COUNT(*) FROM sakila.mystery",
        "有多少条",
        True,
        set(),
    ),
    (
        "unqualified_amount_count",
        "SELECT COUNT(payment_id) FROM sakila.rental r"
        " JOIN sakila.payment p ON p.rental_id = r.rental_id",
        "租赁笔数",
        False,
        {"AMOUNT_TABLE_COUNT"},
    ),
    (
        "unqualified_amount_time",
        "SELECT date_trunc('month', payment_date), COUNT(DISTINCT r.rental_id)"
        " FROM sakila.rental r JOIN sakila.payment p ON p.rental_id = r.rental_id"
        " GROUP BY 1",
        "按月统计租赁笔数",
        False,
        {"AMOUNT_TIME_FOR_EVENT"},
    ),
    (
        "unqualified_store_bypass",
        "SELECT store_id, COUNT(DISTINCT r.rental_id) FROM sakila.rental r"
        " JOIN sakila.inventory i ON i.inventory_id = r.inventory_id"
        " GROUP BY store_id",
        "每个门店的租赁笔数",
        False,
        {"ATTRIBUTION_BYPASS"},
    ),
    (
        "table_function_not_rejected",
        "SELECT g FROM generate_series(1, 10) AS g",
        "租赁笔数",
        True,
        set(),
    ),
    (
        "cte_mixed_source_not_rejected",
        "WITH x AS (SELECT r.rental_id FROM sakila.rental r)"
        " SELECT rental_id FROM x JOIN sakila.staff s ON true",
        "租赁笔数",
        True,
        set(),
    ),
]


@pytest.mark.parametrize(("name", "sql", "question", "passed", "categories"), _CASES)
def test_semantic_cases(
    name: str, sql: str, question: str, passed: bool, categories: set[str]
) -> None:
    verdict = validate_semantics(sql, _graph(), question)
    assert verdict.passed is passed, name
    assert set(verdict.categories) == categories, name


def test_incomplete_graph_skips_hard_rejects() -> None:
    verdict = validate_semantics(
        "SELECT r.nope FROM sakila.rental r", _graph(complete=False), "租赁笔数"
    )
    assert verdict.passed is True


def test_amount_time_guidance_names_event_time_column() -> None:
    """自愈指令给出事件表的具体时间列,模型可直接照改。"""
    verdict = validate_semantics(
        "SELECT date_trunc('month', p.payment_date), COUNT(DISTINCT r.rental_id)"
        " FROM sakila.rental r JOIN sakila.payment p ON p.rental_id = r.rental_id"
        " GROUP BY 1",
        _graph(),
        "按月统计租赁笔数",
    )
    violation = next(v for v in verdict.violations if v.category == "AMOUNT_TIME_FOR_EVENT")
    assert "payment_date" in violation.detail
    assert "sakila.rental.rental_date" in violation.guidance


def test_amount_count_guidance_names_event_table_and_pk() -> None:
    verdict = validate_semantics(
        "SELECT COUNT(p.payment_id) FROM sakila.rental r"
        " JOIN sakila.payment p ON p.rental_id = r.rental_id",
        _graph(),
        "租赁笔数",
    )
    violation = next(v for v in verdict.violations if v.category == "AMOUNT_TABLE_COUNT")
    assert "sakila.rental" in violation.guidance
    assert "COUNT(DISTINCT rental_id)" in violation.guidance


_BRIDGE_SQL = (
    "SELECT date_trunc('month', p.payment_date), st.store_id, COUNT(*), SUM(p.amount)"
    " FROM sakila.payment p JOIN sakila.rental r ON p.rental_id = r.rental_id"
    " JOIN sakila.staff st ON r.staff_id = st.staff_id"
    " JOIN sakila.store s ON st.store_id = s.store_id"
    " GROUP BY 1, 2"
)


def test_bridge_inner_join_loss_is_detected_and_repaired() -> None:
    graph = _graph()
    verdict = validate_bridge_joins(
        _BRIDGE_SQL, graph, "按支付月份统计每个门店的支付笔数和支付总额"
    )
    assert verdict.passed is False
    assert set(verdict.categories) == {"BRIDGE_JOIN_LOSS"}
    fixed = repair_bridge_join(_BRIDGE_SQL, graph, "按支付月份统计每个门店的支付笔数和支付总额")
    assert fixed is not None
    assert "JOIN sakila.rental" not in fixed
    assert "p.staff_id = st.staff_id" in fixed
    assert validate_bridge_joins(fixed, graph).passed is True


def test_bridge_repair_rejects_unrewired_join_predicate() -> None:
    """目标维度 JOIN 的 ON 还引用桥表列时,不能产出悬空别名 SQL。"""
    sql = _BRIDGE_SQL.replace(
        "JOIN sakila.staff st ON r.staff_id = st.staff_id",
        "JOIN sakila.staff st ON r.staff_id = st.staff_id AND r.rental_date > '2005-01-01'",
    )
    assert repair_bridge_join(sql, _graph(), "按支付月份统计每个门店的支付笔数") is None


def test_bridge_repair_keeps_filter_on_bridge_join() -> None:
    """桥 JOIN 的过滤条件(如已付款)不能被自动改写删掉;交给 selfheal。"""
    sql = (
        "SELECT COUNT(*) FROM sakila.rental r"
        " JOIN sakila.payment p ON p.rental_id = r.rental_id AND p.amount > 0"
        " JOIN sakila.staff st ON p.staff_id = st.staff_id"
    )
    graph = _graph()
    assert validate_bridge_joins(sql, graph, "已付款的租赁").passed is False
    assert repair_bridge_join(sql, graph, "已付款的租赁") is None
    # 额外等值条件不是可证外键配对,同样不能自动删桥
    extra_equality = sql.replace("AND p.amount > 0", "AND p.customer_id = r.customer_id")
    assert repair_bridge_join(extra_equality, graph, "已付款的租赁") is None


def test_bridge_join_guard_skips_left_and_metric_use() -> None:
    graph = _graph()
    left = _BRIDGE_SQL.replace("JOIN sakila.rental", "LEFT JOIN sakila.rental")
    assert validate_bridge_joins(left, graph).passed is True
    metric = _BRIDGE_SQL.replace("COUNT(*)", "COUNT(DISTINCT r.rental_id)")
    assert validate_bridge_joins(metric, graph).passed is True
    mentioned = validate_bridge_joins(_BRIDGE_SQL, graph, "按租赁记录统计每个门店的支付笔数")
    assert mentioned.passed is True


def test_graph_for_prompt_only_keeps_schema_hint_tables() -> None:
    graph = _graph()
    hint = "sakila.payment(支付单)(amount(numeric))\nsakila.staff(员工)(staff_id(integer))"
    projected = graph_for_prompt(graph, hint)
    assert {t.table_name for t in projected.tables} == {"payment", "staff"}


def test_projection_keeps_full_graph_as_known_tables() -> None:
    """投影只裁剪提示/列校验范围,不能把全图其他表误判成图外表。"""
    projected = _graph().project(((S, "payment"),))
    assert (S, "customer") in projected.known_tables
    sql = (
        "SELECT c.first_name FROM sakila.payment p "
        "JOIN sakila.customer c ON p.customer_id = c.customer_id"
    )
    assert validate_semantics(sql, projected, "支付客户").passed is True


def test_known_but_unprojected_table_is_not_rejected() -> None:
    """全图已知但未进召回子图的表可被合法引用(模型会写未召回的公开数据集表)。"""
    base = _graph()
    pinned = SchemaGraph(
        base.tables,
        base.foreign_keys,
        complete=True,
        known_tables=((S, "film"),),
    )
    verdict = validate_semantics("SELECT f.title FROM sakila.film f LIMIT 10", pinned, "影片清单")
    assert verdict.passed is True


def test_ambiguous_unqualified_table_is_not_out_of_graph() -> None:
    graph = SchemaGraph(
        (
            _table("rental", [("rental_id", "integer")], ["rental_id"], "event"),
            GraphTable(
                "other_schema",
                "rental",
                columns=(GraphColumn("rental_id", "integer"),),
                primary_key=("rental_id",),
                role="event",
            ),
        ),
        (),
    )
    verdict = validate_semantics("SELECT 1 FROM rental", graph, "租赁笔数")
    assert verdict.passed is True


class _GraphLlm:
    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.users: list[str] = []

    def prompt_budget(self, task: str | None = None) -> int:
        del task
        return 1_000_000

    async def chat(self, system: str, user: str, **_kw: object) -> str:
        del system
        self.users.append(user)
        return self.answers.pop(0)


class _GraphApi:
    def __init__(self, graph: SchemaGraph) -> None:
        self.graph = graph
        self.executed: list[str] = []

    async def rag_retrieve(self, **kw: object) -> RagContext:
        del kw
        return RagContext(
            schema_hint="sakila.rental(rental_id(integer))\nsakila.payment(amount(numeric))",
            schema_graph=self.graph,
        )

    async def fetch_schema(self, **kw: object) -> None:
        del kw

    async def sql_execute(self, **kw: object) -> SqlExecuteOutcome:
        self.executed.append(str(kw["sql_text"]))
        return SqlExecuteOutcome(verdict="PASS", columns=["n"], rows=[[1]])


async def test_graph_semantic_reject_goes_selfheal_before_execute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _GraphApi(_graph())
    llm = _GraphLlm(
        [
            "按月租赁笔数",  # understand
            '{"intent":"data","confidence":0.99}',  # route_intent
            "SELECT COUNT(*) FROM sakila.rental r JOIN sakila.payment p"
            " ON p.rental_id = r.rental_id",  # 首轮扇出错误
            "SELECT COUNT(DISTINCT r.rental_id) FROM sakila.rental r",  # 自愈后
            '{"summary":"共 1"}',  # summarize
        ]
    )
    monkeypatch.setattr(deps, "get_api", lambda: api)
    monkeypatch.setattr(deps, "get_llm", lambda: llm)
    graph = cast("Any", get_graph())
    state: AgentState = {
        "trace_id": "t-sem",
        "tenant_key": "sakila",
        "question": "按月租赁笔数",
        "history": [],
        "datasource_key": "sakila",
        "retry_count": 0,
    }
    final = cast("dict[str, Any]", await graph.ainvoke(state))
    assert api.executed == ["SELECT COUNT(DISTINCT r.rental_id) FROM sakila.rental r"]
    assert final["retry_count"] == 1
    assert "EVENT_COUNT_FANOUT" in llm.users[3]  # 定向自愈提示
    assert "结构事实" in llm.users[2]  # 生成提示消费结构化图


async def test_graph_prompt_includes_roles_and_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = _GraphLlm(["SELECT COUNT(DISTINCT r.rental_id) FROM sakila.rental r"])
    monkeypatch.setattr(deps, "get_llm", lambda: llm)
    state: AgentState = {
        "trace_id": "t",
        "tenant_key": "sakila",
        "question": "租赁笔数",
        "history": [],
        "datasource_key": "sakila",
        "schema_hint": "sakila.rental(rental_id(integer))",
        "schema_graph": _graph().to_dict(),
    }
    await nodes.generate(state)
    assert "结构事实" in llm.users[0]
    assert "角色:event" in llm.users[0]
    assert "主键:rental_id" in llm.users[0]
    assert "外键" in llm.users[0]


def test_guard_trace_records_categories_without_sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict[str, object]]] = []

    def _record(event: str, data: dict[str, object]) -> None:
        events.append((event, data))

    monkeypatch.setattr(semantic_guard, "emit_event", _record)
    sql = "SELECT COUNT(*) FROM sakila.rental r JOIN sakila.payment p ON p.rental_id = r.rental_id"
    out = nodes.validate(
        {
            "trace_id": "t",
            "tenant_key": "sakila",
            "question": "租赁笔数",
            "history": [],
            "datasource_key": "sakila",
            "generated_sql": sql,
            "schema_graph": _graph().to_dict(),
        }
    )
    validation = cast("nodes.ValidationResult", out["validation"])
    assert validation.passed is False and validation.permission_block is False
    assert sql not in validation.reason
    guard = [data for _event, data in events if data.get("node") == "guard"]
    assert guard and guard[0]["fields"] == {
        "kind": "semantic_guard",
        "verdict": "reject",
        "categories": ["EVENT_COUNT_FANOUT"],
    }


def test_validate_uses_full_graph_for_unrecalled_columns() -> None:
    """召回提示可裁剪,但语义预检不能放过未召回真实表的坏列。"""
    sql = (
        "SELECT p.no_such_col FROM sakila.rental r"
        " JOIN sakila.payment p ON p.rental_id = r.rental_id"
    )
    out = nodes.validate(
        {
            "trace_id": "t",
            "tenant_key": "sakila",
            "question": "租赁笔数",
            "history": [],
            "datasource_key": "sakila",
            "schema_hint": "sakila.rental(rental_id(integer))",
            "generated_sql": sql,
            "schema_graph": _graph().to_dict(),
        }
    )
    validation = cast("nodes.ValidationResult", out["validation"])
    assert validation.passed is False
    assert "COLUMN_NOT_IN_GRAPH" in (validation.reason or "")


def _bridge_graph() -> SchemaGraph:
    """影片-分类关联是关系桥:两条维度外键同时参与,不应按绕行拒。"""
    base = _graph()
    film = _table("film", [("film_id", "integer"), ("title", "text")], ["film_id"], "dimension")
    film_category = _table(
        "film_category",
        [("film_id", "integer"), ("category_id", "integer")],
        ["film_id", "category_id"],
        "dimension",
    )
    category = _table(
        "category", [("category_id", "integer"), ("name", "text")], ["category_id"], "dimension"
    )
    fks = (
        *base.foreign_keys,
        _fk("inventory", "film_id", "film", "film_id"),
        _fk("film_category", "film_id", "film", "film_id"),
        _fk("film_category", "category_id", "category", "category_id"),
    )
    return SchemaGraph((*base.tables, film, film_category, category), fks, complete=True)


def test_category_bridge_is_not_attribution_bypass() -> None:
    sql = (
        "SELECT f.title, c.name FROM sakila.rental r"
        " JOIN sakila.inventory i ON i.inventory_id = r.inventory_id"
        " JOIN sakila.film f ON i.film_id = f.film_id"
        " JOIN sakila.film_category fc ON f.film_id = fc.film_id"
        " JOIN sakila.category c ON fc.category_id = c.category_id"
        " GROUP BY f.title, c.name"
    )
    verdict = validate_semantics(sql, _bridge_graph(), "前5部电影分别属于哪个分类")
    assert verdict.passed is True, verdict.violations


def test_unknown_role_bridge_is_not_attribution_bypass() -> None:
    """召回子图里桥表角色未知(Q6 实测):证据不足不做归属硬拒。"""
    film = _table("film", [("film_id", "integer"), ("title", "text")], ["film_id"], "dimension")
    film_category = _table(
        "film_category",
        [("film_id", "integer"), ("category_id", "integer")],
        ["film_id", "category_id"],
        "unknown",
    )
    graph = SchemaGraph(
        (*_graph().tables, film, film_category),
        (*_graph().foreign_keys, _fk("film_category", "film_id", "film", "film_id")),
        complete=True,
        known_tables=((S, "category"),),
    )
    sql = (
        "SELECT f.title, c.name FROM sakila.rental r"
        " JOIN sakila.inventory i ON i.inventory_id = r.inventory_id"
        " JOIN sakila.film f ON i.film_id = f.film_id"
        " JOIN sakila.film_category fc ON f.film_id = fc.film_id"
        " JOIN sakila.category c ON fc.category_id = c.category_id"
        " GROUP BY f.title, c.name"
    )
    verdict = validate_semantics(sql, graph, "前5部电影分别属于哪个分类")
    assert verdict.passed is True, verdict.violations


def _time_graph() -> SchemaGraph:
    rental = GraphTable(
        S,
        "rental",
        "租赁单(一次租借一行)",
        (
            GraphColumn("rental_id", "integer"),
            GraphColumn("rental_date", "timestamp without time zone", "租借时间"),
        ),
        ("rental_id",),
        "event",
    )
    payment = GraphTable(
        S,
        "payment",
        "支付单(按租赁单收款)",
        (
            GraphColumn("payment_id", "integer"),
            GraphColumn("rental_id", "integer"),
            GraphColumn("payment_date", "timestamp without time zone", "支付时间"),
            GraphColumn("amount", "numeric", "支付金额"),
        ),
        ("payment_id",),
        "amount",
    )
    return SchemaGraph(
        (rental, payment),
        (_fk("payment", "rental_id", "rental", "rental_id"),),
        complete=True,
    )


@pytest.mark.parametrize(
    ("question", "expect_question"),
    [
        ("按门店每个月的租赁笔数和总支付金额，按月份升序排列", True),
        ("按租赁月份统计每个门店的租赁笔数和总支付金额", False),
        ("按支付月份统计每个门店的租赁笔数和总支付金额", False),
        ("按月统计租赁笔数", False),
        ("上个月的租赁笔数和总支付金额", False),
        ("按月统计租赁笔数和支付金额", True),
    ],
)
def test_time_basis_clarification(question: str, expect_question: bool) -> None:
    verdict = time_basis_clarification(question, _time_graph())
    assert (verdict is not None) is expect_question, verdict
    if verdict is not None:
        assert "租借时间" in verdict and "支付时间" in verdict


def test_explicit_amount_time_is_allowed() -> None:
    sql = (
        "SELECT date_trunc('month', p.payment_date), COUNT(DISTINCT r.rental_id),"
        " SUM(p.amount) FROM sakila.rental r"
        " JOIN sakila.payment p ON p.rental_id = r.rental_id GROUP BY 1"
    )
    verdict = validate_semantics(sql, _time_graph(), "按支付月份统计租赁笔数和总支付金额")
    assert verdict.passed is True, verdict.violations


def _bare_time_graph() -> SchemaGraph:
    """公开数据集常见形态:表/列没有中文注释,只能靠问句指标词兜底。"""
    base = _time_graph()
    return SchemaGraph(
        (
            GraphTable(
                S,
                "rental",
                "",
                (GraphColumn("rental_id", "integer"), GraphColumn("rental_date", "timestamp")),
                ("rental_id",),
                "event",
            ),
            GraphTable(
                S,
                "payment",
                "",
                (
                    GraphColumn("payment_id", "integer"),
                    GraphColumn("rental_id", "integer"),
                    GraphColumn("payment_date", "timestamp"),
                    GraphColumn("amount", "numeric"),
                ),
                ("payment_id",),
                "amount",
            ),
        ),
        base.foreign_keys,
        complete=True,
    )


def test_time_basis_clarification_requires_metadata_labels() -> None:
    """无表/列注释时不做词表猜测;等控制面把租户元数据补进图后再判。"""
    graph = _bare_time_graph()
    question = "按门店每个月的租赁笔数和总支付金额，按月份升序排列"
    assert time_basis_clarification(question, graph) is None


def test_repair_event_count_fanout_rewrites_pk_count() -> None:
    sql = (
        "SELECT COUNT(r.rental_id) FROM sakila.rental r"
        " JOIN sakila.payment p ON p.rental_id = r.rental_id"
    )
    fixed = repair_event_count_fanout(sql, _graph())
    assert fixed is not None
    assert "COUNT(DISTINCT r.rental_id)" in fixed
    assert validate_semantics(fixed, _graph(), "租赁笔数").passed is True
    star = "SELECT COUNT(*) FROM sakila.rental r JOIN sakila.payment p ON p.rental_id = r.rental_id"
    assert repair_event_count_fanout(star, _graph()) is None


async def test_graph_auto_repairs_count_fanout_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _GraphApi(_graph())
    llm = _GraphLlm(
        [
            "租赁笔数和总支付金额",  # understand
            '{"intent":"data","confidence":0.99}',  # route_intent
            "SELECT COUNT(r.rental_id), SUM(p.amount) FROM sakila.rental r"
            " JOIN sakila.payment p ON p.rental_id = r.rental_id",  # 计数被一对多放大
            '{"summary":"共 1"}',  # summarize
        ]
    )
    monkeypatch.setattr(deps, "get_api", lambda: api)
    monkeypatch.setattr(deps, "get_llm", lambda: llm)
    graph = cast("Any", get_graph())
    state: AgentState = {
        "trace_id": "t-autofix",
        "tenant_key": "sakila",
        "question": "租赁笔数和总支付金额",
        "history": [],
        "datasource_key": "sakila",
        "retry_count": 0,
    }
    final = cast("dict[str, Any]", await graph.ainvoke(state))
    assert final["retry_count"] == 0
    assert "COUNT(DISTINCT r.rental_id)" in api.executed[0]


async def test_generate_time_basis_clarifies_before_sql() -> None:
    state: AgentState = {
        "trace_id": "t-clarify",
        "tenant_key": "sakila",
        "question": "按门店每个月的租赁笔数和总支付金额，按月份升序排列",
        "history": [],
        "datasource_key": "sakila",
        "retry_count": 0,
        "schema_graph": _time_graph().to_dict(),
    }
    out = await nodes.generate(state)
    assert out["end_reason"] == "no_data"
    assert out["fallback_type"] == "CLARIFY"
    assert "请确认时间口径" in str(out["error"])
