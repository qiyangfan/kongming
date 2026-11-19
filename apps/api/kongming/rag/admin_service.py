"""知识库管理用例:上传/精修/归属/删除/扫描登记/重建/覆盖报告。

与召回管线(rag.service.RagService)零方法级互调;共享仅
caller_team_ids/require_usable_datasource 两个模块函数与
repo/embedder/cache/member_teams 端口(单向消费 rag.service)。
知识写入提交后失效对应缓存;向量化失败退回全文检索,润色失败保留结构信息;
自动来源不覆盖人工维护条目。
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from kongming.rag.cache import ContextCache
from kongming.rag.models import (
    COVERAGE_FAILURE_WINDOW_DAYS,
    CoverageReport,
    DatasourceInvalid,
    IntrospectedTable,
    KnowledgeEntry,
    KnowledgeNotFound,
    SchemaNotAllowed,
    TableBlock,
    TeamWriteForbidden,
    build_coverage_report,
    build_embedding_text,
    can_automated_overwrite,
    entry_write_access,
)
from kongming.rag.ports import (
    CoverageRepo,
    EmbeddingError,
    EmbeddingPort,
    MemberRoleReader,
    MemberTeamsReader,
    RagRepo,
    SuggestionCacheEvictor,
    TableDescriberPort,
)
from kongming.rag.service import caller_team_ids, require_usable_datasource, resolve_tenant

log = logging.getLogger(__name__)

_DESCRIBE_CONCURRENCY = 4  # 逐表润色并发上限(防 LLM 端打爆)
_EMBED_BATCH = 64  # embed 单批上限(agent 端点契约)


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


class RagAdminService:
    def __init__(
        self,
        repo: RagRepo,
        *,
        embedder: EmbeddingPort,
        describer: TableDescriberPort,
        cache: ContextCache,
        suggestion_evictor: SuggestionCacheEvictor | None = None,
        member_teams: MemberTeamsReader | None = None,  # 可见集合(未注入=纯公共)
        member_roles: MemberRoleReader | None = None,  # 写权限三态(未注入=非成员)
        coverage: CoverageRepo | None = None,  # 覆盖报告只读仓储(管理根按会话注入)
    ) -> None:
        self._repo = repo
        self._coverage = coverage
        self._embedder = embedder
        self._describer = describer
        self._cache = cache
        self._suggestion_evictor = suggestion_evictor
        self._member_teams = member_teams
        self._member_roles = member_roles

    async def resolve_tenant(self, user_id: str, tenant_key: str | None) -> tuple[str, str]:
        """X-Tenant-Id 解析(与召回服务共用单点)。"""
        return await resolve_tenant(self._repo, user_id, tenant_key)

    async def _evict_suggestions(self, datasource_key: str) -> None:
        """删除该数据源的建议缓存；调用方将其与知识写入放在同一事务提交，未配置失效器时跳过。"""
        if self._suggestion_evictor is None:
            return
        await self._suggestion_evictor.delete_by_datasource(datasource_key)

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
        await require_usable_datasource(self._repo, tenant_id, datasource_key)
        if team_id is not None:
            await self._assert_team_writable(team_id, actor_id, tenant_id)
        # 写边界闸:语法合法 ≠ 集合成员,越界 schema 的条目永不命中召回,
        # 错误会后置到提问期——存在性校验先于 embedding 等贵活
        allowed = await self._repo.tenant.allowed_schemas(
            tenant_id=tenant_id, tenant_key=tenant_key, datasource_key=datasource_key
        )
        if schema_name not in allowed:
            raise SchemaNotAllowed(schema_name)
        # embedding 文本须以「生效别名」参与(与 alias 列/tsv 同源
        # ——upsert 保留既有非空 alias 时,无别名文本的新向量会使别名向量召回
        # 静默退化);二元键只读本 schema 同名表
        existing = await self._repo.entries.list_source_and_alias(
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
            await self._repo.entries.upsert_table(
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
        return await self._repo.entries.list_tables(
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            limit=limit,
            offset=offset,
            team_scope=team_scope,
            visible_team_ids=await caller_team_ids(self._member_teams, caller_user_id),
        )

    async def delete_table(
        self, *, actor_id: str, tenant_id: str, table_id: str
    ) -> KnowledgeEntry | None:
        """硬删单条;不命中(不存在/他租户/团队条目非成员)同形 404(防探测)。"""
        target = await self._repo.entries.find_table(tenant_id=tenant_id, table_id=table_id)
        if target is None:
            return None
        await self._assert_entry_writable(target, actor_id, tenant_id)  # 团队条目成员闸
        deleted = await self._repo.entries.delete_table(tenant_id=tenant_id, table_id=table_id)
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
        target = await self._repo.entries.find_table(tenant_id=tenant_id, table_id=table_id)
        if target is None:
            return None
        await self._assert_entry_writable(target, actor_id, tenant_id)
        if team_id is not None:
            await self._assert_team_writable(team_id, actor_id, tenant_id)
        if not await self._repo.entries.set_entry_team(
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
        return await self._repo.entries.find_table(tenant_id=tenant_id, table_id=table_id)

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
        target = await self._repo.entries.find_table(tenant_id=tenant_id, table_id=table_id)
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
        await self._repo.entries.upsert_table(
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
        return await self._repo.entries.find_table(tenant_id=tenant_id, table_id=table_id)

    # ============ fetch-metadata / reindex(knowledge 域) ============

    def _warn_manual_orphans(
        self, manual_orphans: int, tenant_id: str, datasource_key: str
    ) -> None:
        """对账保留的扫描集外人工条目告警(交人工裁决)。"""
        if manual_orphans:
            log.warning(
                "对账保留扫描集外人工条目 %d 行(交人工裁决): tenant=%s ds=%s",
                manual_orphans,
                tenant_id,
                datasource_key,
            )

    async def _upsert_scan_rows(
        self,
        *,
        tenant_id: str,
        datasource_key: str,
        to_write: list[tuple[str, bool, TableBlock, str]],
        vectors: list[list[float] | None],
    ) -> int:
        """按待写行逐表 upsert(自动来源一律公共),返回其中润色成功的行数。"""
        enriched_count = 0
        for (schema_name, enriched, block, _), vec in zip(to_write, vectors, strict=True):
            await self._repo.entries.upsert_table(
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
        return enriched_count

    async def _pending_scan_writes(
        self,
        tables: list[IntrospectedTable],
        described: list[tuple[bool, TableBlock]],
        *,
        tenant_id: str,
        datasource_key: str,
    ) -> tuple[list[tuple[str, bool, TableBlock, str]], int]:
        """按覆盖优先级筛出待写行与生效别名;返回 ((schema,enriched,块,生效别名)列表, skip 数)。"""
        # 覆盖优先级:自动来源不覆盖 MANUAL_*(批量预读一次);同读既有
        # alias——生效别名(既有非空则沿用)参与 embedding 文本,与 alias 列/
        # tsv 三处同源(否则 alias 保留时派生检索文本丢别名,召回静默失效)
        existing = await self._repo.entries.list_source_and_alias(
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
        return to_write, skipped

    async def _introspect(
        self, *, tenant_id: str, tenant_key: str, datasource_key: str
    ) -> list[IntrospectedTable]:
        """校验数据源可用并内省其表结构;内省失败按数据源非法收口。"""
        await require_usable_datasource(self._repo, tenant_id, datasource_key)
        try:
            return await self._repo.tenant.introspect_all(
                tenant_id=tenant_id, tenant_key=tenant_key, datasource_key=datasource_key
            )
        except LookupError:
            raise DatasourceInvalid(datasource_key) from None

    async def fetch_metadata(
        self, *, actor_id: str, tenant_id: str, tenant_key: str, datasource_key: str
    ) -> FetchResult:
        tables = await self._introspect(
            tenant_id=tenant_id, tenant_key=tenant_key, datasource_key=datasource_key
        )
        return await self._ingest(
            actor_id=actor_id,
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            tables=tables,
        )

    async def _describe_tables(
        self, tables: list[IntrospectedTable]
    ) -> list[tuple[bool, TableBlock]]:
        """逐表润色(并发上限;单表失败降级纯结构不阻断整批)。"""
        sem = asyncio.Semaphore(_DESCRIBE_CONCURRENCY)

        async def describe_one(t: IntrospectedTable) -> tuple[bool, TableBlock]:
            block = TableBlock(
                table_name=t.table_name,
                table_comment=t.table_comment,
                columns_comment=dict(t.columns_comment),
            )
            async with sem:
                return await self._describer.describe(block, t.schema_name)

        return await asyncio.gather(*(describe_one(t) for t in tables))

    async def reindex(
        self, *, actor_id: str, tenant_id: str, tenant_key: str, datasource_key: str
    ) -> ReindexResult:
        """先完成数据源内省、润色和向量化，再在短事务中删除并重建条目；前置处理失败时保留现有条目。"""
        tables = await self._introspect(
            tenant_id=tenant_id, tenant_key=tenant_key, datasource_key=datasource_key
        )

        # ① 润色(事务外;并发上限,单表失败降级纯结构不阻断整批)
        described = await self._describe_tables(tables)

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
        deleted = await self._repo.entries.delete_all_for_datasource(
            tenant_id=tenant_id, datasource_key=datasource_key
        )
        enriched_count = await self._upsert_scan_rows(
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            # 第④位生效别名产物不用(alias 列取块自身),空串占位
            to_write=[
                (t.schema_name, enriched, block, "")
                for t, (enriched, block) in zip(tables, described, strict=True)
            ],
            vectors=vectors,
        )
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
            removed, manual_orphans = await self._repo.entries.delete_stale_entries(
                tenant_id=tenant_id, datasource_key=datasource_key, keep=[]
            )
            self._warn_manual_orphans(manual_orphans, tenant_id, datasource_key)
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
        described = await self._describe_tables(tables)
        to_write, skipped = await self._pending_scan_writes(
            tables, described, tenant_id=tenant_id, datasource_key=datasource_key
        )
        vectors = await self._embed_quietly(
            [
                build_embedding_text(b.table_name, b.table_comment, b.columns_comment, alias=ea)
                for _, _, b, ea in to_write
            ]
        )
        enriched_count = await self._upsert_scan_rows(
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            to_write=to_write,
            vectors=vectors,
        )

        # 对账(与 upsert 同事务):删扫描集外的自动来源登记,人工/反馈行
        # 保留告警;keep=全量扫描集(含被优先级 skip 的表——其现实存在,登记不删)
        keep = [(t.schema_name, b.table_name) for t, (_, b) in zip(tables, described, strict=True)]
        removed, manual_orphans = await self._repo.entries.delete_stale_entries(
            tenant_id=tenant_id, datasource_key=datasource_key, keep=keep
        )
        self._warn_manual_orphans(manual_orphans, tenant_id, datasource_key)
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
        return await self._repo.tenant.allowed_schemas(
            tenant_id=tenant_id, tenant_key=tenant_key, datasource_key=datasource_key
        )

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
        await require_usable_datasource(self._repo, tenant_id, datasource_key)
        # 未注入覆盖仓储(轻量装配)时按主仓储同实现读取:报告读面与会话同源
        coverage = self._coverage if self._coverage is not None else self._repo.coverage
        try:
            tables = await self._repo.tenant.introspect_all(
                tenant_id=tenant_id, tenant_key=tenant_key, datasource_key=datasource_key
            )
        except LookupError:
            raise DatasourceInvalid(datasource_key) from None
        entries = await coverage.list_all_entries(
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            visible_team_ids=await caller_team_ids(self._member_teams, caller_user_id),
        )
        signals = await coverage.failure_signals(
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            since=datetime.now(UTC) - timedelta(days=COVERAGE_FAILURE_WINDOW_DAYS),
        )
        overview = await coverage.history_overview(
            tenant_id=tenant_id, datasource_key=datasource_key
        )
        return build_coverage_report(
            datasource_key=datasource_key,
            tables=tables,
            entries=entries,
            signals=signals,
            history=overview,
        )
