"""semantic 用例编排(023):introspection/注入组装/CRUD。零框架纪律。"""

import logging
from typing import cast

from kongming.semantic.models import (
    CallerScope,
    MetricDef,
    MetricHit,
    TableRelation,
    format_metric_hints,
    format_relations_hint,
    match_metrics,
    project_metric_hits,
)
from kongming.semantic.ports import (
    CallerDeptScopeResolver,
    FkIntrospector,
    KnowledgeTableLookup,
    RecallContextEvictor,
    SemanticRepo,
)

log = logging.getLogger(__name__)


class RelationTableUnknown(Exception):
    """手工关系引用知识条目外的表(040 写边界闸,400 RELATION_TABLE_UNKNOWN)。"""


class SemanticService:
    def __init__(
        self,
        repo: SemanticRepo,
        knowledge: KnowledgeTableLookup | None = None,
        recall_ctx_evictor: RecallContextEvictor | None = None,
        caller_scope_resolver: CallerDeptScopeResolver | None = None,
    ) -> None:
        self._repo = repo
        self._knowledge = knowledge
        self._recall_ctx_evictor = recall_ctx_evictor
        self._caller_scope = caller_scope_resolver

    async def _evict_recall_ctx(self, *, tenant_id: str, datasource_key: str) -> None:
        """095:关系写提交后失效该租户+数据源的检索上下文缓存(正确性要求,
        非优化)。实现(ContextCache)自身 fail-open,失效失败只告警。"""
        if self._recall_ctx_evictor is None:
            return
        await self._recall_ctx_evictor.evict(tenant_id, datasource_key)

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
        source=manual 并提交;提交后失效检索上下文缓存(095)。"""
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
        await self._evict_recall_ctx(tenant_id=tenant_id, datasource_key=datasource_key)
        return row_id

    async def delete_relation(self, *, relation_id: str, tenant_id: str) -> bool:
        """手工关系删除(095 自 router 归位):不命中返回 False(同形 404 由
        调用方映射);命中则删除并提交,提交后按被删行数据源精确失效检索
        上下文缓存(095)。"""
        datasource_key = await self._repo.delete_relation(
            relation_id=relation_id, tenant_id=tenant_id
        )
        if datasource_key is None:
            return False
        await self._repo.commit()
        await self._evict_recall_ctx(tenant_id=tenant_id, datasource_key=datasource_key)
        return True

    async def introspect(
        self, *, tenant_id: str, datasource_key: str, introspector: FkIntrospector
    ) -> int:
        """外键扫描幂等 upsert(按约束分组聚合多列 join_keys);返回关系条数。
        提交后失效检索上下文缓存(095:同问句再召回即见新关系)。"""
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
        await self._evict_recall_ctx(tenant_id=tenant_id, datasource_key=datasource_key)
        return n

    async def enrich(
        self,
        *,
        tenant_id: str,
        datasource_key: str,
        question: str,
        tables: list[str],
        caller_user_id: str | None = None,
    ) -> tuple[str, str, list[MetricHit]]:
        """召回注入段组装(spec:仅召回集合内关系 + 命中指标 top-5)。

        076:命中列表不再丢弃——结构化投影(标识/名称/口径/修订时间)与派生
        文字段同源返回;文字段 SHALL 由结构化命中派生(单一来源)。
        110:指标候选集先过调用者可见性再命中——None 身份/端口未注入=仅 ALL 档
        (「未知调用者」,与 102 缺省纯公共同哲学,不设绕过面);ALL 档=全量
        (过滤参数 None);其余按部门集合下推 SQL 单点。enrich 位于检索上下文
        缓存之外(076 地形),本过滤零缓存键改动。"""
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
        # 110:解析调用者有效范围。解析失败按「未知调用者」读面降级(仅 ALL 档),
        # 与 CUSTOM 空集合同向——可见性过滤方向永远是收紧,不阻断召回主链路。
        scope = CallerScope(universal=False)
        if caller_user_id is not None and self._caller_scope is not None:
            try:
                scope = await self._caller_scope.resolve(
                    user_id=caller_user_id, tenant_id=tenant_id
                )
            except Exception:  # noqa: BLE001  读面降级:判定故障不得放大为召回故障
                log.warning(
                    "指标可见性解析失败,按仅 ALL 档降级",
                    exc_info=True,
                    extra={"user": caller_user_id},
                )
                scope = CallerScope(universal=False)
        metric_rows = await self._repo.list_metrics(
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            visible_dept_ids=None if scope.universal else list(scope.dept_ids),
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
