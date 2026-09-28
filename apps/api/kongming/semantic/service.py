"""semantic 用例编排(023):introspection/注入组装/CRUD。零框架纪律。"""

import logging
from typing import cast

from kongming.semantic.models import (
    MetricDef,
    MetricHit,
    TableRelation,
    format_metric_hints,
    format_relations_hint,
    match_metrics,
    project_metric_hits,
)
from kongming.semantic.ports import FkIntrospector, KnowledgeTableLookup, SemanticRepo

log = logging.getLogger(__name__)


class RelationTableUnknown(Exception):
    """手工关系引用知识条目外的表(040 写边界闸,400 RELATION_TABLE_UNKNOWN)。"""


class SemanticService:
    def __init__(
        self,
        repo: SemanticRepo,
        knowledge: KnowledgeTableLookup | None = None,
    ) -> None:
        self._repo = repo
        self._knowledge = knowledge

    async def add_relation(
        self,
        *,
        tenant_id: str,
        datasource_key: str,
        source_table: str,
        target_table: str,
        join_keys: list[dict[str, str]],
    ) -> str:
        """手工关系(040 自 router 归位:用例逻辑入服务层)。

        写边界闸:source/target 均须为既有知识条目——引用条目外的表在召回
        relations_hint 中永不命中(静默死配置),创建期即拒。通过后落库
        source=manual 并提交。"""
        if self._knowledge is not None:
            for st in (source_table, target_table):
                if not await self._knowledge.knowledge_table_exists(
                    tenant_id=tenant_id,
                    datasource_key=datasource_key,
                    schema_table=st,
                ):
                    raise RelationTableUnknown(st)
        row_id = await self._repo.add_relation(
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            rel={
                "source_table": source_table,
                "target_table": target_table,
                "join_keys": join_keys,
            },
        )
        await self._repo.commit()
        return row_id

    async def introspect(
        self, *, tenant_id: str, datasource_key: str, introspector: FkIntrospector
    ) -> int:
        """外键扫描幂等 upsert(按约束分组聚合多列 join_keys);返回关系条数。"""
        pairs = await introspector.fetch_fk_pairs(schemas=[])
        # 按 (source_table, target_table) 聚合多列外键
        grouped: dict[tuple[str, str], list[dict[str, str]]] = {}
        for p in pairs:
            key = (str(p["source_table"]), str(p["target_table"]))
            grouped.setdefault(key, []).append(
                {"left_col": str(p["left_col"]), "right_col": str(p["right_col"])}
            )
        rels = [
            {"source_table": st, "target_table": tt, "join_keys": keys}
            for (st, tt), keys in grouped.items()
        ]
        n = await self._repo.upsert_introspection(
            tenant_id=tenant_id, datasource_key=datasource_key, rels=rels
        )
        await self._repo.commit()
        return n

    async def enrich(
        self, *, tenant_id: str, datasource_key: str, question: str, tables: list[str]
    ) -> tuple[str, str, list[MetricHit]]:
        """召回注入段组装(spec:仅召回集合内关系 + 命中指标 top-5)。

        076:命中列表不再丢弃——结构化投影(标识/名称/口径/修订时间)与派生
        文字段同源返回;文字段 SHALL 由结构化命中派生(单一来源)。"""
        relations: list[TableRelation] = []
        # 非空即查(023①:单表自引用 FK 也注入——ANY(:ts) 天然收窄,单表
        # 集合只命中自引用行,多表行为不变)
        if tables:
            rows = await self._repo.relations_within(
                tenant_id=tenant_id, datasource_key=datasource_key, tables=tables
            )
            relations = [
                TableRelation(
                    source_table=str(r["source_table"]),
                    target_table=str(r["target_table"]),
                    join_keys=list(r["join_keys"]),
                    source=str(r["source"]),
                )
                for r in rows
            ]
        metric_rows = await self._repo.list_metrics(
            tenant_id=tenant_id, datasource_key=datasource_key
        )
        metrics = [
            MetricDef(
                metric_name=str(r["metric_name"]),
                sql_template=str(r["sql_template"]),
                business_terms=[str(t) for t in cast("list[object]", r["business_terms"] or [])],
                enabled=bool(r["enabled"]),
                metric_id=str(r["id"]),
                updated_at=r["updated_at"],
            )
            for r in metric_rows
        ]
        hits = match_metrics(question, metrics)
        return (
            format_relations_hint(relations),
            format_metric_hints(hits),
            project_metric_hits(hits),
        )
