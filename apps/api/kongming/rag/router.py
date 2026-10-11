"""rag 路由(009):知识库管理端点(/system/rag,租户视角+system:rag:manage)
与内部召回端点(/internal/rag/retrieve——api 全局内部令牌 middleware 守门)。

上传链路:multipart(file+datasource_key+schema_name)→ 文本类直读/PDF·Word
线程内解析 → Markdown `## 表名` 切块 → service 落库。
"""

import asyncio
import logging
import re
import uuid as uuidlib
from datetime import datetime
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.router import current_user, require_perm
from kongming.config import get_settings
from kongming.datasource.models import PG_IDENTIFIER_PATTERN
from kongming.db import get_session

if TYPE_CHECKING:
    from kongming.semantic.models import MetricHit

from kongming.rag.agent_callback import get_agent_rag_client
from kongming.rag.cache import ContextCache
from kongming.rag.chunker import EmptyDocument as DocEmpty
from kongming.rag.chunker import TooManyChunks
from kongming.rag.docparse import parse_to_text, supported_extension
from kongming.rag.models import (
    DatasourceInvalid,
    EmptyDocument,
    InvalidSchemaName,
    KnowledgeEntry,
    KnowledgeNotFound,
    SchemaNotAllowed,
    SchemaTablePair,
    TableBlock,
    TableNameTooLong,
    TeamWriteForbidden,
    TooManyBlocks,
    UnsupportedFileType,
)
from kongming.rag.parser import parse_markdown
from kongming.rag.repository import RagRepository
from kongming.rag.schemas import RecallProbeOut
from kongming.rag.service import RagService
from kongming.rag.service import TenantForbidden as TenantForbiddenApi
from kongming.redis_client import get_redis

log = logging.getLogger(__name__)

router = APIRouter(prefix="/system/rag", tags=["rag"])
internal_router = APIRouter(prefix="/internal/rag", tags=["internal"])

MAX_TABLE_BLOCKS = 500  # 单文档表块上限(防大文档切块风暴,design D7)


class _RelationExpansionAdapter:
    """095 组合根适配器:rag 域只见 RelationExpansionPort,关系查询走
    请求会话的 semantic 仓储(域间经端口反转,不直连;047 suggestion_evictor
    同款纪律)。tables 键序列转两段限定名字符串(关系行存储形态)。"""

    def __init__(self, session: AsyncSession) -> None:
        from kongming.semantic.repository import SqlSemanticRepo

        self._repo = SqlSemanticRepo(session)

    async def related_tables(
        self, *, tenant_id: str, datasource_key: str, tables: list[tuple[str, str]]
    ) -> list[dict[str, object]]:
        return await self._repo.relations_touching(
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            tables=[f"{schema}.{table}" for schema, table in tables],
        )


class _TeamMembershipAdapter:
    """102 组合根适配器:rag 域只见 MemberTeamsReader/MemberRoleReader 窄端口,
    成员关系查询走 team 域既有仓储(只读消费,team 域零改动;design D3)。
    team_ids_of_user 角色全收(可见性按「所在团队」判,VIEWER 亦在集合)。"""

    def __init__(self, session: AsyncSession) -> None:
        from kongming.team.repository import SqlTeamRepo

        self._repo = SqlTeamRepo(session)

    async def team_ids_of_user(self, user_id: str) -> list[str]:
        return list(await self._repo.role_map(user_id=user_id))

    async def role_of(self, tenant_id: str, team_id: str, user_id: str) -> str | None:
        return await self._repo.get_member_role_in_tenant(
            tenant_id=tenant_id, team_id=team_id, user_id=user_id
        )


def get_rag_service(session: AsyncSession) -> RagService:
    """RagService 公共组合根(019 toolcall 复用同源;013 单例工厂同调)。"""
    client = get_agent_rag_client(get_redis())  # 013:进程内单例,连接池跨请求复用
    from kongming.chat.repository import SuggestionRepository

    settings = get_settings()
    membership = _TeamMembershipAdapter(session)  # 102:双窄端口共用一适配器
    return RagService(
        # 098:仓储与缓存注入部署分词配置(缺省 simple 零回归)
        RagRepository(session, ts_config=settings.rag_fulltext_ts_config),
        embedder=client,
        describer=client,
        cache=ContextCache(get_redis(), ts_config=settings.rag_fulltext_ts_config),
        # 047:知识写路径级联失效建议缓存(DELETE 同事务,rag commit 收口)
        suggestion_evictor=SuggestionRepository(session),
        # 064:召回闸门配置(Settings 恒注入;None=测试轻量装配)
        gate_settings=settings,
        # 095:关系感知召回扩展(缺省开启;kill switch=rag_expand_enabled)
        relation_expander=_RelationExpansionAdapter(session),
        expand_enabled=settings.rag_expand_enabled,
        expand_max=settings.rag_expand_max,
        # 102:可见集合/写权限的成员关系端口(只读消费 team 域)
        member_teams=membership,
        member_roles=membership,
    )


def _get_service(session: Annotated[AsyncSession, Depends(get_session)]) -> RagService:
    return get_rag_service(session)


def _validate_schema_name(schema_name: str) -> None:
    """schema_name 领域校验(标识符 pattern 与 datasource 域同一规则,评审 S3)。"""
    if not PG_IDENTIFIER_PATTERN.fullmatch(schema_name):
        raise InvalidSchemaName("schema 须为合法 PG 标识符(小写字母/下划线起头)")


def _parse_upload(content: bytes, filename: str) -> list[TableBlock]:
    """字节 → 表块(解析/切块/上限,在工作线程内执行由调用方负责)。"""
    ext = supported_extension(filename)
    if ext is None:
        raise UnsupportedFileType("不支持的文件类型(md/markdown/txt/pdf/docx)")
    text = parse_to_text(content, filename)
    blocks = parse_markdown(text)
    if len(blocks) > MAX_TABLE_BLOCKS:
        raise TooManyBlocks(f"表块数超上限({MAX_TABLE_BLOCKS})")
    return blocks


# ============ 上传 ============


class UploadOut(BaseModel):
    tables: list[str]


@router.post(
    "/documents/upload",
    response_model=UploadOut,
    dependencies=[Depends(require_perm("system:rag:manage"))],
)
async def upload_document(
    file: Annotated[UploadFile, File()],
    datasource_key: Annotated[str, Form(pattern=r"^[a-z][a-z0-9_]{1,63}$")],
    schema_name: Annotated[str, Form()],
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[RagService, Depends(_get_service)],
    x_tenant_id: Annotated[str | None, Header()] = None,
    team_id: Annotated[uuidlib.UUID | None, Form()] = None,  # 102:上传归属(缺省公共)
) -> UploadOut:
    # 校验序:租户白名单复校先于文件读取(未绑定租户者不消耗解析资源;复审 R5)
    try:
        resolved2 = await svc.resolve_tenant(str(user["sub"]), x_tenant_id)
        tenant_id, tenant_key = resolved2
    except TenantForbiddenApi:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None

    settings = get_settings()
    # 大小上限按扩展名分档(文本类 1MB / PDF·Word 10MB;读 limit+1 判超限)
    ext = supported_extension(file.filename or "")
    if ext is None:
        raise HTTPException(status_code=400, detail="UNSUPPORTED_FILE_TYPE")
    limit = (
        settings.rag_binary_upload_max_bytes
        if ext in (".pdf", ".docx")
        else settings.rag_text_upload_max_bytes
    )
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(status_code=400, detail="DOCUMENT_TOO_LARGE")

    try:
        _validate_schema_name(schema_name)
        blocks = await asyncio.to_thread(_parse_upload, content, file.filename or "")
    except (
        InvalidSchemaName,
        UnsupportedFileType,
        EmptyDocument,
        TableNameTooLong,
        TooManyBlocks,
    ) as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    try:
        tables = await svc.upload_blocks(
            actor_id=str(user["sub"]),
            tenant_id=tenant_id,
            tenant_key=tenant_key,
            datasource_key=datasource_key,
            schema_name=schema_name,
            blocks=blocks,
            team_id=None if team_id is None else str(team_id),  # 102
        )
    except TenantForbiddenApi:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    except DatasourceInvalid:
        raise HTTPException(status_code=400, detail="DATASOURCE_INVALID") from None
    except SchemaNotAllowed:
        raise HTTPException(status_code=400, detail="SCHEMA_NOT_ALLOWED") from None
    except KnowledgeNotFound:
        # 102:归属目标团队不存在/非成员——同形 404(不暴露团队存在性)
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except TeamWriteForbidden:
        raise HTTPException(status_code=403, detail="TEAM_WRITE_FORBIDDEN") from None
    return UploadOut(tables=tables)


# ============ 可扫描 schema 枚举(040:上传表单候选源,与内省/上传闸同源) ============


class SchemasOut(BaseModel):
    datasource_key: str
    schemas: list[str]


@router.get(
    "/schemas",
    response_model=SchemasOut,
    dependencies=[Depends(require_perm("system:rag:manage"))],
)
async def list_allowed_schemas(
    datasource_key: Annotated[str, Query(pattern=r"^[a-z][a-z0-9_]{1,63}$")],
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[RagService, Depends(_get_service)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> SchemasOut:
    try:
        tenant_id, tenant_key = await svc.resolve_tenant(str(user["sub"]), x_tenant_id)
        schemas = await svc.allowed_schemas(
            tenant_id=tenant_id,
            tenant_key=tenant_key,
            datasource_key=datasource_key,
        )
    except TenantForbiddenApi:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    except DatasourceInvalid:
        raise HTTPException(status_code=400, detail="DATASOURCE_INVALID") from None
    return SchemasOut(datasource_key=datasource_key, schemas=schemas)


# ============ 知识列表/删除 ============


class KnowledgeItemOut(BaseModel):
    id: str
    datasource_key: str
    schema_name: str
    table_name: str
    table_comment: str
    columns_comment: dict[str, str]
    sample_queries: list[str]
    source_type: str
    updated_at: datetime
    alias: str = ""  # 037:业务称谓别名
    # 102(纯增可选):归属(空=租户公共;team_name 供列表归属列展示)
    team_id: str | None = None
    team_name: str | None = None


class KnowledgePageOut(BaseModel):
    items: list[KnowledgeItemOut]
    total: int
    page: int
    size: int


# 102:归属筛选取值(all/public/<team uuid>;pattern 双防——非枚举必须 UUID 形态)
_TEAM_SCOPE_PATTERN = (
    r"^(all|public|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)


@router.get(
    "/tables",
    response_model=KnowledgePageOut,
    dependencies=[Depends(require_perm("system:rag:manage"))],
)
async def list_tables(
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[RagService, Depends(_get_service)],
    x_tenant_id: Annotated[str | None, Header()] = None,
    datasource_key: str | None = None,
    # 102:归属筛选(all=公共∪我所在团队缺省形态 / public=仅公共 / <team_id>=该团队)
    team_scope: Annotated[str, Query(pattern=_TEAM_SCOPE_PATTERN)] = "all",
    page: Annotated[int, Field(ge=1)] = 1,
    size: Annotated[int, Field(ge=1, le=100)] = 20,
) -> KnowledgePageOut:
    try:
        resolved2 = await svc.resolve_tenant(str(user["sub"]), x_tenant_id)
        tenant_id = resolved2[0]
    except TenantForbiddenApi:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    entries, total = await svc.list_tables(
        tenant_id=tenant_id,
        datasource_key=datasource_key,
        limit=size,
        offset=(page - 1) * size,
        caller_user_id=str(user["sub"]),  # 102:可见集合
        team_scope=team_scope,  # 102:归属筛选
    )
    return KnowledgePageOut(
        items=[_knowledge_item_out(e) for e in entries],
        total=total,
        page=page,
        size=size,
    )


def _knowledge_item_out(e: KnowledgeEntry) -> KnowledgeItemOut:
    """条目 → 响应投影单点(列表/编辑/归属变更共用;102 归属字段纯增)。"""
    return KnowledgeItemOut(
        id=e.id,
        datasource_key=e.datasource_key,
        schema_name=e.schema_name,
        table_name=e.table_name,
        table_comment=e.table_comment,
        columns_comment=e.columns_comment,
        sample_queries=e.sample_queries,
        source_type=e.source_type,
        updated_at=e.updated_at,  # type: ignore[arg-type]
        alias=e.alias,  # 037
        team_id=e.team_id,  # 102
        team_name=e.team_name,  # 102
    )


class EditTableIn(BaseModel):
    """037:表说明人工精修请求(table_comment 与 alias 同面编辑)。"""

    table_comment: str = Field(max_length=500)
    alias: str = Field(max_length=64)

    @field_validator("table_comment", "alias")
    @classmethod
    def _strip_no_control(cls, v: str) -> str:
        # 换行/回车破坏 schema_hint 行结构(R1-F1);strip 后空串=清除语义
        if "\n" in v or "\r" in v:
            raise ValueError("不可包含换行")
        return v.strip()

    @field_validator("alias")
    @classmethod
    def _alias_no_structure_chars(cls, v: str) -> str:
        # 括号/竖线与 hint 行结构「表名(别名)(列…)」冲突,可借换行伪造表名
        # 注入 023 提取正则(R1-F1 实证);此处拒收,models.clean_alias 为纵深
        if any(c in v for c in "()（）|"):
            raise ValueError("别名不可包含括号或竖线")
        return v


@router.put(
    "/tables/{table_id}",
    response_model=KnowledgeItemOut,
    dependencies=[Depends(require_perm("system:rag:manage"))],
)
async def edit_table(
    table_id: str,
    body: EditTableIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[RagService, Depends(_get_service)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> KnowledgeItemOut:
    try:
        resolved2 = await svc.resolve_tenant(str(user["sub"]), x_tenant_id)
        tenant_id = resolved2[0]
    except TenantForbiddenApi:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    try:
        result = await svc.edit_table(
            actor_id=str(user["sub"]),
            tenant_id=tenant_id,
            table_id=table_id,
            table_comment=body.table_comment,
            alias=body.alias,
        )
    except KnowledgeNotFound:
        # 102:团队条目非成员写——与不存在同 detail(404 同形)
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except TeamWriteForbidden:
        raise HTTPException(status_code=403, detail="TEAM_WRITE_FORBIDDEN") from None
    if result is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return _knowledge_item_out(result)


class OwnershipIn(BaseModel):
    """102:归属变更请求(team_id 空=转公共;非空=转该团队专属)。"""

    team_id: uuidlib.UUID | None = None


@router.put(
    "/tables/{table_id}/ownership",
    response_model=KnowledgeItemOut,
    dependencies=[Depends(require_perm("system:rag:manage"))],
)
async def set_table_ownership(
    table_id: str,
    body: OwnershipIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[RagService, Depends(_get_service)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> KnowledgeItemOut:
    """归属变更(102):公共⇄团队原位转移(单一实例,内容不动)。

    非成员 404 同形(与不存在条目同 detail,不暴露存在性);VIEWER 403;
    变更落审计+精确失效检索缓存。web 侧走受控确认(AlertDialog 明示可见
    范围变化)。"""
    try:
        resolved2 = await svc.resolve_tenant(str(user["sub"]), x_tenant_id)
        tenant_id = resolved2[0]
    except TenantForbiddenApi:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    try:
        result = await svc.set_ownership(
            actor_id=str(user["sub"]),
            tenant_id=tenant_id,
            table_id=table_id,
            team_id=None if body.team_id is None else str(body.team_id),
        )
    except KnowledgeNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except TeamWriteForbidden:
        raise HTTPException(status_code=403, detail="TEAM_WRITE_FORBIDDEN") from None
    if result is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return _knowledge_item_out(result)


@router.delete(
    "/tables/{table_id}",
    status_code=204,
    dependencies=[Depends(require_perm("system:rag:manage"))],
)
async def delete_table(
    table_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[RagService, Depends(_get_service)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> None:
    try:
        resolved2 = await svc.resolve_tenant(str(user["sub"]), x_tenant_id)
        tenant_id = resolved2[0]
    except TenantForbiddenApi:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    try:
        deleted = await svc.delete_table(
            actor_id=str(user["sub"]), tenant_id=tenant_id, table_id=table_id
        )
    except KnowledgeNotFound:
        # 102:团队条目非成员写——与不存在同形(不暴露存在性)
        raise HTTPException(status_code=404, detail="KNOWLEDGE_NOT_FOUND") from None
    if deleted is None:
        raise HTTPException(status_code=404, detail="KNOWLEDGE_NOT_FOUND") from None


# ============ fetch-metadata / reindex ============


class DatasourceActionIn(BaseModel):
    datasource_key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")


class FetchOut(BaseModel):
    found: int
    enriched: int
    skipped: int
    removed: int  # 042:对账删除的扫描集外自动来源行数


class ReindexOut(BaseModel):
    deleted: int
    rebuilt: int
    enriched: int


@router.post(
    "/fetch-metadata",
    response_model=FetchOut,
    dependencies=[Depends(require_perm("system:rag:manage"))],
)
async def fetch_metadata(
    req: DatasourceActionIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[RagService, Depends(_get_service)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> FetchOut:
    try:
        tenant_id, tenant_key = await svc.resolve_tenant(str(user["sub"]), x_tenant_id)
        result = await svc.fetch_metadata(
            actor_id=str(user["sub"]),
            tenant_id=tenant_id,
            tenant_key=tenant_key,
            datasource_key=req.datasource_key,
        )
    except TenantForbiddenApi:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    except DatasourceInvalid:
        raise HTTPException(status_code=400, detail="DATASOURCE_INVALID") from None
    return FetchOut(
        found=result.found,
        enriched=result.enriched,
        skipped=result.skipped,
        removed=result.removed,
    )


@router.post(
    "/reindex",
    response_model=ReindexOut,
    dependencies=[Depends(require_perm("system:rag:manage"))],
)
async def reindex(
    req: DatasourceActionIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[RagService, Depends(_get_service)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> ReindexOut:
    try:
        tenant_id, tenant_key = await svc.resolve_tenant(str(user["sub"]), x_tenant_id)
        result = await svc.reindex(
            actor_id=str(user["sub"]),
            tenant_id=tenant_id,
            tenant_key=tenant_key,
            datasource_key=req.datasource_key,
        )
    except TenantForbiddenApi:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    except DatasourceInvalid:
        raise HTTPException(status_code=400, detail="DATASOURCE_INVALID") from None
    return ReindexOut(deleted=result.deleted, rebuilt=result.rebuilt, enriched=result.enriched)


# ============ 知识覆盖报告(052;只读确定性纯聚合) ============


class SchemaTableOut(BaseModel):
    schema_name: str
    table_name: str


class MissingColumnsOut(BaseModel):
    schema_name: str
    table_name: str
    missing: list[str]
    missing_count: int


class FailureClusterOut(BaseModel):
    question: str
    count: int
    last_status: str
    last_seen: datetime
    rating: str
    has_corrected_sql: bool


class HistoryOverviewOut(BaseModel):
    success_count: int
    distinct_questions: int
    cap: int


class CoverageReportOut(BaseModel):
    datasource_key: str
    unregistered_tables: list[SchemaTableOut]
    unregistered_total: int
    structure_only_tables: list[SchemaTableOut]
    structure_only_total: int
    missing_columns: list[MissingColumnsOut]
    missing_columns_total: int
    no_sample_tables: list[SchemaTableOut]
    no_sample_total: int
    history: HistoryOverviewOut
    failure_clusters: list[FailureClusterOut]
    failure_cluster_total: int


def _st(pairs: list[SchemaTablePair]) -> list[SchemaTableOut]:
    return [SchemaTableOut(schema_name=p.schema_name, table_name=p.table_name) for p in pairs]


@router.get(
    "/coverage-report",
    response_model=CoverageReportOut,
    dependencies=[Depends(require_perm("system:rag:manage"))],
)
async def coverage_report(
    datasource_key: Annotated[str, Query(pattern=r"^[a-z][a-z0-9_]{1,63}$")],
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[RagService, Depends(_get_service)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> CoverageReportOut:
    try:
        tenant_id, tenant_key = await svc.resolve_tenant(str(user["sub"]), x_tenant_id)
        report = await svc.coverage_report(
            tenant_id=tenant_id,
            tenant_key=tenant_key,
            datasource_key=datasource_key,
            caller_user_id=str(user["sub"]),
        )
    except TenantForbiddenApi:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    except DatasourceInvalid:
        # 含内省失败(整体失败,不产出半份报告;与 fetch-metadata 同形)
        raise HTTPException(status_code=400, detail="DATASOURCE_INVALID") from None
    return CoverageReportOut(
        datasource_key=report.datasource_key,
        unregistered_tables=_st(report.unregistered_tables),
        unregistered_total=report.unregistered_total,
        structure_only_tables=_st(report.structure_only_tables),
        structure_only_total=report.structure_only_total,
        missing_columns=[
            MissingColumnsOut(
                schema_name=m.schema_name,
                table_name=m.table_name,
                missing=m.missing,
                missing_count=m.missing_count,
            )
            for m in report.missing_columns
        ],
        missing_columns_total=report.missing_columns_total,
        no_sample_tables=_st(report.no_sample_tables),
        no_sample_total=report.no_sample_total,
        history=HistoryOverviewOut(
            success_count=report.history.success_count,
            distinct_questions=report.history.distinct_questions,
            cap=report.history.cap,
        ),
        failure_clusters=[
            FailureClusterOut(
                question=c.question,
                count=c.count,
                last_status=c.last_status,
                last_seen=c.last_seen,
                rating=c.rating,
                has_corrected_sql=c.has_corrected_sql,
            )
            for c in report.failure_clusters
        ],
        failure_cluster_total=report.failure_cluster_total,
    )


# ============ 内部召回端点(agent 回调;/internal 前缀由全局 middleware 令牌守门) ============


class RetrieveIn(BaseModel):
    tenant_key: str = Field(min_length=1, max_length=64)
    datasource_key: str = Field(default="default", pattern=r"^[a-z][a-z0-9_]{1,63}$")
    question: str = Field(min_length=1, max_length=2000)
    # 047 多轮继承:最近历史问句(≤3 条,取末条走继承路;单轮缺省零回归)
    history: list[Annotated[str, Field(max_length=2000)]] = Field(
        default_factory=list, max_length=3
    )
    # 102 纯增可选:调用者身份(端用户 id)——可见集合=公共∪其所在团队;
    # 缺省(未携带)=纯公共形态,既有回调负载零回归(agent 面无须改动即可
    # 继续工作;接线后团队条目方进入召回)
    user_id: uuidlib.UUID | None = None


class MetricHitOut(BaseModel):
    """076(纯增):语义层口径命中(标识/名称/口径表达式/修订时间)。"""

    metric_id: str
    metric_name: str
    sql_template: str
    updated_at: str | None = None


class RetrieveOut(BaseModel):
    schema_hint: str
    history_sql: str
    # 023 语义段(纯增;空串=无注入,agent 零回归)
    relations_hint: str = ""
    metric_hints: str = ""
    # 076(纯增):结构化口径命中(与 metric_hints 同源;空=无命中)
    metric_hits: list[MetricHitOut] = []
    # 024 文档段(纯增;空=无命中,双通道隔离)。
    # 068:每项纯增 citation_key(稳定引用键;由父块主键派生)
    doc_hints: list[dict[str, str]] = []
    # 056 skill-pack 投影段(纯增;空=零包零回归)
    skill_terms: str = ""
    skill_examples: str = ""
    skill_persona: str = ""


@internal_router.post("/retrieve", response_model=RetrieveOut)
async def rag_retrieve(
    req: RetrieveIn,
    svc: Annotated[RagService, Depends(_get_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RetrieveOut:
    try:
        ctx = await svc.recall(
            tenant_key=req.tenant_key,
            datasource_key=req.datasource_key,
            question=req.question,
            history=req.history,  # 047 多轮继承
            caller_user_id=None if req.user_id is None else str(req.user_id),  # 102
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="TENANT_NOT_FOUND") from None
    except DatasourceInvalid:
        raise HTTPException(status_code=400, detail="DATASOURCE_INVALID") from None
    # 023:语义段组装(召回表集合收窄;失败降级空串不影响召回主链路)
    relations_hint = ""
    metric_hints = ""
    metric_hits_objs: list[MetricHit] = []
    try:
        import re as _re

        from kongming.semantic.repository import SqlSemanticRepo
        from kongming.semantic.service import SemanticService

        tid_row = (
            await session.execute(
                text("SELECT id FROM public.sys_tenant WHERE tenant_key=:k AND status='ACTIVE'"),
                {"k": req.tenant_key},
            )
        ).scalar_one_or_none()
        if tid_row:
            # 召回形态唯一:build_schema_hint=`schema.table(列…)`(009;R1-L2
            # 删「表 x.y」死路径——该形态只存在于 agent 侧内省回退,不经此处)
            tables = _re.findall(r"^([\w]+\.[\w]+)\(", ctx.schema_hint, _re.M)
            tables = list(dict.fromkeys(tables))
            relations_hint, metric_hints, metric_hits_objs = await SemanticService(
                SqlSemanticRepo(session)
            ).enrich(
                tenant_id=str(tid_row),
                datasource_key=req.datasource_key,
                question=req.question,
                tables=tables,
            )
    except Exception as _e:
        # R1-L3:undefined_table(0012 未迁移)升 error(运维可感知整体失效);
        # 其余异常仍降级空串不阻断召回主链路
        if "UndefinedTable" in type(_e).__name__ or "undefined_table" in str(_e):
            log.error("语义层表缺失(迁移未跑?),注入整体失效: %s", _e)
        else:
            log.warning("语义段组装失败(降级空串)", exc_info=True)
    # 024:文档通道(降级空;与 schema 通道隔离)
    doc_hints: list[dict[str, str]] = []
    try:
        from kongming.rag.documents_repo import SqlDocumentRepo
        from kongming.rag.documents_service import retrieve_docs

        tid2 = (
            await session.execute(
                text("SELECT id FROM public.sys_tenant WHERE tenant_key=:k AND status='ACTIVE'"),
                {"k": req.tenant_key},
            )
        ).scalar_one_or_none()
        if tid2:
            doc_hints = await retrieve_docs(
                SqlDocumentRepo(session, ts_config=get_settings().rag_fulltext_ts_config),
                get_agent_rag_client(get_redis()),
                tenant_id=str(tid2),
                datasource_key=req.datasource_key,
                question=req.question,
            )
    except Exception:
        log.warning("文档检索组装失败(降级空)", exc_info=True)
    # 056:skill-pack 合并投影(零包=空段;失败降级不阻断)
    skill_terms = skill_examples = skill_persona = ""
    try:
        from kongming.skillpack.repository import SqlSkillPackRepo
        from kongming.skillpack.service import SkillPackService

        tid3 = (
            await session.execute(
                text("SELECT id FROM public.sys_tenant WHERE tenant_key=:k AND status='ACTIVE'"),
                {"k": req.tenant_key},
            )
        ).scalar_one_or_none()
        if tid3:
            proj = await SkillPackService(SqlSkillPackRepo(session)).projection(
                tenant_id=str(tid3), datasource_key=req.datasource_key
            )
            skill_terms, skill_examples, skill_persona = (
                proj.skill_terms,
                proj.skill_examples,
                proj.skill_persona,
            )
    except Exception:
        log.warning("skill-pack 投影组装失败(降级空)", exc_info=True)
    metric_hits_out = [
        MetricHitOut(
            metric_id=h.metric_id,
            metric_name=h.metric_name,
            sql_template=h.sql_template,
            updated_at=h.updated_at,
        )
        for h in metric_hits_objs
    ]
    return RetrieveOut(
        schema_hint=ctx.schema_hint,
        history_sql=ctx.history_sql,
        relations_hint=relations_hint,
        metric_hints=metric_hints,
        metric_hits=metric_hits_out,  # 076(纯增):结构化口径命中
        doc_hints=doc_hints,
        skill_terms=skill_terms,
        skill_examples=skill_examples,
        skill_persona=skill_persona,
    )


@router.get(
    "/recall-probe",
    response_model=RecallProbeOut,
    dependencies=[Depends(require_perm("system:rag:manage"))],
)
async def recall_probe(
    question: Annotated[str, Query(min_length=1, max_length=2000)],
    datasource_key: Annotated[str, Query(pattern=r"^[a-z][a-z0-9_]{1,63}$")],
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[RagService, Depends(_get_service)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> RecallProbeOut:
    """064:召回试跑(只读):三路命中及各自原生分、融合分、双条件判定结论。

    零副作用:SHALL NOT 修改配置、SHALL NOT 落库、SHALL NOT 触发召回缓存
    失效(不走 ctx 缓存)。两个高频场景:上传表说明后验证可召回性;闸门
    阈值标定(不等真实流量)。102:召回在调用者可见集合内(公共∪所在团队)。
    097:响应模型纯定型(键集=service 现值锁定,判定逻辑零变更)。
    """
    try:
        _tenant_id, tenant_key = await svc.resolve_tenant(str(user["sub"]), x_tenant_id)
        return RecallProbeOut.model_validate(
            await svc.recall_probe(
                tenant_key=tenant_key,
                datasource_key=datasource_key,
                question=question,
                caller_user_id=str(user["sub"]),
            )
        )
    except TenantForbiddenApi:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    except DatasourceInvalid:
        raise HTTPException(status_code=400, detail="DATASOURCE_INVALID") from None
    except LookupError:
        raise HTTPException(status_code=404, detail="TENANT_NOT_FOUND") from None


# ============ 024 document-rag:文档管理端点 ============

doc_router = APIRouter(prefix="/system/document", tags=["document"])


def _doc_name_sensitive(name: str) -> bool:
    """上传文件名敏感信息检测(纯检测,不脱敏):复用护栏 pii_*/cred_ 规则
    的正则,命中即拒——文件名原样入库/回显/进 LLM 提示词,须在入口拦下;
    bankcard 规则既有 19/20 前缀排除(时间戳防误伤)对文件名同样生效。"""
    from kongming.guardrail.rules import REGISTRY

    for rule in REGISTRY:
        if rule.guardrail_type == "input" and rule.name.startswith(("pii_", "cred_", "inj_")):
            # inj_ 一并拦(R1-B2:文件名经 source_ref 进召回文本→LLM 提示词,
            # injection 短语文件名与 PII 同为入口防线)
            if rule.pattern and re.search(rule.pattern, name):
                return True
    return False


async def _doc_tenant(
    session: AsyncSession, svc: RagService, user_id: str, tenant_key: str | None
) -> str:
    """R1-M4:统一 403 映射(与 009 端点同口径)。"""
    try:
        resolved = await svc.resolve_tenant(user_id, tenant_key)
    except TenantForbiddenApi:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    return resolved[0]


async def _assert_doc_datasource(
    session: AsyncSession, *, tenant_id: str, datasource_key: str
) -> None:
    """R1-M1:数据源存在+启用+归属校验(伪造同形 400;spec「伪造 datasource_key
    同形拒绝」);default 恒可用。"""
    if datasource_key == "default":
        return
    row = (
        (
            await session.execute(
                text(
                    "SELECT status, tenant_id FROM public.sys_datasource WHERE datasource_key = :k"
                ),
                {"k": datasource_key},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None or row["status"] != "ACTIVE" or str(row["tenant_id"]) != tenant_id:
        raise HTTPException(status_code=400, detail="DATASOURCE_INVALID") from None


class DocumentItemOut(BaseModel):
    id: str
    doc_name: str
    block_count: int
    updated_at: str


class DocumentChunkOut(BaseModel):
    id: str
    chunk_level: str
    seq: int
    source_ref: str | None
    has_embedding: bool
    content: str


@doc_router.get("/documents", response_model=list[DocumentItemOut])
async def list_documents(
    user: Annotated[dict[str, object], Depends(current_user)],
    _: Annotated[None, Depends(require_perm("system:document:manage"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    datasource_key: str = "default",
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> list[DocumentItemOut]:
    from kongming.rag.documents_repo import SqlDocumentRepo

    svc = _get_service(session)
    tid = await _doc_tenant(session, svc, str(user["sub"]), x_tenant_id)
    await _assert_doc_datasource(session, tenant_id=tid, datasource_key=datasource_key)
    rows = await SqlDocumentRepo(
        session, ts_config=get_settings().rag_fulltext_ts_config
    ).list_documents(tenant_id=tid, datasource_key=datasource_key)
    return [
        DocumentItemOut(
            id=str(r["id"]),
            doc_name=str(r["doc_name"]),
            block_count=int(r["block_count"]),
            updated_at=str(r["updated_at"]),
        )
        for r in rows
    ]


class DocUploadOut(BaseModel):
    doc_id: str
    blocks: int
    skipped: bool


@doc_router.post("/upload", response_model=DocUploadOut)
async def upload_doc(
    file: Annotated[UploadFile, File()],
    datasource_key: Annotated[str, Form(pattern=r"^[a-z][a-z0-9_]{1,63}$")],
    user: Annotated[dict[str, object], Depends(current_user)],
    _: Annotated[None, Depends(require_perm("system:document:manage"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> DocUploadOut:
    """文档上传(024):类型/大小沿用 009 分档;分块+embedding(子块)+覆盖落库。"""
    from kongming.rag.documents_repo import SqlDocumentRepo
    from kongming.rag.documents_service import DocumentConflict, upload_document

    svc = _get_service(session)
    tenant_id = await _doc_tenant(session, svc, str(user["sub"]), x_tenant_id)
    await _assert_doc_datasource(session, tenant_id=tenant_id, datasource_key=datasource_key)
    settings = get_settings()
    ext = supported_extension(file.filename or "")
    if ext is None:
        raise HTTPException(status_code=400, detail="不支持的文件类型(md/txt/pdf/docx)") from None
    max_bytes = (
        settings.rag_binary_upload_max_bytes
        if ext in ("pdf", "docx")
        else settings.rag_text_upload_max_bytes
    )
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(status_code=400, detail="文件超大小上限") from None
    doc_name = file.filename or "unnamed"
    if len(doc_name) > 150:
        raise HTTPException(status_code=400, detail="文件名过长(≤150)") from None
    if _doc_name_sensitive(doc_name):
        # 拒绝优于脱敏:脱敏后名可能撞文档唯一键(uq_documents_tdq)
        raise HTTPException(
            status_code=400, detail="文件名含敏感信息(手机号/证件号等),请重命名后上传"
        ) from None
    try:
        text_content = await asyncio.to_thread(parse_to_text, content, file.filename or "")
    except Exception:
        raise HTTPException(status_code=400, detail="文档解析失败") from None
    try:
        doc_id, blocks, skipped = await upload_document(
            SqlDocumentRepo(session, ts_config=get_settings().rag_fulltext_ts_config),
            get_agent_rag_client(get_redis()),
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            doc_name=doc_name,
            text=text_content,
        )
    except DocEmpty:
        raise HTTPException(status_code=400, detail="文档内容为空") from None
    except TooManyChunks as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except DocumentConflict:
        raise HTTPException(status_code=409, detail="同名文档并发上传冲突,请重试") from None
    return DocUploadOut(doc_id=doc_id, blocks=blocks, skipped=skipped)


@doc_router.delete("/documents/{doc_id}", status_code=204)
async def delete_doc(
    doc_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    _: Annotated[None, Depends(require_perm("system:document:manage"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> None:
    from kongming.rag.documents_repo import SqlDocumentRepo

    svc = _get_service(session)
    tid = await _doc_tenant(session, svc, str(user["sub"]), x_tenant_id)
    ok = await SqlDocumentRepo(
        session, ts_config=get_settings().rag_fulltext_ts_config
    ).delete_document(doc_id=doc_id, tenant_id=tid)
    if not ok:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    await session.commit()


@doc_router.get("/documents/{doc_id}/chunks", response_model=list[DocumentChunkOut])
async def list_doc_chunks(
    doc_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    _: Annotated[None, Depends(require_perm("system:document:manage"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> list[DocumentChunkOut]:
    from kongming.rag.documents_repo import SqlDocumentRepo

    svc = _get_service(session)
    tid = await _doc_tenant(session, svc, str(user["sub"]), x_tenant_id)
    rows = await SqlDocumentRepo(
        session, ts_config=get_settings().rag_fulltext_ts_config
    ).list_chunks(doc_id=doc_id, tenant_id=tid)
    return [
        DocumentChunkOut(
            id=str(r["id"]),
            chunk_level=str(r["chunk_level"]),
            seq=int(r["seq"]),
            source_ref=None if r["source_ref"] is None else str(r["source_ref"]),
            has_embedding=bool(r["has_embedding"]),
            content=str(r["content"]),
        )
        for r in rows
    ]
