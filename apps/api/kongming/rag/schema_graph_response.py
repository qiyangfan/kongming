"""内部召回的结构化图响应装配:图/桥接/关系段与缓存,不承担 HTTP 映射。"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from kongming.config import get_settings
from kongming.rag.cache import ContextCache
from kongming.rag.schema_graph import (
    BridgePath,
    SchemaGraph,
    TableKey,
    drop_schema_hint_tables,
    format_join,
    format_table_hint,
    schema_hint_tables,
    select_bridge_tables,
)
from kongming.rag.schema_graph_loader import load_schema_graph
from kongming.rag.schemas import (
    SchemaGraphColumnOut,
    SchemaGraphForeignKeyOut,
    SchemaGraphOut,
    SchemaGraphTableOut,
)
from kongming.redis_client import get_redis

log = logging.getLogger(__name__)


def graph_out(graph: SchemaGraph) -> SchemaGraphOut:
    """领域图 → 响应模型(只含结构,不带凭据)。"""
    known_tables = graph.known_tables or tuple(t.key for t in graph.tables)
    return SchemaGraphOut(
        tables=[
            SchemaGraphTableOut(
                schema_name=t.schema_name,
                table_name=t.table_name,
                comment=t.comment,
                columns=[
                    SchemaGraphColumnOut(name=c.name, data_type=c.data_type, comment=c.comment)
                    for c in t.columns
                ],
                primary_key=list(t.primary_key),
                role=t.role,
                source=t.source,
            )
            for t in graph.tables
        ],
        foreign_keys=[
            SchemaGraphForeignKeyOut(
                source_schema=fk.source_schema,
                source_table=fk.source_table,
                source_columns=list(fk.source_columns),
                target_schema=fk.target_schema,
                target_table=fk.target_table,
                target_columns=list(fk.target_columns),
            )
            for fk in graph.foreign_keys
        ],
        complete=graph.complete,
        known_tables=[f"{schema}.{table}" for schema, table in known_tables],
    )


def merge_hint_lines(first: str, second: str) -> str:
    """关系段合并去重(语义层既有行为在前,图派生路径尾部追加)。"""
    seen: set[str] = set()
    out: list[str] = []
    for line in (first, second):
        for item in line.splitlines():
            text = item.strip()
            if text and text not in seen:
                seen.add(text)
                out.append(text)
    return "\n".join(out)


def _apply_bridges(
    graph: SchemaGraph, bridges: list[BridgePath], keys: list[TableKey]
) -> tuple[list[str], list[str]]:
    extra_hint: list[str] = []
    extra_relations: list[str] = []
    for bridge in bridges:
        table = graph.table(bridge.key)
        if table is None:
            continue
        extra_hint.append(format_table_hint(table))
        extra_relations.extend(format_join(edge) for edge in bridge.edges)
        keys.append(bridge.key)
    return extra_hint, extra_relations


def _append_bridges(schema_hint: str, extra_hint: list[str], bridges: list[BridgePath]) -> str:
    if not extra_hint:
        return schema_hint
    log.info(
        "schema 图桥接 trace: %s",
        [
            {
                "table": f"{bridge.key[0]}.{bridge.key[1]}",
                "via": "->".join(
                    f"{edge.source_table}->{edge.target_table}" for edge in bridge.edges
                ),
            }
            for bridge in bridges
        ],
    )
    return schema_hint.rstrip() + "\n" + "\n".join(extra_hint)


def _align_hint_with_graph(
    schema_hint: str, recalled: list[tuple[TableKey, str]], graph: SchemaGraph
) -> tuple[str, list[tuple[TableKey, str]]]:
    """图完整时剔除提示词中的图外表(知识条目残留),让下发子图与提示词一致。

    残留表既会诱导生成,又会让子图被判定为不完整,导致语义硬规则整体跳过。
    """
    if not graph.complete:
        return schema_hint, recalled
    # 图只覆盖允许的 schema:先按 schema 范围圈定管辖面,再判表级残留;
    # 范围外 schema 的表(如知识里别的 schema)图无从判断,保留
    known_schemas = {t.schema_name for t in graph.tables}
    known = {t.key for t in graph.tables}
    stale = [key for key, _ in recalled if key[0] in known_schemas and key not in known]
    if not stale:
        return schema_hint, recalled
    log.info(
        "schema_hint 剔除图外表(已删除或继承子表): %s",
        sorted(f"{schema}.{table}" for schema, table in stale),
    )
    return (
        drop_schema_hint_tables(schema_hint, set(stale)),
        # 管辖外 schema 无法由图判断:召回项必须与提示词一起保留,
        # 否则模型按提示词用了该表,语义护栏却按「不在已知表」硬拒
        [(key, note) for key, note in recalled if key in known or key[0] not in known_schemas],
    )


async def build_graph_hints(
    session: AsyncSession,
    *,
    tenant_id: str | None,
    tenant_key: str,
    datasource_key: str,
    question: str,
    schema_hint: str,
) -> tuple[str, str, SchemaGraphOut | None]:
    """组装结构化图与桥接表;图不可用/不完整时保持既有 schema_hint 与关系段。"""
    settings = get_settings()
    if not settings.rag_schema_graph_enabled or not schema_hint.strip() or tenant_id is None:
        return schema_hint, "", None
    graph = await load_schema_graph(
        session,
        tenant_id=tenant_id,
        tenant_key=tenant_key,
        datasource_key=datasource_key,
        cache=ContextCache(get_redis(), ts_config=settings.rag_fulltext_ts_config),
    )
    if graph is None:
        return schema_hint, "", None
    recalled = schema_hint_tables(schema_hint)
    schema_hint, recalled = _align_hint_with_graph(schema_hint, recalled, graph)
    if not recalled:
        return schema_hint, "", None
    keys = [key for key, _ in recalled]
    bridges = (
        select_bridge_tables(
            graph,
            keys,
            question,
            max_bridges=settings.rag_schema_graph_bridge_max,
        )
        if graph.complete
        else []
    )
    extra_hint, extra_relations = _apply_bridges(graph, bridges, keys)
    hint = _append_bridges(schema_hint, extra_hint, bridges)
    # 语义预检需要完整外键图:子图会漏掉桥接/对端表,INNER JOIN 丢行无法证明。
    # schema_hint 仍只含召回与桥接表;agent 提示词按 hint 过滤图,不膨胀。
    return hint, "\n".join(dict.fromkeys(extra_relations)), graph_out(graph)
