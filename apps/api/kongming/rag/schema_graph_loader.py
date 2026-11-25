"""Schema 图采集:默认源走控制面会话,动态源走只读连接池;结果可按租户+数据源缓存。

采集只读 information_schema/pg_catalog,不读取业务行;图内不含连接地址或凭证。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from kongming.datasource.resolver import DatasourceInvalid, resolve_for_read
from kongming.internal.executor import get_or_create_engine
from kongming.rag.cache import ContextCache
from kongming.rag.catalog import allowed_schemas_for, is_reserved
from kongming.rag.schema_graph import (
    GraphColumn,
    GraphForeignKey,
    GraphTable,
    SchemaGraph,
    infer_roles,
)
from kongming.semantic.introspection import fetch_fk_pairs

log = logging.getLogger(__name__)

MAX_GRAPH_TABLES = 300


async def load_schema_graph(
    session: AsyncSession,
    *,
    tenant_id: str,
    tenant_key: str,
    datasource_key: str,
    cache: ContextCache | None = None,
) -> SchemaGraph | None:
    """按数据源采集结构化图;任何失败返回 None(召回保持旧响应)。"""
    if cache is not None:
        cached = await cache.get_schema_graph(tenant_id, datasource_key)
        if cached is not None:
            return cached
    try:
        graph = await _collect_graph(
            session,
            tenant_id=tenant_id,
            tenant_key=tenant_key,
            datasource_key=datasource_key,
        )
    except Exception:
        log.warning("schema 图采集失败(保持旧召回)", exc_info=True)
        return None
    if graph is not None and cache is not None:
        await cache.set_schema_graph(tenant_id, datasource_key, graph)
    return graph


async def _collect_graph(
    session: AsyncSession,
    *,
    tenant_id: str,
    tenant_key: str,
    datasource_key: str,
) -> SchemaGraph | None:
    if datasource_key == "default":
        schemas = allowed_schemas_for(tenant_key, None)
        return await _read_graph(session, schemas=schemas)
    try:
        row, plain = await resolve_for_read(
            session, tenant_id=tenant_id, datasource_key=datasource_key
        )
    except DatasourceInvalid:
        return None
    engine = await get_or_create_engine(
        datasource_key,
        URL.create(
            "postgresql+asyncpg",
            username=row.username,
            password=plain,
            host=row.host,
            port=row.port,
            database=row.database,
        ),
    )
    async with engine.connect() as conn:
        return await _read_graph(conn, schemas=allowed_schemas_for(tenant_key, row.default_schema))


async def _read_graph(
    conn: AsyncSession | AsyncConnection, *, schemas: list[str]
) -> SchemaGraph | None:
    tables = await _read_tables(conn, schemas=schemas)
    if not tables:
        return None
    complete = len(tables) <= MAX_GRAPH_TABLES
    selected = tables[:MAX_GRAPH_TABLES]
    keys = {t.key for t in selected}
    pks = await _read_primary_keys(conn, schemas=schemas)
    with_pk = [
        GraphTable(
            schema_name=t.schema_name,
            table_name=t.table_name,
            comment=t.comment,
            columns=t.columns,
            primary_key=tuple(pks.get(t.key, ())),
        )
        for t in selected
    ]
    graph = infer_roles(with_pk, await _read_foreign_keys(conn, schemas=schemas))
    if not complete:
        graph = SchemaGraph(tables=graph.tables, foreign_keys=graph.foreign_keys, complete=False)
    return graph if keys else None


async def _read_tables(
    conn: AsyncSession | AsyncConnection, *, schemas: list[str]
) -> list[GraphTable]:
    table_rows = (
        (
            await conn.execute(
                text(
                    "SELECT c.table_schema, c.table_name,"
                    "       obj_description(cls.oid, 'pg_class') AS table_comment"
                    " FROM information_schema.tables c"
                    " JOIN pg_class cls ON cls.relname = c.table_name"
                    " JOIN pg_namespace n ON n.oid = cls.relnamespace"
                    "   AND n.nspname = c.table_schema"
                    " WHERE c.table_schema = ANY(CAST(:schemas AS text[]))"
                    "   AND c.table_type = 'BASE TABLE'"
                    "   AND NOT EXISTS ("
                    "     SELECT 1 FROM pg_inherits inh WHERE inh.inhrelid = cls.oid"
                    "   )"
                    " ORDER BY c.table_schema, c.table_name"
                ),
                {"schemas": schemas},
            )
        )
        .mappings()
        .all()
    )
    visible = [r for r in table_rows if not is_reserved(str(r["table_name"]))]
    kept = {(str(r["table_schema"]), str(r["table_name"])) for r in visible}
    col_rows = (
        (
            await conn.execute(
                text(
                    "SELECT c.table_schema, c.table_name, c.column_name, c.data_type,"
                    "       col_description(cls.oid, a.attnum) AS column_comment"
                    " FROM information_schema.columns c"
                    " JOIN pg_class cls ON cls.relname = c.table_name"
                    " JOIN pg_namespace n ON n.oid = cls.relnamespace"
                    "   AND n.nspname = c.table_schema"
                    " JOIN pg_attribute a ON a.attrelid = cls.oid"
                    "   AND a.attname = c.column_name"
                    " WHERE c.table_schema = ANY(CAST(:schemas AS text[]))"
                    " ORDER BY c.table_schema, c.table_name, c.ordinal_position"
                ),
                {"schemas": schemas},
            )
        )
        .mappings()
        .all()
    )
    columns: dict[tuple[str, str], list[GraphColumn]] = defaultdict(list)
    for r in col_rows:
        key = (str(r["table_schema"]), str(r["table_name"]))
        if key in kept:
            columns[key].append(
                GraphColumn(
                    name=str(r["column_name"]),
                    data_type=str(r["data_type"]),
                    comment=str(r["column_comment"] or ""),
                )
            )
    return [
        GraphTable(
            schema_name=str(r["table_schema"]),
            table_name=str(r["table_name"]),
            comment=str(r["table_comment"] or ""),
            columns=tuple(columns.get((str(r["table_schema"]), str(r["table_name"])), [])),
        )
        for r in visible
    ]


async def _read_primary_keys(
    conn: AsyncSession | AsyncConnection, *, schemas: list[str]
) -> dict[tuple[str, str], list[str]]:
    rows = (
        (
            await conn.execute(
                text(
                    "SELECT tc.table_schema, tc.table_name, kcu.column_name"
                    " FROM information_schema.table_constraints tc"
                    " JOIN information_schema.key_column_usage kcu"
                    "   ON tc.constraint_name = kcu.constraint_name"
                    "  AND tc.table_schema = kcu.table_schema"
                    " WHERE tc.constraint_type = 'PRIMARY KEY'"
                    "   AND tc.table_schema = ANY(CAST(:schemas AS text[]))"
                    " ORDER BY tc.table_schema, tc.table_name, kcu.ordinal_position"
                ),
                {"schemas": schemas},
            )
        )
        .mappings()
        .all()
    )
    out: dict[tuple[str, str], list[str]] = defaultdict(list)
    for r in rows:
        out[(str(r["table_schema"]), str(r["table_name"]))].append(str(r["column_name"]))
    return out


async def _read_foreign_keys(
    conn: AsyncSession | AsyncConnection, *, schemas: list[str]
) -> list[GraphForeignKey]:
    pairs = await fetch_fk_pairs(conn, schemas=schemas)
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for p in pairs:
        grouped[
            (
                str(p["source_table"]),
                str(p["target_table"]),
                str(p.get("constraint_name") or ""),
            )
        ].append(p)
    out: list[GraphForeignKey] = []
    for (source, target, _constraint), cols in grouped.items():
        cols.sort(key=lambda x: int(cast("int", x.get("ordinal") or 0)))
        source_schema, _, source_table = source.partition(".")
        target_schema, _, target_table = target.partition(".")
        out.append(
            GraphForeignKey(
                source_schema=source_schema,
                source_table=source_table,
                source_columns=tuple(str(c["left_col"]) for c in cols),
                target_schema=target_schema,
                target_table=target_table,
                target_columns=tuple(str(c["right_col"]) for c in cols),
            )
        )
    return out
