"""结构化 schema 图:角色推断、桥接、缓存与内部召回纯增字段。"""
# pyright: reportPrivateUsage=false

from collections.abc import AsyncIterator
from typing import Any, cast

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
    SchemaGraph,
    drop_schema_hint_tables,
    infer_roles,
    schema_hint_tables,
    select_bridge_tables,
)
from kongming.rag.schema_graph_loader import _load_descriptions, load_schema_graph
from kongming.rag.schema_graph_response import _align_hint_with_graph
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


def test_drop_schema_hint_tables_removes_block_with_examples() -> None:
    hint = (
        "tenant_graph.rental(rental_id(integer)) -- 租赁\n"
        "  示例:SELECT * FROM tenant_graph.rental LIMIT 10;\n"
        "tenant_graph.payment_p2007_01(amount(numeric)) -- 继承子表残留\n"
        "  示例:SELECT * FROM tenant_graph.payment_p2007_01 LIMIT 10;\n"
        "tenant_graph.store(store_id(integer))"
    )
    out = drop_schema_hint_tables(hint, {("tenant_graph", "payment_p2007_01")})
    assert "payment_p2007_01" not in out
    assert "tenant_graph.rental" in out and "示例:SELECT * FROM tenant_graph.rental" in out
    assert out.splitlines()[-1] == "tenant_graph.store(store_id(integer))"


def test_project_carries_full_known_tables(
    graph_tables: list[GraphTable], graph_fks: list[GraphForeignKey]
) -> None:
    """子图只含召见表,但 known_tables 携带全图键:调用方据此区分未召回与不存在。"""
    graph = infer_roles(graph_tables, graph_fks)
    sub = graph.project([(SCHEMA, "rental")], {})
    assert [t.key for t in sub.tables] == [(SCHEMA, "rental")]
    assert sub.known_tables == tuple(sorted(t.key for t in graph.tables))
    assert (SCHEMA, "store") in sub.known_tables


def test_align_hint_drops_only_stale_tables_in_managed_schemas() -> None:
    """图完整时只剔管辖 schema 内的图外表;管辖外 schema 的知识表保留。"""
    graph = infer_roles([_table("rental", [("rental_id", "integer")], ["rental_id"])], [])
    hint = (
        "tenant_graph.rental(rental_id(integer)) -- 租赁\n"
        "tenant_graph.payment_p2007_01(amount(numeric)) -- 继承子表残留\n"
        "public.legacy(id(integer)) -- 管辖外"
    )
    out, recalled = _align_hint_with_graph(hint, schema_hint_tables(hint), graph)
    assert "payment_p2007_01" not in out
    assert "public.legacy" in out
    assert [key for key, _ in recalled] == [
        ("tenant_graph", "rental"),
        ("public", "legacy"),
    ]


def test_align_hint_noop_when_graph_incomplete() -> None:
    """图不完整时不做剔除,避免把未知状态当残留。"""
    base = infer_roles([_table("rental", [("rental_id", "integer")], ["rental_id"])], [])
    graph = SchemaGraph(
        tables=base.tables,
        foreign_keys=base.foreign_keys,
        complete=False,
    )
    hint = (
        "tenant_graph.rental(rental_id(integer))\n"
        "tenant_graph.payment_p2007_01(amount(numeric)) -- 残留"
    )
    recalled = schema_hint_tables(hint)
    out, kept = _align_hint_with_graph(hint, recalled, graph)
    assert out == hint
    assert kept == recalled


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


async def test_description_query_failure_rolls_back() -> None:
    """可选说明查询失败后必须回滚,否则同请求后续检索全部被中止事务拖垮。"""

    class _FailingConn:
        rolled_back = False

        async def execute(self, *_args: object, **_kwargs: object) -> object:
            raise RuntimeError("boom")

        async def rollback(self) -> None:
            self.rolled_back = True

    conn = _FailingConn()
    assert (
        await _load_descriptions(
            cast("Any", conn), tenant_id="t", datasource_key="default", schemas=[SCHEMA]
        )
        == {}
    )
    assert conn.rolled_back is True


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
        headers={**INTERNAL, "Accept-Encoding": "gzip"},
    )
    assert r.status_code == 200, r.text
    # 全图响应按内容协商压缩;httpx 自动解码,r.json() 仍是原结构
    assert r.headers.get("content-encoding") == "gzip"
    body = r.json()
    graph = body["schema_graph"]
    assert graph is not None and graph["complete"] is True
    assert graph["known_tables"]
    names = {t["table_name"] for t in graph["tables"]}
    assert {"rental", "payment", "staff", "store"} <= names
    roles = {t["table_name"]: t["role"] for t in graph["tables"]}
    assert roles["rental"] == "event" and roles["payment"] == "amount"
    comments = {t["table_name"]: t["comment"] for t in graph["tables"]}
    assert comments["rental"] == "租赁记录"  # 数据库无注释时取租户 RAG 表说明
    assert comments["payment"] == "支付明细"
    assert "password" not in str(graph).lower()
    assert f"{SCHEMA}.staff" in body["schema_hint"]  # 桥接尾部追加
    assert f"JOIN {SCHEMA}.store ON" in body["relations_hint"]


async def test_graph_ignores_team_owned_description(
    client: AsyncClient, session: AsyncSession, fake_embed: None
) -> None:
    """租户级图缓存不得合并团队说明,否则非成员可经图看到团队文案。"""
    tid = await _tenant(session)
    team_id = str(
        (
            await session.execute(
                text(
                    "INSERT INTO public.sys_team"
                    " (id, tenant_id, team_key, name, created_by)"
                    " VALUES (gen_random_uuid(), CAST(:t AS uuid), 'graph_team',"
                    "         'graph_team', gen_random_uuid()) RETURNING id"
                ),
                {"t": tid},
            )
        ).scalar_one()
    )
    await _seed(session, tid, "store", "团队专属门店说明")
    await session.execute(
        text(
            "UPDATE rag.table_descriptions SET team_id = CAST(:team AS uuid)"
            " WHERE tenant_id = CAST(:t AS uuid) AND table_name = 'store'"
        ),
        {"team": team_id, "t": tid},
    )
    await session.commit()
    key = f"km:raggraph:{tid}:default:{get_settings().rag_fulltext_ts_config}:and-cw2"
    await get_redis().delete(key)
    r = await client.post(
        "/internal/rag/retrieve",
        json={"tenant_key": "graph", "question": QUESTION},
        headers=INTERNAL,
    )
    assert r.status_code == 200, r.text
    assert "团队专属门店说明" not in r.text


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
