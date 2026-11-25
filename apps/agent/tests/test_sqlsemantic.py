"""确定性 SQL 语义校验:表驱动违规、歧义不误拒与生成/自愈链集成。"""
# pyright: reportPrivateUsage=false

from typing import Any, cast

import pytest

from kongming_agent import deps
from kongming_agent.apiclient import (
    RagContext,
    SqlExecuteOutcome,
)
from kongming_agent.graph import nodes
from kongming_agent.graph.build import get_graph
from kongming_agent.graph.state import AgentState
from kongming_agent.schemagraph import GraphColumn, GraphForeignKey, GraphTable, SchemaGraph
from kongming_agent.sqlsemantic import validate_semantics

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

    monkeypatch.setattr(nodes, "emit_event", _record)
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
