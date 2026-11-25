"""结构化 schema 图:角色推断、桥接、缓存与内部召回纯增字段。"""
# pyright: reportPrivateUsage=false

from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.config import get_settings
from kongming.db import get_engine
from kongming.rag.cache import ContextCache
from kongming.rag.schema_graph import (
    GraphColumn,
    GraphForeignKey,
    GraphTable,
    infer_roles,
    schema_hint_tables,
    select_bridge_tables,
)
from kongming.rag.schema_graph_loader import load_schema_graph
from kongming.redis_client import get_redis

INTERNAL = {"X-Internal-Token": "test-internal-token"}
SCHEMA = "tenant_graph"
QUESTION = "按月统计门店租赁笔数和总支付金额"


def _table(
    name: str, columns: list[tuple[str, str]], pk: list[str], comment: str = ""
) -> GraphTable:
    return GraphTable(
        schema_name=SCHEMA,
        table_name=name,
        comment=comment,
        columns=tuple(GraphColumn(name=c, data_type=t) for c, t in columns),
        primary_key=tuple(pk),
    )


def _fk(source: str, source_col: str, target: str, target_col: str) -> GraphForeignKey:
    return GraphForeignKey(
        source_schema=SCHEMA,
        source_table=source,
        source_columns=(source_col,),
        target_schema=SCHEMA,
        target_table=target,
        target_columns=(target_col,),
    )


@pytest.fixture(scope="module")
def graph_tables() -> list[GraphTable]:
    return [
        _table(
            "rental",
            [
                ("rental_id", "integer"),
                ("rental_date", "timestamp without time zone"),
                ("inventory_id", "integer"),
                ("customer_id", "integer"),
                ("staff_id", "integer"),
                ("last_update", "timestamp without time zone"),
            ],
            ["rental_id"],
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
            "支付明细",
        ),
        _table(
            "staff",
            [("staff_id", "integer"), ("store_id", "integer"), ("last_update", "timestamp")],
            ["staff_id"],
            "员工档案",
        ),
        _table("store", [("store_id", "integer"), ("last_update", "timestamp")], ["store_id"]),
        _table(
            "inventory",
            [("inventory_id", "integer"), ("film_id", "integer"), ("store_id", "integer")],
            ["inventory_id"],
            "影片库存",
        ),
        _table("film", [("film_id", "integer")], ["film_id"]),
        _table(
            "customer",
            [
                ("customer_id", "integer"),
                ("store_id", "integer"),
                ("create_date", "date"),
                ("last_update", "timestamp"),
            ],
            ["customer_id"],
            "客户档案",
        ),
    ]


@pytest.fixture(scope="module")
def graph_fks() -> list[GraphForeignKey]:
    return [
        _fk("payment", "rental_id", "rental", "rental_id"),
        _fk("payment", "customer_id", "customer", "customer_id"),
        _fk("payment", "staff_id", "staff", "staff_id"),
        _fk("rental", "customer_id", "customer", "customer_id"),
        _fk("rental", "staff_id", "staff", "staff_id"),
        _fk("rental", "inventory_id", "inventory", "inventory_id"),
        _fk("staff", "store_id", "store", "store_id"),
        _fk("inventory", "store_id", "store", "store_id"),
        _fk("inventory", "film_id", "film", "film_id"),
        _fk("customer", "store_id", "store", "store_id"),
    ]


def test_roles_are_structural_and_event_not_negated_by_amount_fk(
    graph_tables: list[GraphTable], graph_fks: list[GraphForeignKey]
) -> None:
    graph = infer_roles(graph_tables, graph_fks)
    roles = {t.table_name: t.role for t in graph.tables}
    assert roles["rental"] == "event"  # 被 payment 外键引用仍是事件
    assert roles["payment"] == "amount"
    assert roles["staff"] == "dimension"
    assert roles["store"] == "dimension"
    assert roles["inventory"] == "dimension"


def test_graph_contains_no_credentials(
    graph_tables: list[GraphTable], graph_fks: list[GraphForeignKey]
) -> None:
    payload = infer_roles(graph_tables, graph_fks).to_dict()
    assert "password" not in str(payload).lower()
    assert "host" not in payload and "database_url" not in payload


def test_bridge_paths_prefer_direct_and_are_bounded(
    graph_tables: list[GraphTable], graph_fks: list[GraphForeignKey]
) -> None:
    graph = infer_roles(graph_tables, graph_fks)
    bridges = select_bridge_tables(graph, [(SCHEMA, "rental")], QUESTION, max_bridges=6)
    names = [b.key[1] for b in bridges]
    assert "staff" in names and "store" in names
    assert "payment" not in names  # 金额表不作桥接维度
    assert "customer" not in names  # 事件角色表不作桥接维度
    assert len(bridges) <= 6
    store = next(b for b in bridges if b.key[1] == "store")
    path = [(e.source_table, e.target_table) for e in store.edges]
    assert path[0][0] == "rental" and path[-1][1] == "store"


def test_bridge_zero_extension_without_path(
    graph_tables: list[GraphTable], graph_fks: list[GraphForeignKey]
) -> None:
    graph = infer_roles(graph_tables, graph_fks)
    # 从孤立维表出发没有事件角色,不扩展
    assert select_bridge_tables(graph, [(SCHEMA, "film")], QUESTION, max_bridges=6) == []


def test_schema_hint_table_parser_keeps_note() -> None:
    rows = schema_hint_tables(
        "tenant_graph.rental(租赁记录)(rental_id(integer)) -- 租赁记录\n"
        "tenant_graph.store(store_id(integer))"
    )
    assert rows[0] == (("tenant_graph", "rental"), "租赁记录")
    assert rows[1] == (("tenant_graph", "store"), "")


@pytest.fixture(scope="module", autouse=True)
async def _graph_biz() -> AsyncIterator[None]:  # pyright: ignore[reportUnusedFunction]
    async with get_engine().begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))
        await conn.execute(
            text(
                f'CREATE TABLE IF NOT EXISTS "{SCHEMA}".store ('
                " store_id integer PRIMARY KEY, last_update timestamp)"
            )
        )
        await conn.execute(
            text(
                f'CREATE TABLE IF NOT EXISTS "{SCHEMA}".staff ('
                " staff_id integer PRIMARY KEY, store_id integer REFERENCES"
                f' "{SCHEMA}".store(store_id), last_update timestamp)'
            )
        )
        await conn.execute(
            text(
                f'CREATE TABLE IF NOT EXISTS "{SCHEMA}".customer ('
                " customer_id integer PRIMARY KEY, store_id integer REFERENCES"
                f' "{SCHEMA}".store(store_id), create_date date, last_update timestamp)'
            )
        )
        await conn.execute(
            text(
                f'CREATE TABLE IF NOT EXISTS "{SCHEMA}".inventory ('
                " inventory_id integer PRIMARY KEY, film_id integer, store_id integer REFERENCES"
                f' "{SCHEMA}".store(store_id))'
            )
        )
        await conn.execute(
            text(
                f'CREATE TABLE IF NOT EXISTS "{SCHEMA}".rental ('
                " rental_id integer PRIMARY KEY, rental_date timestamp,"
                " inventory_id integer REFERENCES"
                f' "{SCHEMA}".inventory(inventory_id),'
                " customer_id integer REFERENCES"
                f' "{SCHEMA}".customer(customer_id),'
                " staff_id integer REFERENCES"
                f' "{SCHEMA}".staff(staff_id), last_update timestamp)'
            )
        )
        await conn.execute(
            text(
                f'CREATE TABLE IF NOT EXISTS "{SCHEMA}".payment ('
                " payment_id integer PRIMARY KEY, rental_id integer REFERENCES"
                f' "{SCHEMA}".rental(rental_id),'
                " customer_id integer REFERENCES"
                f' "{SCHEMA}".customer(customer_id),'
                " staff_id integer REFERENCES"
                f' "{SCHEMA}".staff(staff_id), amount numeric, payment_date timestamp)'
            )
        )
        await conn.execute(text(f"COMMENT ON TABLE \"{SCHEMA}\".rental IS '租赁记录'"))
        await conn.execute(text(f"COMMENT ON TABLE \"{SCHEMA}\".payment IS '支付明细'"))
        await conn.execute(text(f"COMMENT ON TABLE \"{SCHEMA}\".staff IS '员工档案'"))
    yield
    async with get_engine().begin() as conn:
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE'))


@pytest.fixture
def fake_embed(monkeypatch: pytest.MonkeyPatch) -> None:
    """召回向量固定指向本测试问句,确保 rental/payment 稳定入主召回。"""
    from kongming.rag.agent_callback import AgentRagClient

    async def _embed(self: AgentRagClient, texts: list[str]) -> list[list[float]]:
        del self
        return [[1.0] + [0.0] * (get_settings().rag_embedding_dim - 1) for _ in texts]

    monkeypatch.setattr(AgentRagClient, "embed", _embed)


async def _seed(s: AsyncSession, tenant_id: str, table: str, comment: str) -> None:
    vec = "[" + ",".join(["1.0"] + ["0.0"] * (get_settings().rag_embedding_dim - 1)) + "]"
    await s.execute(
        text(
            "INSERT INTO rag.table_descriptions"
            " (id, tenant_id, datasource_key, schema_name, table_name, table_comment,"
            "  columns_comment, sample_queries, source_type, embedding, tsv)"
            " VALUES (gen_random_uuid(), :t, 'default', :sn, :n, :c, '{}', '[]',"
            "  'MANUAL_UPLOAD', CAST(:v AS vector),"
            "  to_tsvector('simple', :n_t || ' ' || :c_t))"
            " ON CONFLICT (tenant_id, datasource_key, schema_name, table_name) DO NOTHING"
        ),
        {
            "t": tenant_id,
            "sn": SCHEMA,
            "n": table,
            "c": comment,
            "v": vec,
            "n_t": table,
            "c_t": comment or " ",
        },
    )
    await s.commit()


async def _tenant(s: AsyncSession) -> str:
    row = (
        await s.execute(text("SELECT id FROM public.sys_tenant WHERE tenant_key='graph'"))
    ).scalar_one_or_none()
    if row is not None:
        return str(row)
    tid = (
        await s.execute(
            text(
                "INSERT INTO public.sys_tenant (id, tenant_key, tenant_name)"
                " VALUES (gen_random_uuid(), 'graph', 'graph') RETURNING id"
            )
        )
    ).scalar_one()
    await s.commit()
    return str(tid)


async def test_retrieve_returns_graph_and_bridges(
    client: AsyncClient, session: AsyncSession, fake_embed: None
) -> None:
    tid = await _tenant(session)
    await _seed(session, tid, "rental", "租赁记录")
    await _seed(session, tid, "payment", "支付明细")
    await get_redis().delete(
        f"km:raggraph:{tid}:default:{get_settings().rag_fulltext_ts_config}:and-cw2"
    )
    r = await client.post(
        "/internal/rag/retrieve",
        json={"tenant_key": "graph", "question": QUESTION},
        headers=INTERNAL,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    graph = body["schema_graph"]
    assert graph is not None and graph["complete"] is True
    names = {t["table_name"] for t in graph["tables"]}
    assert {"rental", "payment", "staff", "store"} <= names
    roles = {t["table_name"]: t["role"] for t in graph["tables"]}
    assert roles["rental"] == "event" and roles["payment"] == "amount"
    assert "password" not in str(graph).lower()
    assert f"{SCHEMA}.staff" in body["schema_hint"]  # 桥接尾部追加
    assert f"JOIN {SCHEMA}.store ON" in body["relations_hint"]


async def test_graph_cache_hit_and_invalidated(
    client: AsyncClient, session: AsyncSession, fake_embed: None
) -> None:
    tid = await _tenant(session)
    key = f"km:raggraph:{tid}:default:{get_settings().rag_fulltext_ts_config}:and-cw2"
    await get_redis().delete(key)
    async with get_engine().connect() as conn:
        graph = await load_schema_graph(
            conn,  # type: ignore[arg-type]  # 读图只读,连接与会话同为 AsyncConnection 接口
            tenant_id=tid,
            tenant_key="graph",
            datasource_key="default",
            cache=ContextCache(get_redis(), ts_config=get_settings().rag_fulltext_ts_config),
        )
    assert graph is not None
    assert await get_redis().exists(key) == 1
    await ContextCache(get_redis(), ts_config=get_settings().rag_fulltext_ts_config).evict(
        tid, "default"
    )
    assert await get_redis().exists(key) == 0
