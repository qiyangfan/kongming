"""rag 用例层(009):上传/召回/抓取润色/重建/回写编排。

零框架依赖(宪法 Article II):持久化/内省/租户解析经 RagRepo 端口反转,
向量化经 EmbeddingPort、润色经 TableDescriberPort、缓存失效经 ContextCache。
编排纪律:
- 三路召回只查控制面库 rag 两表(tenant+ds 双过滤),不连业务库;
  fetch-metadata 的业务库内省在仓储层(introspect_all);
- 知识写操作(上传/删除/fetch/reindex)commit 后精确失效检索缓存(D6);
- embedding/润色失败降级不阻断(润色失败→纯结构;embedding 失败→全文路);
- 自动来源 upsert 受覆盖优先级约束(D5:不覆盖 MANUAL_*)。
"""

import asyncio
import logging
from dataclasses import dataclass

from kongming.rag.cache import ContextCache
from kongming.rag.models import (
    DatasourceInvalid,
    IntrospectedTable,
    KnowledgeEntry,
    RagContext,
    TableBlock,
    build_embedding_text,
    build_history_sql,
    build_schema_hint,
    can_automated_overwrite,
    rrf_fuse,
)
from kongming.rag.ports import EmbeddingError, EmbeddingPort, RagRepo, TableDescriberPort

log = logging.getLogger(__name__)

_DESCRIBE_CONCURRENCY = 4  # 逐表润色并发上限(防 LLM 端打爆)
_EMBED_BATCH = 64          # embed 单批上限(agent 端点契约)


@dataclass(frozen=True)
class FetchResult:
    found: int
    enriched: int
    skipped: int


@dataclass(frozen=True)
class ReindexResult:
    deleted: int
    rebuilt: int
    enriched: int


class TenantForbidden(Exception):
    """未绑定目标租户(或租户不存在/禁用——同形不区分,与 chat 同构)。"""


class RagService:
    def __init__(
        self,
        repo: RagRepo,
        *,
        embedder: EmbeddingPort,
        describer: TableDescriberPort,
        cache: ContextCache,
    ) -> None:
        self._repo = repo
        self._embedder = embedder
        self._describer = describer
        self._cache = cache

    # ============ 租户上下文(与 chat/options 同构:显式查白名单,缺省默认租户) ============

    async def resolve_tenant(self, user_id: str, tenant_key: str | None) -> tuple[str, str]:
        """X-Tenant-Id 解析(白名单复校);返回 (tenant_id, tenant_key)。"""
        if tenant_key is not None:
            bound = await self._repo.resolve_bound_tenant(user_id, tenant_key)
            if bound is None:
                raise TenantForbidden(tenant_key)
            return bound
        default = await self._repo.resolve_default_tenant(user_id)
        if default is None:
            raise TenantForbidden("<default>")
        return default

    # ============ 上传(文档链路) ============

    async def upload_blocks(
        self,
        *,
        actor_id: str,
        tenant_id: str,
        datasource_key: str,
        schema_name: str,
        blocks: list[TableBlock],
    ) -> list[str]:
        """解析后的表块批量落库(MANUAL_UPLOAD;人工显式操作=显式覆盖,不受优先级限)。"""
        await self._require_usable_datasource(tenant_id, datasource_key)
        vectors = await self._embed_quietly(
            [
                build_embedding_text(b.table_name, b.table_comment, b.columns_comment)
                for b in blocks
            ]
        )
        for block, vec in zip(blocks, vectors, strict=True):
            await self._repo.upsert_table(
                tenant_id=tenant_id,
                datasource_key=datasource_key,
                schema_name=schema_name,
                table_name=block.table_name,
                table_comment=block.table_comment,
                columns_comment=block.columns_comment,
                sample_queries=block.sample_queries,
                source_type="MANUAL_UPLOAD",
                embedding=vec,
            )
        await self._repo.log_oper(
            actor_id,
            "rag.upload",
            datasource_key,
            {"schema_name": schema_name, "tables": [b.table_name for b in blocks],
             "datasource_key": datasource_key},
        )
        await self._repo.commit()
        await self._cache.evict(tenant_id, datasource_key)
        return [b.table_name for b in blocks]

    # ============ 管理面:列表/删除 ============

    async def list_tables(
        self, *, tenant_id: str, datasource_key: str | None, limit: int, offset: int
    ) -> tuple[list[KnowledgeEntry], int]:
        return await self._repo.list_tables(
            tenant_id=tenant_id, datasource_key=datasource_key, limit=limit, offset=offset
        )

    async def delete_table(
        self, *, actor_id: str, tenant_id: str, table_id: str
    ) -> KnowledgeEntry | None:
        """硬删单条;不命中(不存在/他租户)返回 None(同形 404,防探测)。"""
        target = await self._repo.find_table(tenant_id=tenant_id, table_id=table_id)
        if target is None:
            return None
        deleted = await self._repo.delete_table(tenant_id=tenant_id, table_id=table_id)
        if not deleted:
            await self._repo.rollback()
            return None
        await self._repo.log_oper(
            actor_id, "rag.delete", table_id, {"table_name": target.table_name}
        )
        await self._repo.commit()
        await self._cache.evict(tenant_id, target.datasource_key)
        return target

    # ============ fetch-metadata / reindex(knowledge 域) ============

    async def fetch_metadata(
        self, *, actor_id: str, tenant_id: str, tenant_key: str, datasource_key: str
    ) -> FetchResult:
        await self._require_usable_datasource(tenant_id, datasource_key)
        try:
            tables = await self._repo.introspect_all(
                tenant_id=tenant_id, tenant_key=tenant_key, datasource_key=datasource_key
            )
        except LookupError:
            raise DatasourceInvalid(datasource_key) from None
        return await self._ingest(
            actor_id=actor_id,
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            tables=tables,
        )

    async def reindex(
        self, *, actor_id: str, tenant_id: str, tenant_key: str, datasource_key: str
    ) -> ReindexResult:
        """清空重建(显式语义:清掉人工精修回到自动生成基线,spec 已声明)。"""
        await self._require_usable_datasource(tenant_id, datasource_key)
        deleted = await self._repo.delete_all_for_datasource(
            tenant_id=tenant_id, datasource_key=datasource_key
        )
        try:
            tables = await self._repo.introspect_all(
                tenant_id=tenant_id, tenant_key=tenant_key, datasource_key=datasource_key
            )
        except LookupError:
            raise DatasourceInvalid(datasource_key) from None
        result = await self._ingest(
            actor_id=actor_id,
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            tables=tables,
            action="rag.reindex",
        )
        return ReindexResult(deleted=deleted, rebuilt=result.found, enriched=result.enriched)

    async def _ingest(
        self,
        *,
        actor_id: str,
        tenant_id: str,
        datasource_key: str,
        tables: list[IntrospectedTable],
        action: str = "rag.fetch_metadata",
    ) -> FetchResult:
        """润色→优先级 upsert→审计→commit→缓存失效。"""
        if not tables:
            await self._repo.log_oper(
                actor_id, action, datasource_key, {"found": 0, "enriched": 0, "skipped": 0}
            )
            await self._repo.commit()
            await self._cache.evict(tenant_id, datasource_key)
            return FetchResult(found=0, enriched=0, skipped=0)

        # 逐表润色(并发上限;单表失败降级纯结构不阻断整批)
        sem = asyncio.Semaphore(_DESCRIBE_CONCURRENCY)

        async def describe_one(t: IntrospectedTable) -> tuple[bool, TableBlock]:
            block = TableBlock(
                table_name=t.table_name,
                table_comment=t.table_comment,
                columns_comment=dict(t.columns_comment),
            )
            async with sem:
                return await self._describer.describe(block, t.schema_name)

        described = await asyncio.gather(*(describe_one(t) for t in tables))

        # 覆盖优先级:自动来源不覆盖 MANUAL_*(批量预读一次)
        existing = await self._repo.list_source_types(
            tenant_id=tenant_id, datasource_key=datasource_key
        )
        to_write: list[tuple[str, bool, TableBlock]] = []  # (schema_name, enriched, block)
        skipped = 0
        for t, (enriched, block) in zip(tables, described, strict=True):
            incoming = "AI_GENERATED" if enriched else "INTROSPECTION"
            prior = existing.get(block.table_name)
            if prior is not None and not can_automated_overwrite(prior, incoming):
                skipped += 1
                continue
            to_write.append((t.schema_name, enriched, block))

        vectors = await self._embed_quietly(
            [
                build_embedding_text(b.table_name, b.table_comment, b.columns_comment)
                for _, _, b in to_write
            ]
        )
        enriched_count = 0
        for (schema_name, enriched, block), vec in zip(to_write, vectors, strict=True):
            await self._repo.upsert_table(
                tenant_id=tenant_id,
                datasource_key=datasource_key,
                schema_name=schema_name,
                table_name=block.table_name,
                table_comment=block.table_comment,
                columns_comment=block.columns_comment,
                sample_queries=block.sample_queries,
                source_type="AI_GENERATED" if enriched else "INTROSPECTION",
                embedding=vec,
            )
            if enriched:
                enriched_count += 1

        await self._repo.log_oper(
            actor_id,
            action,
            datasource_key,
            {
                "found": len(tables),
                "enriched": enriched_count,
                "skipped": skipped,
                "datasource_key": datasource_key,
            },
        )
        await self._repo.commit()
        await self._cache.evict(tenant_id, datasource_key)
        return FetchResult(found=len(tables), enriched=enriched_count, skipped=skipped)

    async def _require_usable_datasource(self, tenant_id: str, datasource_key: str) -> None:
        """default 免校验(内置路径);非 default 存在+启用+归属(同形,防探测)。"""
        if datasource_key != "default" and not await self._repo.datasource_usable(
            datasource_key, tenant_id
        ):
            raise DatasourceInvalid(datasource_key)

    async def _embed_quietly(self, texts: list[str]) -> list[list[float] | None]:
        """批量向量化(分批≤64);失败返回全 None(无向量落库,全文路仍可用)。"""
        out: list[list[float] | None] = [None] * len(texts)
        try:
            for start in range(0, len(texts), _EMBED_BATCH):
                batch = texts[start : start + _EMBED_BATCH]
                vectors = await self._embedder.embed(batch)
                for i, v in enumerate(vectors):
                    out[start + i] = v
        except EmbeddingError:
            log.warning("embedding 失败,条目以无向量形态落库(全文路可用)")
            return [None] * len(texts)
        return out

    # ============ 召回(内部端点) ============

    async def recall(
        self, *, tenant_key: str, datasource_key: str, question: str
    ) -> RagContext:
        """三路召回:ctx 缓存→embedding→向量/全文/历史→RRF 融合。

        tenant+datasource 双过滤;embedding 失败降级纯全文路;不连业务库。
        """
        tenant_id = await self._repo.tenant_id_by_key(tenant_key)
        if tenant_id is None:
            raise LookupError(tenant_key)  # router 映射 404(与 meta 通道同语义)
        await self._require_usable_datasource(tenant_id, datasource_key)

        cached = await self._cache.get(tenant_id, datasource_key, question)
        if cached is not None:
            return cached

        vector: list[float] | None = None
        try:
            vector = (await self._embedder.embed([question]))[0]
        except EmbeddingError:
            log.warning("召回 embedding 失败,降级纯全文路")

        vector_hits = []
        history: list[str] = []
        if vector is not None:
            vector_hits = await self._repo.recall_vector(
                tenant_id=tenant_id, datasource_key=datasource_key, vector=vector
            )
            history = await self._repo.recall_history(
                tenant_id=tenant_id, datasource_key=datasource_key, vector=vector
            )
        fulltext_hits = await self._repo.recall_fulltext(
            tenant_id=tenant_id, datasource_key=datasource_key, question=question
        )
        fused = rrf_fuse([vector_hits, fulltext_hits])
        entries = await self._repo.fetch_entries(
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            keys=[(h.schema_name, h.table_name) for h in fused],
        )
        ctx = RagContext(
            schema_hint=build_schema_hint(entries),
            history_sql=build_history_sql(history),
        )
        # 空结果不写缓存:降级形态(embedding 失败/知识为空)缓存会让恢复后的
        # 服务在 TTL 窗口内持续返回空提示——空结果无缓存价值,下次请求重试全链路
        if ctx.schema_hint or ctx.history_sql:
            await self._cache.set(tenant_id, datasource_key, question, ctx)
        return ctx

    # ============ 回写(成功 SQL) ============

    async def writeback(
        self, *, tenant_id: str, datasource_key: str, question: str, sql_text: str
    ) -> None:
        """成功查询回写 query_history;失败仅告警(调用方独立 try,不阻断主流程)。

        embedding 复用 emb 缓存(相同问句零成本);embedding 失败仍回写(无向量行
        不参与向量召回,不构成错误 SQL 污染面)。
        """
        if not sql_text.strip():
            return
        vec: list[float] | None = None
        try:
            vec = (await self._embedder.embed([question]))[0]
        except EmbeddingError:
            log.warning("回写 embedding 失败,query_history 以无向量形态落库")
        try:
            await self._repo.insert_history(
                tenant_id=tenant_id,
                datasource_key=datasource_key,
                question=question,
                sql_text=sql_text,
                embedding=vec,
            )
            await self._repo.commit()
        except Exception:  # noqa: BLE001  # 回写永不阻断主流程
            log.warning("query_history 回写失败(不阻断主流程)", exc_info=True)
            await self._repo.rollback()
