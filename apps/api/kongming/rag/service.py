"""rag 用例层(009):上传/召回/扫描润色/重建/回写编排。

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
from datetime import UTC, datetime, timedelta
from typing import Any

from kongming.rag.cache import ContextCache
from kongming.rag.gate import GateResult, gate_hits
from kongming.rag.models import (
    COVERAGE_FAILURE_WINDOW_DAYS,
    RECALL_TOP_K,
    CoverageReport,
    DatasourceInvalid,
    HistorySample,
    IntrospectedTable,
    KnowledgeEntry,
    KnowledgeNotFound,
    RagContext,
    RecallHit,
    RelationExpansion,
    SchemaNotAllowed,
    TableBlock,
    TeamWriteForbidden,
    build_coverage_report,
    build_embedding_text,
    build_history_sql,
    build_schema_hint,
    can_automated_overwrite,
    entry_write_access,
    expand_by_relations,
    rrf_fuse,
)
from kongming.rag.ports import (
    EmbeddingError,
    EmbeddingPort,
    MemberRoleReader,
    MemberTeamsReader,
    RagRepo,
    RelationExpansionPort,
    SuggestionCacheEvictor,
    TableDescriberPort,
)

log = logging.getLogger(__name__)

_DESCRIBE_CONCURRENCY = 4  # 逐表润色并发上限(防 LLM 端打爆)
_EMBED_BATCH = 64  # embed 单批上限(agent 端点契约)
_RECALL_TOTAL_CAP = RECALL_TOP_K * 2


@dataclass(frozen=True)
class FetchResult:
    found: int
    enriched: int
    skipped: int
    removed: int = 0  # 042:对账删除的扫描集外自动来源行数


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
        suggestion_evictor: SuggestionCacheEvictor | None = None,
        gate_settings: Any = None,  # 064:闸门配置(Settings;None=测试轻量装配,组合根恒注入)
        relation_expander: RelationExpansionPort | None = None,  # 095:组合根注入
        expand_enabled: bool = False,  # 095:构造缺省关(未注入端口的调用方零行为)
        expand_max: int = 2,
        member_teams: MemberTeamsReader | None = None,  # 102:可见集合(未注入=纯公共)
        member_roles: MemberRoleReader | None = None,  # 102:写权限三态(未注入=非成员)
    ) -> None:
        self._repo = repo
        self._embedder = embedder
        self._describer = describer
        self._cache = cache
        self._suggestion_evictor = suggestion_evictor
        self._gate = gate_settings
        self._relation_expander = relation_expander
        self._expand_enabled = expand_enabled
        self._expand_max = max(0, expand_max)
        self._member_teams = member_teams
        self._member_roles = member_roles
        # 064:按路闸门观测(main=主路 / prior=047 继承路);recall 消费后
        # 原样下发日志,不静默
        self._last_gate_trace: dict[str, object] = {}

    def _gate_configs(self) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """064:闸门配置提取单点(两路判据参数;未注入=None——测试轻量装配
        形态,不判不裁)。

        主链路 _apply_gate 与试跑端点 recall_probe 共用——阈值口径两处
        一致,禁各起一套 getattr。向量路/全文路量纲不同(余弦相似度 vs
        ts_rank),阈值按路独立;min_count/mean_window 为路无关参数共用。"""
        if self._gate is None:
            return None
        shared: dict[str, Any] = {
            "min_count": int(getattr(self._gate, "rag_gate_min_count", 1)),
            "mean_window": int(getattr(self._gate, "rag_gate_mean_window", 3)),
        }
        vec = {
            "top1_threshold": float(getattr(self._gate, "rag_gate_vector_top1", 0.0)),
            "mean_threshold": float(getattr(self._gate, "rag_gate_vector_mean", 0.0)),
            **shared,
        }
        ft = {
            "top1_threshold": float(getattr(self._gate, "rag_gate_fulltext_top1", 0.0)),
            "mean_threshold": float(getattr(self._gate, "rag_gate_fulltext_mean", 0.0)),
            **shared,
        }
        return vec, ft

    async def _evict_suggestions(self, datasource_key: str) -> None:
        """047:建议缓存级联失效(知识写路径;DELETE 与业务写同事务,由
        本次 commit 一并收口)。未注入(轻量装配/既有测试)时零行为。"""
        if self._suggestion_evictor is None:
            return
        await self._suggestion_evictor.delete_by_datasource(datasource_key)

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

    # ============ 102 团队归属:可见集合与写权限单点 ============

    async def _caller_team_ids(self, caller_user_id: str | None) -> list[str]:
        """调用者团队集合(可见集合与缓存指纹共用单点);无身份/未注入端口=
        空集合=纯公共形态(既有调用方零回归)。"""
        if not caller_user_id or self._member_teams is None:
            return []
        return sorted(await self._member_teams.team_ids_of_user(caller_user_id))

    async def _assert_entry_writable(
        self, target: KnowledgeEntry, actor_id: str, tenant_id: str
    ) -> None:
        """团队条目写闸(102):非成员 404 同形(KnowledgeNotFound,不暴露
        存在性);VIEWER 403(TeamWriteForbidden);公共条目=既有权限口径,
        零额外闸(存量零回归)。未注入角色端口按非成员处理(fail-closed)。
        角色查询租户限定:跨租户团队身份不参与判定。"""
        if target.team_id is None:
            return
        role = (
            await self._member_roles.role_of(tenant_id, target.team_id, actor_id)
            if self._member_roles is not None
            else None
        )
        verdict = entry_write_access(team_id=target.team_id, member_role=role)
        if verdict == "not_member":
            raise KnowledgeNotFound(target.id)
        if verdict == "viewer":
            raise TeamWriteForbidden(target.team_id)

    # ============ 上传(文档链路) ============

    async def upload_blocks(
        self,
        *,
        actor_id: str,
        tenant_id: str,
        tenant_key: str,
        datasource_key: str,
        schema_name: str,
        blocks: list[TableBlock],
        team_id: str | None = None,  # 102:上传归属(空=公共;既有调用零回归)
    ) -> list[str]:
        """解析后的表块批量落库(MANUAL_UPLOAD;人工显式操作=显式覆盖,不受优先级限;
        alias 例外——parser 无别名语法,清空非用户显式意图,upsert 只补空不改非空)。

        102 归属:显式携带 team_id 时校验操作者为该团队 OWNER/MEMBER
        (非成员 404 同形、VIEWER 403),写入即归属;缺省不携带=无归属意图
        ——新建行为公共,既有条目归属原位保留(不静默公共化,防可见范围被
        上传意外扩大)。既有条目写闸:四元冲突命中团队条目时,无论是否携带
        归属,均按既有条目归属校验操作者成员身份(spec「写操作(上传/…)
        对团队条目 SHALL 额外校验」)——防借覆盖夺取他团队条目(内容乃至
        归属);公共条目/新条目走既有口径。"""
        await self._require_usable_datasource(tenant_id, datasource_key)
        if team_id is not None:
            await self._assert_team_writable(team_id, actor_id, tenant_id)
        # 040 写边界闸:语法合法 ≠ 集合成员,越界 schema 的条目永不命中召回,
        # 错误会后置到提问期——存在性校验先于 embedding 等贵活
        allowed = await self._repo.allowed_schemas(
            tenant_id=tenant_id, tenant_key=tenant_key, datasource_key=datasource_key
        )
        if schema_name not in allowed:
            raise SchemaNotAllowed(schema_name)
        # 审查循环 F1:embedding 文本须以「生效别名」参与(与 alias 列/tsv 同源
        # ——upsert 保留既有非空 alias 时,无别名文本的新向量会使别名向量召回
        # 静默退化);与 _ingest 同款预读,042 二元键只读本 schema 同名表
        existing = await self._repo.list_source_and_alias(
            tenant_id=tenant_id, datasource_key=datasource_key
        )
        # 102 既有条目写闸(贵活之前):团队条目非成员/VIEWER 先拒
        for b in blocks:
            prior = existing.get((schema_name, b.table_name))
            if prior is not None and prior[2] is not None:
                await self._assert_team_writable(prior[2], actor_id, tenant_id)
        vectors = await self._embed_quietly(
            [
                build_embedding_text(
                    b.table_name,
                    b.table_comment,
                    b.columns_comment,
                    alias=(existing.get((schema_name, b.table_name), ("", ""))[1] or b.alias),
                )
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
                alias=block.alias,  # 037
                team_id=team_id,
                set_team_id=team_id is not None,  # 102:显式归属才写,缺省保留既有
            )
        await self._repo.log_oper(
            actor_id,
            "rag.upload",
            datasource_key,
            {
                "schema_name": schema_name,
                "tables": [b.table_name for b in blocks],
                "datasource_key": datasource_key,
                "team_id": team_id,  # 102
            },
        )
        await self._evict_suggestions(datasource_key)  # 047 级联失效(同事务)
        await self._repo.commit()
        await self._cache.evict(tenant_id, datasource_key)
        return [b.table_name for b in blocks]

    async def _assert_team_writable(self, team_id: str, actor_id: str, tenant_id: str) -> None:
        """目标团队写闸(102:上传归属/归属变更指向团队时):操作者须该团队
        OWNER/MEMBER 且团队属于当前租户;非成员 404 同形(不暴露团队存在性)、
        VIEWER 403。"""
        role = (
            await self._member_roles.role_of(tenant_id, team_id, actor_id)
            if self._member_roles is not None
            else None
        )
        verdict = entry_write_access(team_id=team_id, member_role=role)
        if verdict == "not_member":
            raise KnowledgeNotFound(team_id)
        if verdict == "viewer":
            raise TeamWriteForbidden(team_id)

    # ============ 管理面:列表/删除 ============

    async def list_tables(
        self,
        *,
        tenant_id: str,
        datasource_key: str | None,
        limit: int,
        offset: int,
        caller_user_id: str | None = None,  # 102:可见集合(公共∪所在团队)
        team_scope: str = "all",  # 102:all|public|<team_id> 归属筛选
    ) -> tuple[list[KnowledgeEntry], int]:
        """管理面列表(102):仅呈现公共条目与调用者所在团队条目;team_scope
        在可见集合内二次收窄(public=仅公共/team_id=仅该团队)。"""
        return await self._repo.list_tables(
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            limit=limit,
            offset=offset,
            team_scope=team_scope,
            visible_team_ids=await self._caller_team_ids(caller_user_id),
        )

    async def delete_table(
        self, *, actor_id: str, tenant_id: str, table_id: str
    ) -> KnowledgeEntry | None:
        """硬删单条;不命中(不存在/他租户/团队条目非成员)同形 404(防探测)。"""
        target = await self._repo.find_table(tenant_id=tenant_id, table_id=table_id)
        if target is None:
            return None
        await self._assert_entry_writable(target, actor_id, tenant_id)  # 102:团队条目成员闸
        deleted = await self._repo.delete_table(tenant_id=tenant_id, table_id=table_id)
        if not deleted:
            await self._repo.rollback()
            return None
        await self._repo.log_oper(
            actor_id, "rag.delete", table_id, {"table_name": target.table_name}
        )
        await self._evict_suggestions(target.datasource_key)  # 047 级联失效(同事务)
        await self._repo.commit()
        await self._cache.evict(tenant_id, target.datasource_key)
        return target

    async def set_ownership(
        self,
        *,
        actor_id: str,
        tenant_id: str,
        table_id: str,
        team_id: str | None,
    ) -> KnowledgeEntry | None:
        """归属变更原位转移(102):公共⇄团队,单一实例(内容/来源标记不动)。

        两侧成员关系闸:当前条目(团队条目须可写)与目标团队(指向团队时须
        OWNER/MEMBER);审计+检索缓存失效与既有写路径同口径。"""
        target = await self._repo.find_table(tenant_id=tenant_id, table_id=table_id)
        if target is None:
            return None
        await self._assert_entry_writable(target, actor_id, tenant_id)
        if team_id is not None:
            await self._assert_team_writable(team_id, actor_id, tenant_id)
        if not await self._repo.set_entry_team(
            tenant_id=tenant_id, table_id=table_id, team_id=team_id
        ):
            await self._repo.rollback()
            return None
        await self._repo.log_oper(
            actor_id,
            "rag.set_ownership",
            table_id,
            {
                "table_name": target.table_name,
                "from_team_id": target.team_id,
                "to_team_id": team_id,
            },
        )
        await self._evict_suggestions(target.datasource_key)
        await self._repo.commit()
        await self._cache.evict(tenant_id, target.datasource_key)
        return await self._repo.find_table(tenant_id=tenant_id, table_id=table_id)

    async def edit_table(
        self,
        *,
        actor_id: str,
        tenant_id: str,
        table_id: str,
        table_comment: str,
        alias: str,
    ) -> KnowledgeEntry | None:
        """037:人工精修编辑(table_comment 与 alias 同面,knowledge spec)。

        MANUAL_EDIT 显式覆盖(最高覆盖档,自动来源整行跳过不覆盖);embedding
        以新 comment+alias 重算(输入侧文本与向量/tsv 三者同源),重算失败
        保留既有向量(upsert 人工路径 COALESCE——清向量会使该表退出向量召回)。
        """
        target = await self._repo.find_table(tenant_id=tenant_id, table_id=table_id)
        if target is None:
            return None
        await self._assert_entry_writable(target, actor_id, tenant_id)  # 102:团队条目成员闸
        vec = (
            await self._embed_quietly(
                [
                    build_embedding_text(
                        target.table_name,
                        table_comment,
                        target.columns_comment,
                        alias=alias,
                    )
                ]
            )
        )[0]
        await self._repo.upsert_table(
            tenant_id=tenant_id,
            datasource_key=target.datasource_key,
            schema_name=target.schema_name,
            table_name=target.table_name,
            table_comment=table_comment,
            columns_comment=target.columns_comment,
            sample_queries=target.sample_queries,
            source_type="MANUAL_EDIT",
            embedding=vec,
            alias=alias,
            # 102:人工精修=内容显式覆盖,归属非本面意图——原位保留
            # (归属变更走受控 set_ownership 面,不经编辑夹带)
        )
        await self._repo.log_oper(
            actor_id,
            "rag.edit_table",
            table_id,
            {
                "table_name": target.table_name,
                "alias": alias,
                "table_comment": table_comment[:200],
            },
        )
        await self._evict_suggestions(target.datasource_key)  # 047 级联失效(同事务)
        await self._repo.commit()
        await self._cache.evict(tenant_id, target.datasource_key)
        return await self._repo.find_table(tenant_id=tenant_id, table_id=table_id)

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
        """清空重建(显式语义:清掉人工精修回到自动生成基线,spec 已声明)。

        执行序(014 挂账⑪清零,knowledge spec「先润色后删写」):内省+润色+
        embedding(分钟级 LLM,零 DB 写事务挂起)先行,delete+insert+审计在同一
        短事务收口——消「delete→润色→commit」长事务锁窗口(窗口期并发 upload
        冲突键阻塞);前置阶段异常则不进入删写,既有条目保留。
        reindex 全删重建,无 fetch-metadata 的优先级判定需求,不复用 _ingest。
        """
        await self._require_usable_datasource(tenant_id, datasource_key)
        try:
            tables = await self._repo.introspect_all(
                tenant_id=tenant_id, tenant_key=tenant_key, datasource_key=datasource_key
            )
        except LookupError:
            raise DatasourceInvalid(datasource_key) from None

        # ① 润色(事务外;并发上限,单表失败降级纯结构不阻断整批)
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

        # ② embedding(事务外;失败全 None 无向量落库,全文路仍可用)
        vectors = await self._embed_quietly(
            [
                build_embedding_text(
                    b.table_name, b.table_comment, b.columns_comment, alias=b.alias
                )
                for _, b in described
            ]
        )

        # ③ 短事务删写:delete+insert+审计同事务
        deleted = await self._repo.delete_all_for_datasource(
            tenant_id=tenant_id, datasource_key=datasource_key
        )
        enriched_count = 0
        for t, (enriched, block), vec in zip(tables, described, vectors, strict=True):
            await self._repo.upsert_table(
                tenant_id=tenant_id,
                datasource_key=datasource_key,
                schema_name=t.schema_name,
                table_name=block.table_name,
                table_comment=block.table_comment,
                columns_comment=block.columns_comment,
                sample_queries=block.sample_queries,
                source_type="AI_GENERATED" if enriched else "INTROSPECTION",
                embedding=vec,
                alias=block.alias,  # 037
                # 102:reindex 产物一律公共(design D5)——团队条目被清空重建后
                # 回公共,明示在确认文案(web「重建索引」对话框)
                team_id=None,
                set_team_id=True,
            )
            if enriched:
                enriched_count += 1
        await self._repo.log_oper(
            actor_id,
            "rag.reindex",
            datasource_key,
            {
                "deleted": deleted,
                "rebuilt": len(tables),
                "enriched": enriched_count,
                "datasource_key": datasource_key,
            },
        )
        await self._evict_suggestions(datasource_key)  # 047 级联失效(同事务)
        await self._repo.commit()
        await self._cache.evict(tenant_id, datasource_key)
        return ReindexResult(deleted=deleted, rebuilt=len(tables), enriched=enriched_count)

    async def _ingest(
        self,
        *,
        actor_id: str,
        tenant_id: str,
        datasource_key: str,
        tables: list[IntrospectedTable],
        action: str = "rag.fetch_metadata",
    ) -> FetchResult:
        """润色→优先级 upsert→对账→审计→commit→缓存失效(042:登记=现实)。"""
        if not tables:
            # 空扫描集=该范围已无表:同样对账清零自动登记(keep=∅);
            # 内省连接失败走异常路径,不会进到这里误删
            removed, manual_orphans = await self._repo.delete_stale_entries(
                tenant_id=tenant_id, datasource_key=datasource_key, keep=[]
            )
            if manual_orphans:
                log.warning(
                    "对账保留扫描集外人工条目 %d 行(交人工裁决): tenant=%s ds=%s",
                    manual_orphans,
                    tenant_id,
                    datasource_key,
                )
            await self._repo.log_oper(
                actor_id,
                action,
                datasource_key,
                {
                    "found": 0,
                    "enriched": 0,
                    "skipped": 0,
                    "removed": removed,
                    "manual_orphans": manual_orphans,
                    "datasource_key": datasource_key,
                },
            )
            await self._evict_suggestions(datasource_key)  # 047 级联失效(同事务)
            await self._repo.commit()
            await self._cache.evict(tenant_id, datasource_key)
            return FetchResult(found=0, enriched=0, skipped=0, removed=removed)

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

        # 覆盖优先级:自动来源不覆盖 MANUAL_*(批量预读一次);037 同读既有
        # alias——生效别名(既有非空则沿用)参与 embedding 文本,与 alias 列/
        # tsv 三处同源(R1-S1:否则 alias 保留时派生检索文本丢别名,召回静默失效)
        existing = await self._repo.list_source_and_alias(
            tenant_id=tenant_id, datasource_key=datasource_key
        )
        # (schema_name, enriched, block, effective_alias)
        to_write: list[tuple[str, bool, TableBlock, str]] = []
        skipped = 0
        for t, (enriched, block) in zip(tables, described, strict=True):
            incoming = "AI_GENERATED" if enriched else "INTROSPECTION"
            prior = existing.get((t.schema_name, block.table_name))  # 042 二元键
            if prior is not None and not can_automated_overwrite(prior[0], incoming):
                skipped += 1
                continue
            effective_alias = prior[1] if prior is not None and prior[1] else block.alias
            to_write.append((t.schema_name, enriched, block, effective_alias))

        vectors = await self._embed_quietly(
            [
                build_embedding_text(b.table_name, b.table_comment, b.columns_comment, alias=ea)
                for _, _, b, ea in to_write
            ]
        )
        enriched_count = 0
        for (schema_name, enriched, block, _), vec in zip(to_write, vectors, strict=True):
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
                alias=block.alias,  # 037
                # 102:自动来源一律公共(design D5)——团队归属的自动条目被
                # 扫描更新后同口径回公共;人工条目整行 skip 保护不受影响
                team_id=None,
                set_team_id=True,
            )
            if enriched:
                enriched_count += 1

        # 042 对账(与 upsert 同事务):删扫描集外的自动来源登记,人工/反馈行
        # 保留告警;keep=全量扫描集(含被优先级 skip 的表——其现实存在,登记不删)
        keep = [(t.schema_name, b.table_name) for t, (_, b) in zip(tables, described, strict=True)]
        removed, manual_orphans = await self._repo.delete_stale_entries(
            tenant_id=tenant_id, datasource_key=datasource_key, keep=keep
        )
        if manual_orphans:
            log.warning(
                "对账保留扫描集外人工条目 %d 行(交人工裁决): tenant=%s ds=%s",
                manual_orphans,
                tenant_id,
                datasource_key,
            )
        await self._repo.log_oper(
            actor_id,
            action,
            datasource_key,
            {
                "found": len(tables),
                "enriched": enriched_count,
                "skipped": skipped,
                "removed": removed,
                "manual_orphans": manual_orphans,
                "datasource_key": datasource_key,
            },
        )
        await self._evict_suggestions(datasource_key)  # 047 级联失效(同事务)
        await self._repo.commit()
        await self._cache.evict(tenant_id, datasource_key)
        return FetchResult(
            found=len(tables), enriched=enriched_count, skipped=skipped, removed=removed
        )

    async def allowed_schemas(
        self, *, tenant_id: str, tenant_key: str, datasource_key: str
    ) -> list[str]:
        """可扫描 schema 集合(040 枚举端点;与内省/上传闸同源,集合计算在仓储层)。"""
        return await self._repo.allowed_schemas(
            tenant_id=tenant_id, tenant_key=tenant_key, datasource_key=datasource_key
        )

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

    def _apply_gate(
        self,
        route: str,
        vector_hits: list[RecallHit],
        fulltext_hits: list[RecallHit],
        *,
        knowledge_present: bool = True,
    ) -> tuple[list[RecallHit], list[RecallHit]] | None:
        """064:按路闸门消费(纯函数管道;恒裁剪形态,未注入=None 零行为)。

        route=main|prior(047 继承路同样过闸——闸门判据是路级相关性,
        继承路的命中同样是余弦/ts_rank 原生分,"多轮依赖表"不构成
        relevance 豁免:junk 依赖表一样稀释提示词)。
        不过闸整路裁掉(过闸全留,dropped 记 trace 可观测,不静默降质);
        零命中路不投票(空集即空集)。
        """
        configs = self._gate_configs()
        if configs is None:
            return None
        if not knowledge_present:
            # 064 前置(2026-10-10 对拍裁决):零知识数据源闸门自动旁路——
            # 零命中源于「无知识」而非「低相关」,裁剪会误杀内省回退可答题;
            # trace 如实标注,不静默
            self._last_gate_trace[route] = {"skipped": "no_knowledge"}
            return None
        vec_cfg, ft_cfg = configs
        vec = gate_hits(vector_hits, **vec_cfg)
        ft = gate_hits(fulltext_hits, **ft_cfg)
        self._last_gate_trace[route] = {
            "vector": vec.verdict.as_dict() if vec.verdict else None,
            "vector_dropped": vec.dropped,
            "fulltext": ft.verdict.as_dict() if ft.verdict else None,
            "fulltext_dropped": ft.dropped,
        }
        return vec.passed, ft.passed

    async def _expand_relations(
        self, *, tenant_id: str, datasource_key: str, keys: list[tuple[str, str]]
    ) -> list[RelationExpansion]:
        """095 扩展判定单点(recall 与 recall_probe 共用,试跑同源)。

        配置/端口一票短路先于任何查询(关闭形态零 SQL);查询失败降级为
        无扩展(与既有语义段降级同口径),SHALL NOT 阻断召回主链路。
        扩展判定本身是纯函数 expand_by_relations(一跳/去重/上限/尾附加序)。"""
        if not keys or not self._expand_enabled or self._relation_expander is None:
            return []
        try:
            rows = await self._relation_expander.related_tables(
                tenant_id=tenant_id, datasource_key=datasource_key, tables=keys
            )
        except Exception:  # noqa: BLE001  # 降级为无扩展,主链路不阻断
            log.warning("关系扩展查询失败(降级无扩展)", exc_info=True)
            return []
        return expand_by_relations(keys, rows, max_expand=self._expand_max)

    async def recall(
        self,
        *,
        tenant_key: str,
        datasource_key: str,
        question: str,
        history: list[str] | None = None,
        caller_user_id: str | None = None,  # 102:调用者身份(可见集合维度)
    ) -> RagContext:
        """三路召回:ctx 缓存→embedding→向量/全文/历史→RRF 融合。

        tenant+datasource 双过滤;embedding 失败降级纯全文路;不连业务库。
        047 多轮继承:history 非空时以末条历史问句独立召回一路(上轮依赖表
        是本轮查询的确定性上下文,不依赖向量相似度),去重合并——主路序在
        前、总量封顶 2×RECALL_TOP_K;ctx 缓存键并入历史指纹(同问句不同
        上下文不互串);history 空(单轮)时与单路召回逐字节同形。
        102:条目过滤叠加可见集合(公共 ∪ 调用者团队,纯函数 entry_visible
        的 SQL 同判投影);缓存键并入团队集合指纹(不同可见集合不互命中,
        团队关系变化→指纹变→新集合生效)。caller_user_id 缺省(内部回调未
        携带身份)=纯公共形态,与既有行为一致。
        """
        tenant_id = await self._repo.tenant_id_by_key(tenant_key)
        if tenant_id is None:
            raise LookupError(tenant_key)  # router 映射 404(与 meta 通道同语义)
        await self._require_usable_datasource(tenant_id, datasource_key)
        visible_teams = await self._caller_team_ids(caller_user_id)  # 102

        prior = (history or [])[-1].strip() if history else ""
        # 键材料作为 question 参数传入——派生(hash)逻辑留在 cache 内部
        # 095:键并入扩展形态(开/关各自缓存空间)。否则开形态写入的缓存会在
        # 配置切到关停后的 TTL 窗口内命中,返回含扩展的旧输出——违背「关停
        # 输出与开启前逐字一致」;关→开同理,新能力在 TTL 窗口内静默不生效
        # (与关系写后不失效同类的静默漂移,评审 R1 修复)
        expand_state = (
            "on" if (self._expand_enabled and self._relation_expander is not None) else "off"
        )
        key_material = (
            f"{question}\x1f{prior}\x1f{expand_state}" if prior else f"{question}\x1f{expand_state}"
        )

        cached = await self._cache.get(
            tenant_id, datasource_key, key_material, team_ids=visible_teams
        )
        if cached is not None:
            return cached

        # 064 前置(单点,主/继承两路过闸共用):零知识数据源闸门旁路——
        # 「没灌知识」与「召回不相关」对闸门同为零命中,语义不可区分
        knowledge_present = await self._repo.has_knowledge(
            tenant_id=tenant_id, datasource_key=datasource_key
        )

        vector: list[float] | None = None
        try:
            vector = (await self._embedder.embed([question]))[0]
        except EmbeddingError:
            log.warning("召回 embedding 失败,降级纯全文路")

        vector_hits = []
        fewshot_sqls: list[str] = []
        if vector is not None:
            vector_hits = await self._repo.recall_vector(
                tenant_id=tenant_id,
                datasource_key=datasource_key,
                vector=vector,
                visible_team_ids=visible_teams,  # 102
            )
            # 070:同问句候选去重——优先 ADOPTED 反馈纠错样本(候选已仅含
            # 启用态,FEEDBACK 即 ADOPTED),无反馈样本回退执行成功样本;
            # 保持候选相似度序(去见重后取前 K)
            candidates = await self._repo.recall_history(
                tenant_id=tenant_id,
                datasource_key=datasource_key,
                vector=vector,
                visible_team_ids=visible_teams,  # 102 伴生:历史路同可见集合
            )
            by_question: dict[str, HistorySample] = {}
            for cand in candidates:
                kept = by_question.get(cand.question)
                if kept is None or (
                    cand.source_type == "FEEDBACK" and kept.source_type != "FEEDBACK"
                ):
                    by_question[cand.question] = cand
            fewshot_sqls = [c.sql_text for c in by_question.values()][:RECALL_TOP_K]
        fulltext_hits = await self._repo.recall_fulltext(
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            question=question,
            visible_team_ids=visible_teams,  # 102
        )
        # 064:相关度闸门——各按本路原生分独立判定(vector=余弦相似度/
        # fulltext=ts_rank,量纲不共用);恒裁剪:不过闸整路裁掉后重融合
        gate_notes = self._apply_gate(
            "main", vector_hits, fulltext_hits, knowledge_present=knowledge_present
        )
        if gate_notes is not None:
            vector_hits, fulltext_hits = gate_notes
        fused = rrf_fuse([vector_hits, fulltext_hits])
        if prior and prior != question:
            prior_vector: list[float] | None = None
            try:
                prior_vector = (await self._embedder.embed([prior]))[0]
            except EmbeddingError:
                log.warning("继承路 embedding 失败,降级纯全文路")
            prior_vec_hits = (
                await self._repo.recall_vector(
                    tenant_id=tenant_id,
                    datasource_key=datasource_key,
                    vector=prior_vector,
                    visible_team_ids=visible_teams,  # 102:继承路同可见集合
                )
                if prior_vector is not None
                else []
            )
            prior_ft_hits = await self._repo.recall_fulltext(
                tenant_id=tenant_id,
                datasource_key=datasource_key,
                question=prior,
                visible_team_ids=visible_teams,  # 102:继承路同可见集合
            )
            # 064:继承路同样过闸(与主路同一判据同一阈值口径——"多轮依赖
            # 表"不是 relevance 豁免;junk 依赖表一样稀释提示词)
            prior_gate = self._apply_gate(
                "prior", prior_vec_hits, prior_ft_hits, knowledge_present=knowledge_present
            )
            if prior_gate is not None:
                prior_vec_hits, prior_ft_hits = prior_gate
            seen = {(h.schema_name, h.table_name) for h in fused}
            fused = fused + [
                h
                for h in rrf_fuse([prior_vec_hits, prior_ft_hits])
                if (h.schema_name, h.table_name) not in seen
            ]
            fused = fused[:_RECALL_TOTAL_CAP]
        if self._last_gate_trace:
            # 064 闸门观测:各路 verdict(生效阈值/判定归因/命中原生分)与
            # dropped 按路(main/prior)下发——曾只打 {"gate":"applied"}
            # 把观测数据算了就丢,违背「SHALL NOT 静默降质」
            log.info("召回闸门 trace 观测 notes=%s", self._last_gate_trace)
        # 095:关系感知一跳扩展(主召回确定后、fetch_entries 前;尾附加使
        # 扩展天然落在最低优先级,080 预算超限时先裁扩展、主召回不动)
        recalled_keys = [(h.schema_name, h.table_name) for h in fused]
        expansions = await self._expand_relations(
            tenant_id=tenant_id, datasource_key=datasource_key, keys=recalled_keys
        )
        if expansions:
            # SHALL NOT 静默:本轮扩展表与经由关系进 trace
            log.info(
                "关系扩展 trace: %s",
                [
                    {
                        "table": f"{e.key[0]}.{e.key[1]}",
                        "via": f"{e.via.get('source_table')}->{e.via.get('target_table')}",
                    }
                    for e in expansions
                ],
            )
            recalled_keys = recalled_keys + [e.key for e in expansions]
        entries = await self._repo.fetch_entries(
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            keys=recalled_keys,
            visible_team_ids=visible_teams,  # 102:扩展路兜底同可见集合
        )
        ctx = RagContext(
            schema_hint=build_schema_hint(entries),
            history_sql=build_history_sql(fewshot_sqls),
        )
        # 空结果不写缓存:降级形态(embedding 失败/知识为空)缓存会让恢复后的
        # 服务在 TTL 窗口内持续返回空提示——空结果无缓存价值,下次请求重试全链路
        if ctx.schema_hint or ctx.history_sql:
            await self._cache.set(
                tenant_id, datasource_key, key_material, ctx, team_ids=visible_teams
            )
        return ctx

    async def recall_probe(
        self,
        *,
        tenant_key: str,
        datasource_key: str,
        question: str,
        caller_user_id: str | None = None,  # 102:试跑面同可见集合(公共∪所在团队)
    ) -> dict[str, object]:
        """064 任务 2.6:召回试跑投影(只读验证面,零副作用)。

        与主链路 retrieve 相同的三路召回 + 闸门判定纯函数(gate_hits)——
        返回三路命中及各自原生分、融合分、双条件判定结论。SHALL NOT 修改
        配置/落库/触发召回缓存失效(不走 _cache;不写任何表)。
        102:召回在调用者可见集合内(与主链路同口径)——「上传后验证可召回性」
        对团队归属条目同样成立,probe 面不成为可见集合旁路或反指。
        """
        tenant_id = await self._repo.tenant_id_by_key(tenant_key)
        if tenant_id is None:
            raise LookupError(tenant_key)
        await self._require_usable_datasource(tenant_id, datasource_key)
        visible_teams = await self._caller_team_ids(caller_user_id)
        # 064 零知识旁路镜像(与主链路同判据):没灌知识时主链路整路跳闸,
        # 试跑观测面必须如实反映(否则 verdict 报 mean_below 与主链路「旁路」
        # 行为相悖,标定与排障被误导)
        knowledge_present = await self._repo.has_knowledge(
            tenant_id=tenant_id, datasource_key=datasource_key
        )
        vector: list[float] | None = None
        try:
            vector = (await self._embedder.embed([question]))[0]
        except EmbeddingError:
            log.warning("试跑 embedding 失败(降级纯全文路)")
        vector_hits = (
            await self._repo.recall_vector(
                tenant_id=tenant_id,
                datasource_key=datasource_key,
                vector=vector,
                visible_team_ids=visible_teams,
            )
            if vector is not None
            else []
        )
        fulltext_hits = await self._repo.recall_fulltext(
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            question=question,
            visible_team_ids=visible_teams,
        )
        fused = rrf_fuse([vector_hits, fulltext_hits])
        # 闸门判定(与主链路同一配置单点+同一纯函数,恒裁剪;零知识=闸门
        # 未参与,verdict 置空——试跑观测面如实反映,不报裁剪结论误导排障)
        configs = self._gate_configs()
        if configs is not None and knowledge_present:
            vec_cfg, ft_cfg = configs
            vec_gate = gate_hits(vector_hits, **vec_cfg)
            ft_gate = gate_hits(fulltext_hits, **ft_cfg)
        else:
            # 未注入(测试轻量装配)或零知识旁路:不判不裁,verdict=None 透出
            vec_gate = GateResult(passed=vector_hits)
            ft_gate = GateResult(passed=fulltext_hits)
        # 095:试跑同源透出扩展明细(与主链路共用 _expand_relations 单点;
        # 只读验证面零副作用纪律不变——不写缓存/配置/审计)
        fused_keys = [(h.schema_name, h.table_name) for h in fused]
        expansions = await self._expand_relations(
            tenant_id=tenant_id, datasource_key=datasource_key, keys=fused_keys
        )
        return {
            "question": question,
            "gate_skipped": None if knowledge_present else "no_knowledge",
            "expansion": {
                "enabled": bool(self._expand_enabled and self._relation_expander is not None),
                "tables": [
                    {
                        "schema_name": e.key[0],
                        "table_name": e.key[1],
                        "via": {
                            "source_table": e.via.get("source_table"),
                            "target_table": e.via.get("target_table"),
                            "join_keys": e.via.get("join_keys"),
                            "source": e.via.get("source"),
                        },
                    }
                    for e in expansions
                ],
                "fused_after": [
                    {"schema_name": key[0], "table_name": key[1]}
                    for key in fused_keys + [e.key for e in expansions]
                ],
            },
            "vector": {
                "hits": [
                    {
                        "schema_name": h.schema_name,
                        "table_name": h.table_name,
                        "score": round(h.score, 6),
                        "source": h.source,
                    }
                    for h in vector_hits
                ],
                "verdict": vec_gate.verdict.as_dict() if vec_gate.verdict else None,
                "dropped": vec_gate.dropped,
            },
            "fulltext": {
                "hits": [
                    {
                        "schema_name": h.schema_name,
                        "table_name": h.table_name,
                        "score": round(h.score, 6),
                        "source": h.source,
                    }
                    for h in fulltext_hits
                ],
                "verdict": ft_gate.verdict.as_dict() if ft_gate.verdict else None,
                "dropped": ft_gate.dropped,
            },
            "fused": [
                {
                    "schema_name": h.schema_name,
                    "table_name": h.table_name,
                    "score": round(h.score, 6),
                }
                for h in fused
            ],
        }

    # ============ 回写(成功 SQL) ============

    async def writeback(
        self,
        *,
        tenant_id: str,
        datasource_key: str,
        question: str,
        sql_text: str,
        caller_user_id: str | None = None,
    ) -> None:
        """成功查询回写 query_history;失败仅告警(调用方独立 try,不阻断主流程)。

        016 治理:同 (tenant, ds, question) 幂等覆盖保留最新成功 SQL(不增行)。
        embedding 复用 emb 缓存(相同问句零成本);embedding 失败仍回写——
        首次落库为无向量行(不参与向量召回);同题重写时保留该行旧向量
        (问句未变旧向量语义仍有效,repository COALESCE)。
        102 伴生:caller_user_id 决定样本来源可见集合(公共∪其团队)——
        团队条目引出的 SQL 不经其成员授权进入非成员 few-shot(历史路旁路
        治理);缺省(None)=公共样本,既有调用零回归。
        """
        if not sql_text.strip():
            return
        vec: list[float] | None = None
        try:
            vec = (await self._embedder.embed([question]))[0]
        except EmbeddingError:
            log.warning("回写 embedding 失败(新行无向量;同题重写保留旧向量)")
        try:
            await self._repo.insert_history(
                tenant_id=tenant_id,
                datasource_key=datasource_key,
                question=question,
                sql_text=sql_text,
                embedding=vec,
                visible_teams=await self._caller_team_ids(caller_user_id),
            )
            await self._repo.commit()
        except Exception:  # noqa: BLE001  # 回写永不阻断主流程
            log.warning("query_history 回写失败(不阻断主流程)", exc_info=True)
            await self._repo.rollback()

    async def evict_ctx(self, tenant_id: str, datasource_key: str) -> None:
        """070:样本准入变更后 ctx 缓存按租户+数据源精确失效(复用语义:
        few-shot 样本进出召回会改变同问句的召回产物,缓存须同步)。"""
        await self._cache.evict(tenant_id, datasource_key)

    # ============ 列注释读取(041 呈现层列标签) ============

    async def column_comments(
        self, *, tenant_id: str, datasource_key: str
    ) -> dict[str, dict[str, str]]:
        """ColumnCommentReader 端口实现:表名 → 列注释原文(整集一次读,
        单数据源注释集量级小;剥离与映射在 chat 呈现纯函数内)。"""
        return await self._repo.column_comments(tenant_id=tenant_id, datasource_key=datasource_key)

    # ============ 知识覆盖报告(052;确定性纯聚合) ============

    async def coverage_report(
        self,
        *,
        tenant_id: str,
        tenant_key: str,
        datasource_key: str,
        caller_user_id: str | None = None,  # 102:报告读面同可见集合
    ) -> CoverageReport:
        """四类空白盘点(零覆盖表/纯结构登记/缺标签列/失败问题簇)+ few-shot
        概况。确定性纯聚合契约:零 LLM(不触 embedder/describer)、零写入、
        无缓存;内省失败整体失败不产出半份报告(宁缺毋错,042 对账同口径
        ——半份报告会被误读为「登记完备」)。102:登记条目面取调用者可见
        集合(与列表端点同口径,管理读面不成为团队条目存在性旁路)。"""
        await self._require_usable_datasource(tenant_id, datasource_key)
        try:
            tables = await self._repo.introspect_all(
                tenant_id=tenant_id, tenant_key=tenant_key, datasource_key=datasource_key
            )
        except LookupError:
            raise DatasourceInvalid(datasource_key) from None
        entries = await self._repo.list_all_entries(
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            visible_team_ids=await self._caller_team_ids(caller_user_id),
        )
        signals = await self._repo.failure_signals(
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            since=datetime.now(UTC) - timedelta(days=COVERAGE_FAILURE_WINDOW_DAYS),
        )
        overview = await self._repo.history_overview(
            tenant_id=tenant_id, datasource_key=datasource_key
        )
        return build_coverage_report(
            datasource_key=datasource_key,
            tables=tables,
            entries=entries,
            signals=signals,
            history=overview,
        )
