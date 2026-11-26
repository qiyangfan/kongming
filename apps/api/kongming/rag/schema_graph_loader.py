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
        descriptions = await _load_descriptions(
            session, tenant_id=tenant_id, datasource_key=datasource_key, schemas=schemas
        )
        return await _read_graph(session, schemas=schemas, descriptions=descriptions)
    try:
        row, plain = await resolve_for_read(
            session, tenant_id=tenant_id, datasource_key=datasource_key
        )
    except DatasourceInvalid:
        return None
    schemas = allowed_schemas_for(tenant_key, row.default_schema)
    descriptions = await _load_descriptions(
        session, tenant_id=tenant_id, datasource_key=datasource_key, schemas=schemas
    )
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
        return await _read_graph(conn, schemas=schemas, descriptions=descriptions)


async def _load_descriptions(
    conn: AsyncSession | AsyncConnection,
    *,
    tenant_id: str,
    datasource_key: str,
    schemas: list[str],
) -> dict[tuple[str, str], dict[str, object]]:
    """读取租户公共 RAG 表说明/别名,作为数据库注释缺失时的图元数据。

    图按租户级缓存并跨用户复用,团队归属说明不得并入(否则非成员可经图
    看到团队文案);团队可见内容仍由召回面按调用者团队返回。只读控制面
    知识表,不进凭证;失败按无说明降级,不影响图采集。
    """
    try:
        rows = (
            (
                await conn.execute(
                    text(
                        "SELECT schema_name, table_name, table_comment, columns_comment,"
                        "       COALESCE(alias, '') AS alias"
                        " FROM rag.table_descriptions"
                        " WHERE tenant_id = CAST(:t AS uuid)"
                        "   AND datasource_key = :d"
                        "   AND schema_name = ANY(CAST(:schemas AS text[]))"
                        "   AND team_id IS NULL"
                    ),
                    {"t": tenant_id, "d": datasource_key, "schemas": schemas},
                )
            )
            .mappings()
            .all()
        )
    except Exception:
        log.warning("schema 图读取知识表说明失败(按无说明降级)", exc_info=True)
        # PostgreSQL 语句错误会中止当前事务;不 rollback 会让同请求后续检索全部失败
        try:
            await conn.rollback()
        except Exception:
            log.warning("schema 图说明查询失败后回滚失败", exc_info=True)
        return {}
    out: dict[tuple[str, str], dict[str, object]] = {}
    for r in rows:
        columns_raw = r["columns_comment"]
        columns = cast("dict[str, object]", columns_raw) if isinstance(columns_raw, dict) else {}
        out[(str(r["schema_name"]), str(r["table_name"]))] = {
            "table_comment": str(r["table_comment"] or ""),
            "columns_comment": columns,
            "alias": str(r["alias"] or ""),
        }
    return out


async def _read_graph(
    conn: AsyncSession | AsyncConnection,
    *,
    schemas: list[str],
    descriptions: dict[tuple[str, str], dict[str, object]] | None = None,
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
            comment=_merged_table_comment(t, descriptions or {}),
            columns=_merged_columns(t, descriptions or {}),
            primary_key=tuple(pks.get(t.key, ())),
        )
        for t in selected
    ]
    graph = infer_roles(with_pk, await _read_foreign_keys(conn, schemas=schemas))
    if not complete:
        graph = SchemaGraph(tables=graph.tables, foreign_keys=graph.foreign_keys, complete=False)
    return graph if keys else None


def _merged_table_comment(
    table: GraphTable, descriptions: dict[tuple[str, str], dict[str, object]]
) -> str:
    desc = descriptions.get(table.key)
    if desc is None:
        return table.comment
    alias = str(desc.get("alias") or "").strip()
    base = table.comment.strip() or str(desc.get("table_comment") or "").strip()
    if not alias or alias in base:
        return base or alias
    return f"{alias}（{base}）" if base else alias


def _merged_columns(
    table: GraphTable, descriptions: dict[tuple[str, str], dict[str, object]]
) -> tuple[GraphColumn, ...]:
    desc = descriptions.get(table.key)
    if desc is None:
        return table.columns
    raw = desc.get("columns_comment")
    if not isinstance(raw, dict):
        return table.columns
    columns_comment = cast("dict[str, object]", raw)
    out: list[GraphColumn] = []
    for column in table.columns:
        extra = columns_comment.get(column.name)
        if column.comment or not isinstance(extra, str):
            out.append(column)
            continue
        text = extra.strip()
        dtype = column.data_type.lower()
        if text.lower().startswith(dtype):
            text = text[len(dtype) :].strip()
        out.append(
            GraphColumn(
                name=column.name,
                data_type=column.data_type,
                comment=text,
            )
        )
    return tuple(out)


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
