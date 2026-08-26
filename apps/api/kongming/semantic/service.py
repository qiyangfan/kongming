"""semantic 用例编排(023):introspection/注入组装/CRUD。零框架纪律。"""

import logging

from kongming.semantic.models import (
    MetricDef,
    TableRelation,
    format_metric_hints,
    format_relations_hint,
    match_metrics,
)
from kongming.semantic.ports import FkIntrospector, SemanticRepo

log = logging.getLogger(__name__)


class SemanticService:
    def __init__(self, repo: SemanticRepo) -> None:
        self._repo = repo

    async def introspect(
        self, *, tenant_id: str, datasource_key: str, introspector: FkIntrospector
    ) -> int:
        """外键抓取幂等 upsert(按约束分组聚合多列 join_keys);返回关系条数。"""
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
    ) -> tuple[str, str]:
        """召回注入段组装(spec:仅召回集合内关系 + 命中指标 top-5)。"""
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
                business_terms=[str(t) for t in r["business_terms"] or []],
                enabled=bool(r["enabled"]),
            )
            for r in metric_rows
        ]
        hits = match_metrics(question, metrics)
        return format_relations_hint(relations), format_metric_hints(hits)
