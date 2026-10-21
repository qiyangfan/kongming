"""知识库用例编排，负责上传、召回、扫描、重建和历史记录回写。召回只查询控制面知识库并同时限定租户和数据源；业务数据库内省经仓储端口执行。

知识写入提交后失效对应缓存。向量化失败时退回全文检索，润色失败时保留结构信息；自动来源不会覆盖人工维护的条目。"""

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
    removed: int = 0  # 对账删除的扫描集外自动来源行数


@dataclass(frozen=True)
class ReindexResult:
    deleted: int
    rebuilt: int
    enriched: int


class TenantForbidden(Exception):
    """未绑定目标租户(或租户不存在/禁用,同形不区分)。"""


class RagService:
    def __init__(
        self,
        repo: RagRepo,
        *,
        embedder: EmbeddingPort,
        describer: TableDescriberPort,
        cache: ContextCache,
        suggestion_evictor: SuggestionCacheEvictor | None = None,
        gate_settings: Any = None,  # 闸门配置(Settings;None=测试轻量装配,组合根恒注入)
        relation_expander: RelationExpansionPort | None = None,  # 组合根注入
        expand_enabled: bool = False,  # 构造缺省关(未注入端口的调用方零行为)
        expand_max: int = 2,
        member_teams: MemberTeamsReader | None = None,  # 可见集合(未注入=纯公共)
        member_roles: MemberRoleReader | None = None,  # 写权限三态(未注入=非成员)
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
        # 按路闸门观测(main=主路 / prior=继承路);recall 消费后
        # 原样下发日志,不静默
        self._last_gate_trace: dict[str, object] = {}

    def _gate_configs(self) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """取向量与全文闸门参数,两路阈值独立,共用 min_count/mean_window;
        未配置时返回 None。"""
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
        """删除该数据源的建议缓存；调用方将其与知识写入放在同一事务提交，未配置失效器时跳过。"""
        if self._suggestion_evictor is None:
            return
        await self._suggestion_evictor.delete_by_datasource(datasource_key)

    # ============ 租户上下文(显式查白名单,缺省默认租户) ============

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

    # ============ 团队归属:可见集合与写权限单点 ============

    async def _caller_team_ids(self, caller_user_id: str | None) -> list[str]:
        """调用者团队集合(可见集合与缓存指纹共用单点);无身份/未注入端口
        为空集合,即纯公共形态。"""
        if not caller_user_id or self._member_teams is None:
            return []
        return sorted(await self._member_teams.team_ids_of_user(caller_user_id))

    async def _assert_entry_writable(
        self, target: KnowledgeEntry, actor_id: str, tenant_id: str
    ) -> None:
        """团队条目写闸:非成员 404 同形(KnowledgeNotFound,不暴露
        存在性);VIEWER 403(TeamWriteForbidden);公共条目不设额外闸。
        未注入角色端口按非成员处理(fail-closed);角色查询限定当前租户,
        跨租户团队身份不参与判定。"""
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

    # ============ 文档上传 ============

    async def upload_blocks(
        self,
        *,
        actor_id: str,
        tenant_id: str,
        tenant_key: str,
        datasource_key: str,
        schema_name: str,
        blocks: list[TableBlock],
        team_id: str | None = None,  # 上传归属(空=公共)
    ) -> list[str]:
        """人工上传显式内容可覆盖自动来源,但不会覆盖已有非空 alias。团队条目
        写入要求用户具备团队成员权限;未指定 team_id 时,新条目为公共,
        现有条目保留归属。"""
        await self._require_usable_datasource(tenant_id, datasource_key)
        if team_id is not None:
            await self._assert_team_writable(team_id, actor_id, tenant_id)
        # 写边界闸:语法合法 ≠ 集合成员,越界 schema 的条目永不命中召回,
        # 错误会后置到提问期——存在性校验先于 embedding 等贵活
        allowed = await self._repo.allowed_schemas(
            tenant_id=tenant_id, tenant_key=tenant_key, datasource_key=datasource_key
        )
        if schema_name not in allowed:
            raise SchemaNotAllowed(schema_name)
        # embedding 文本须以「生效别名」参与(与 alias 列/tsv 同源
        # ——upsert 保留既有非空 alias 时,无别名文本的新向量会使别名向量召回
        # 静默退化);二元键只读本 schema 同名表
        existing = await self._repo.list_source_and_alias(
            tenant_id=tenant_id, datasource_key=datasource_key
        )
        # 先校验团队条目的写权限，再执行 embedding 等耗时操作。
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
                alias=block.alias,
                team_id=team_id,
                set_team_id=team_id is not None,  # 显式归属才写,缺省保留既有
            )
        await self._repo.log_oper(
            actor_id,
            "rag.upload",
            datasource_key,
            {
                "schema_name": schema_name,
                "tables": [b.table_name for b in blocks],
                "datasource_key": datasource_key,
                "team_id": team_id,
            },
        )
        await self._evict_suggestions(datasource_key)  # 级联失效(同事务)
        await self._repo.commit()
        await self._cache.evict(tenant_id, datasource_key)
        return [b.table_name for b in blocks]

    async def _assert_team_writable(self, team_id: str, actor_id: str, tenant_id: str) -> None:
        """目标团队写闸(上传归属/归属变更指向团队时):操作者须该团队
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
        caller_user_id: str | None = None,  # 可见集合(公共∪所在团队)
        team_scope: str = "all",  # all|public|<team_id> 归属筛选
    ) -> tuple[list[KnowledgeEntry], int]:
        """管理面列表:仅呈现公共条目与调用者所在团队条目;team_scope
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
        await self._assert_entry_writable(target, actor_id, tenant_id)  # 团队条目成员闸
        deleted = await self._repo.delete_table(tenant_id=tenant_id, table_id=table_id)
        if not deleted:
            await self._repo.rollback()
            return None
        await self._repo.log_oper(
            actor_id, "rag.delete", table_id, {"table_name": target.table_name}
        )
        await self._evict_suggestions(target.datasource_key)  # 级联失效(同事务)
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
        """归属变更原位转移:公共⇄团队,单一实例(内容/来源标记不动)。

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
        """人工精修编辑(table_comment 与 alias 同面,knowledge spec)。

        MANUAL_EDIT 显式覆盖(最高覆盖档,自动来源整行跳过不覆盖);embedding
        以新 comment+alias 重算(输入侧文本与向量/tsv 三者同源),重算失败
        保留既有向量(upsert 人工路径 COALESCE——清向量会使该表退出向量召回)。
        """
        target = await self._repo.find_table(tenant_id=tenant_id, table_id=table_id)
        if target is None:
            return None
        await self._assert_entry_writable(target, actor_id, tenant_id)  # 团队条目成员闸
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
            # 人工精修=内容显式覆盖,归属非本面意图——原位保留
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
        await self._evict_suggestions(target.datasource_key)  # 级联失效(同事务)
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
        """先完成数据源内省、润色和向量化，再在短事务中删除并重建条目；前置处理失败时保留现有条目。"""
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
                alias=block.alias,
                # reindex 产物一律公共——团队条目被清空重建后
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
        await self._evict_suggestions(datasource_key)  # 级联失效(同事务)
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
        """润色→按优先级 upsert→对账→审计→commit→缓存失效,使登记与
        实际 schema 对齐。"""
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
            await self._evict_suggestions(datasource_key)  # 级联失效(同事务)
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

        # 覆盖优先级:自动来源不覆盖 MANUAL_*(批量预读一次);同读既有
        # alias——生效别名(既有非空则沿用)参与 embedding 文本,与 alias 列/
        # tsv 三处同源(否则 alias 保留时派生检索文本丢别名,召回静默失效)
        existing = await self._repo.list_source_and_alias(
            tenant_id=tenant_id, datasource_key=datasource_key
        )
        # (schema_name, enriched, block, effective_alias)
        to_write: list[tuple[str, bool, TableBlock, str]] = []
        skipped = 0
        for t, (enriched, block) in zip(tables, described, strict=True):
            incoming = "AI_GENERATED" if enriched else "INTROSPECTION"
            prior = existing.get((t.schema_name, block.table_name))  # 二元键
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
                alias=block.alias,
                # 自动来源一律公共——团队归属的自动条目被
                # 扫描更新后同口径回公共;人工条目整行 skip 保护不受影响
                team_id=None,
                set_team_id=True,
            )
            if enriched:
                enriched_count += 1

        # 对账(与 upsert 同事务):删扫描集外的自动来源登记,人工/反馈行
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
        await self._evict_suggestions(datasource_key)  # 级联失效(同事务)
        await self._repo.commit()
        await self._cache.evict(tenant_id, datasource_key)
        return FetchResult(
            found=len(tables), enriched=enriched_count, skipped=skipped, removed=removed
        )

    async def allowed_schemas(
        self, *, tenant_id: str, tenant_key: str, datasource_key: str
    ) -> list[str]:
        """可扫描 schema 集合(枚举端点;与内省/上传闸同源,集合计算在仓储层)。"""
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
        """按路闸门消费(纯函数管道;恒裁剪形态,未注入=None 零行为)。

        route=main|prior(继承路同样过闸——闸门判据是路级相关性,
        继承路的命中同样是余弦/ts_rank 原生分,"多轮依赖表"不构成
        relevance 豁免:junk 依赖表一样稀释提示词)。
        不过闸整路裁掉(过闸全留,dropped 记 trace 可观测,不静默降质);
        零命中路不投票(空集即空集)。
        """
        configs = self._gate_configs()
        if configs is None:
            return None
        if not knowledge_present:
            # 零知识数据源闸门自动旁路:零命中源于「无知识」而非「低相关」,
            # 裁剪会误杀内省回退可答题;trace 如实标注,不静默
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
        """按召回结果补充关联表；未启用、未配置或查询失败时跳过扩展。"""
        if not keys or not self._expand_enabled or self._relation_expander is None:
            return []
        try:
            rows = await self._relation_expander.related_tables(
                tenant_id=tenant_id, datasource_key=datasource_key, tables=keys
            )
        except Exception:  # noqa: BLE001  # 关系查询失败时跳过扩展，不影响召回。
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
        caller_user_id: str | None = None,  # 调用者身份(可见集合维度)
    ) -> RagContext:
        """执行向量、全文和历史 SQL 三路召回,结果按 RRF 合并。查询同时过滤
        租户、数据源和团队可见范围;embedding 失败时使用全文结果,且不连接
        业务库。"""
        tenant_id = await self._repo.tenant_id_by_key(tenant_key)
        if tenant_id is None:
            raise LookupError(tenant_key)  # router 映射 404
        await self._require_usable_datasource(tenant_id, datasource_key)
        visible_teams = await self._caller_team_ids(caller_user_id)

        prior = (history or [])[-1].strip() if history else ""
        # 键材料作为 question 参数传入——派生(hash)逻辑留在 cache 内部。
        # 键并入扩展形态(开/关各自缓存空间):否则开形态写入的缓存会在配置
        # 切到关停后的 TTL 窗口内命中,返回含扩展的旧输出;关→开同理,
        # 新能力在 TTL 窗口内静默不生效
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

        # 前置(单点,主/继承两路过闸共用):零知识数据源闸门旁路——
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
                visible_team_ids=visible_teams,
            )
            # 同问句候选去重——优先 ADOPTED 反馈纠错样本(候选已仅含
            # 启用态,FEEDBACK 即 ADOPTED),无反馈样本回退执行成功样本;
            # 保持候选相似度序,去重后取前 K
            candidates = await self._repo.recall_history(
                tenant_id=tenant_id,
                datasource_key=datasource_key,
                vector=vector,
                visible_team_ids=visible_teams,  # 伴生:历史路同可见集合
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
            visible_team_ids=visible_teams,
        )
        # 相关度闸门——各按本路原生分独立判定(vector=余弦相似度/
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
                    visible_team_ids=visible_teams,  # 继承路同可见集合
                )
                if prior_vector is not None
                else []
            )
            prior_ft_hits = await self._repo.recall_fulltext(
                tenant_id=tenant_id,
                datasource_key=datasource_key,
                question=prior,
                visible_team_ids=visible_teams,  # 继承路同可见集合
            )
            # 继承路同样过闸(与主路同一判据同一阈值口径——"多轮依赖
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
            # 闸门观测:各路 verdict(生效阈值/判定归因/命中原生分)与
            # dropped 按路(main/prior)全量下发日志,不静默丢弃
            log.info("召回闸门 trace 观测 notes=%s", self._last_gate_trace)
        # 关系感知一跳扩展(主召回确定后、fetch_entries 前;尾附加使
        # 扩展天然落在最低优先级,预算超限时先裁扩展、主召回不动)
        recalled_keys = [(h.schema_name, h.table_name) for h in fused]
        expansions = await self._expand_relations(
            tenant_id=tenant_id, datasource_key=datasource_key, keys=recalled_keys
        )
        if expansions:
            # 扩展表与经由关系写入 trace,不静默
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
            visible_team_ids=visible_teams,  # 扩展路兜底同可见集合
        )
        ctx = RagContext(
            schema_hint=build_schema_hint(entries),
            history_sql=build_history_sql(fewshot_sqls),
        )
        # 空结果不写缓存，避免 embedding 恢复后仍在 TTL 内持续返回空提示。
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
        caller_user_id: str | None = None,  # 试跑面同可见集合(公共∪所在团队)
    ) -> dict[str, object]:
        """只读执行召回试跑，返回命中、融合、闸门和关系扩展结果；范围遵循调用者可见权限。"""
        tenant_id = await self._repo.tenant_id_by_key(tenant_key)
        if tenant_id is None:
            raise LookupError(tenant_key)
        await self._require_usable_datasource(tenant_id, datasource_key)
        visible_teams = await self._caller_team_ids(caller_user_id)
        # 没有知识条目时跳过闸门，避免把旁路状态误报为相关度不足。
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
        # 未配置闸门或知识为空时不裁剪，verdict 留空表示未判定。
        configs = self._gate_configs()
        if configs is not None and knowledge_present:
            vec_cfg, ft_cfg = configs
            vec_gate = gate_hits(vector_hits, **vec_cfg)
            ft_gate = gate_hits(fulltext_hits, **ft_cfg)
        else:
            # 未注入(测试轻量装配)或零知识旁路:不判不裁,verdict=None 透出
            vec_gate = GateResult(passed=vector_hits)
            ft_gate = GateResult(passed=fulltext_hits)
        # 试跑复用关系扩展逻辑，但不写缓存、配置或审计。
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
        """成功问答按租户、数据源和问句幂等回写最新 SQL;向量化失败时仍保存
        无向量样本,团队样本只对可见团队成员开放。"""
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
        """样本准入变更后 ctx 缓存按租户+数据源精确失效(复用语义:
        few-shot 样本进出召回会改变同问句的召回产物,缓存须同步)。"""
        await self._cache.evict(tenant_id, datasource_key)

    # ============ 列注释读取(呈现层列标签) ============

    async def column_comments(
        self, *, tenant_id: str, datasource_key: str
    ) -> dict[str, dict[str, str]]:
        """ColumnCommentReader 端口实现:表名 → 列注释原文(整集一次读,
        单数据源注释集量级小;剥离与映射在 chat 呈现纯函数内)。"""
        return await self._repo.column_comments(tenant_id=tenant_id, datasource_key=datasource_key)

    # ============ 知识覆盖报告(确定性纯聚合) ============

    async def coverage_report(
        self,
        *,
        tenant_id: str,
        tenant_key: str,
        datasource_key: str,
        caller_user_id: str | None = None,  # 报告读面同可见集合
    ) -> CoverageReport:
        """四类空白盘点(零覆盖表/纯结构登记/缺标签列/失败问题簇)+ few-shot
        概况。确定性纯聚合契约:零 LLM(不触 embedder/describer)、零写入、
        无缓存;内省失败整体失败不产出半份报告(宁缺毋错,对账同口径
        ——半份报告会被误读为「登记完备」)。登记条目面取调用者可见
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
