"""知识库管理与内部召回路由。文件上传先解析为文本，再按 Markdown 表标题切块并交给 service 保存。"""

import asyncio
import logging
import re
import uuid as uuidlib
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from pydantic import Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.deps import current_user, require_perm
from kongming.config import get_settings
from kongming.datasource.models import PG_IDENTIFIER_PATTERN
from kongming.db import get_session

if TYPE_CHECKING:
    from kongming.semantic.models import MetricHit

from kongming.rag.admin_service import RagAdminService
from kongming.rag.agent_callback import get_agent_rag_client
from kongming.rag.chunker import EmptyDocument as DocEmpty
from kongming.rag.chunker import TooManyChunks
from kongming.rag.dependencies import get_rag_admin_service_dep, get_rag_service_dep
from kongming.rag.docparse import parse_to_text, supported_extension
from kongming.rag.models import (
    DatasourceInvalid,
    EmptyDocument,
    InvalidSchemaName,
    KnowledgeNotFound,
    SchemaNotAllowed,
    TableBlock,
    TableNameTooLong,
    TeamWriteForbidden,
    TooManyBlocks,
    UnsupportedFileType,
)
from kongming.rag.parser import parse_markdown
from kongming.rag.schemas import (
    CoverageReportOut,
    DatasourceActionIn,
    DocEnrichOut,
    DocumentChunkOut,
    DocumentItemOut,
    DocUploadOut,
    EditTableIn,
    FailureClusterOut,
    FetchOut,
    HistoryOverviewOut,
    KnowledgeItemOut,
    KnowledgePageOut,
    MetricHitOut,
    MissingColumnsOut,
    OwnershipIn,
    RecallProbeOut,
    ReindexOut,
    RetrievalProbeOut,
    RetrieveIn,
    RetrieveOut,
    SchemasOut,
    UploadOut,
    to_knowledge_item_out,
    to_schema_table_out,
)
from kongming.rag.service import RagService
from kongming.rag.service import TenantForbidden as TenantForbiddenApi
from kongming.redis_client import get_redis

log = logging.getLogger(__name__)

router = APIRouter(prefix="/system/rag", tags=["rag"])
internal_router = APIRouter(prefix="/internal/rag", tags=["internal"])

MAX_TABLE_BLOCKS = 500  # 单文档表块上限(防大文档切块风暴)


def _validate_schema_name(schema_name: str) -> None:
    """按 datasource 域的标识符规则校验 schema_name。"""
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


async def _resolve_admin_tenant(
    svc: RagAdminService, user_id: str, x_tenant_id: str | None
) -> tuple[str, str]:
    """管理面租户解析;租户未绑定统一映射 403。"""
    try:
        return await svc.resolve_tenant(user_id, x_tenant_id)
    except TenantForbiddenApi:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None


def _upload_kind_and_limit(filename: str) -> tuple[str, int]:
    """按扩展名判定上传分档并给出字节上限(文本类 / PDF·Word)。"""
    ext = supported_extension(filename)
    if ext is None:
        raise HTTPException(status_code=400, detail="UNSUPPORTED_FILE_TYPE")
    settings = get_settings()
    limit = (
        settings.rag_binary_upload_max_bytes
        if ext in (".pdf", ".docx")
        else settings.rag_text_upload_max_bytes
    )
    return ext, limit


async def _parse_blocks_or_400(content: bytes, filename: str) -> list[TableBlock]:
    """解析上传文件为表块;解析与切块类错误统一映射 400。"""
    try:
        blocks = await asyncio.to_thread(_parse_upload, content, filename)
    except (
        InvalidSchemaName,
        UnsupportedFileType,
        EmptyDocument,
        TableNameTooLong,
        TooManyBlocks,
    ) as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return blocks


async def _upload_blocks_or_map(
    svc: RagAdminService,
    *,
    actor_id: str,
    tenant_id: str,
    tenant_key: str,
    datasource_key: str,
    schema_name: str,
    blocks: list[TableBlock],
    team_id: str | None,
) -> list[str]:
    """写入上传表块;租户/数据源/schema/团队闸异常统一映射为对应状态码。"""
    try:
        return await svc.upload_blocks(
            actor_id=actor_id,
            tenant_id=tenant_id,
            tenant_key=tenant_key,
            datasource_key=datasource_key,
            schema_name=schema_name,
            blocks=blocks,
            team_id=team_id,
        )
    except TenantForbiddenApi:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    except DatasourceInvalid:
        raise HTTPException(status_code=400, detail="DATASOURCE_INVALID") from None
    except SchemaNotAllowed:
        raise HTTPException(status_code=400, detail="SCHEMA_NOT_ALLOWED") from None
    except KnowledgeNotFound:
        # 归属目标团队不存在/非成员——同形 404(不暴露团队存在性)
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except TeamWriteForbidden:
        raise HTTPException(status_code=403, detail="TEAM_WRITE_FORBIDDEN") from None


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
    svc: Annotated[RagAdminService, Depends(get_rag_admin_service_dep)],
    x_tenant_id: Annotated[str | None, Header()] = None,
    team_id: Annotated[uuidlib.UUID | None, Form()] = None,  # 上传归属(缺省公共)
) -> UploadOut:
    # 先复核租户绑定，再读取文件，避免未绑定租户消耗解析资源。
    tenant_id, tenant_key = await _resolve_admin_tenant(svc, str(user["sub"]), x_tenant_id)
    _, limit = _upload_kind_and_limit(file.filename or "")
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(status_code=400, detail="DOCUMENT_TOO_LARGE")
    try:
        _validate_schema_name(schema_name)
    except InvalidSchemaName as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    blocks = await _parse_blocks_or_400(content, file.filename or "")
    tables = await _upload_blocks_or_map(
        svc,
        actor_id=str(user["sub"]),
        tenant_id=tenant_id,
        tenant_key=tenant_key,
        datasource_key=datasource_key,
        schema_name=schema_name,
        blocks=blocks,
        team_id=None if team_id is None else str(team_id),
    )
    return UploadOut(tables=tables)


# ============ 可扫描 schema 枚举(上传表单候选源,与内省/上传闸同源) ============


@router.get(
    "/schemas",
    response_model=SchemasOut,
    dependencies=[Depends(require_perm("system:rag:manage"))],
)
async def list_allowed_schemas(
    datasource_key: Annotated[str, Query(pattern=r"^[a-z][a-z0-9_]{1,63}$")],
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[RagAdminService, Depends(get_rag_admin_service_dep)],
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


# 归属筛选取值(all/public/<team uuid>;pattern 双防——非枚举必须 UUID 形态)
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
    svc: Annotated[RagAdminService, Depends(get_rag_admin_service_dep)],
    x_tenant_id: Annotated[str | None, Header()] = None,
    datasource_key: str | None = None,
    # 归属筛选(all=公共∪我所在团队缺省形态 / public=仅公共 / <team_id>=该团队)
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
        caller_user_id=str(user["sub"]),  # 可见集合
        team_scope=team_scope,  # 归属筛选
    )
    return KnowledgePageOut(
        items=[to_knowledge_item_out(e) for e in entries],
        total=total,
        page=page,
        size=size,
    )


@router.put(
    "/tables/{table_id}",
    response_model=KnowledgeItemOut,
    dependencies=[Depends(require_perm("system:rag:manage"))],
)
async def edit_table(
    table_id: str,
    body: EditTableIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[RagAdminService, Depends(get_rag_admin_service_dep)],
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
        # 团队条目非成员写——与不存在同 detail(404 同形)
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except TeamWriteForbidden:
        raise HTTPException(status_code=403, detail="TEAM_WRITE_FORBIDDEN") from None
    if result is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return to_knowledge_item_out(result)


@router.put(
    "/tables/{table_id}/ownership",
    response_model=KnowledgeItemOut,
    dependencies=[Depends(require_perm("system:rag:manage"))],
)
async def set_table_ownership(
    table_id: str,
    body: OwnershipIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[RagAdminService, Depends(get_rag_admin_service_dep)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> KnowledgeItemOut:
    """归属变更:公共⇄团队原位转移(单一实例,内容不动)。

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
    return to_knowledge_item_out(result)


@router.delete(
    "/tables/{table_id}",
    status_code=204,
    dependencies=[Depends(require_perm("system:rag:manage"))],
)
async def delete_table(
    table_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[RagAdminService, Depends(get_rag_admin_service_dep)],
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
        # 团队条目非成员写——与不存在同形(不暴露存在性)
        raise HTTPException(status_code=404, detail="KNOWLEDGE_NOT_FOUND") from None
    if deleted is None:
        raise HTTPException(status_code=404, detail="KNOWLEDGE_NOT_FOUND") from None


# ============ fetch-metadata / reindex ============


@router.post(
    "/fetch-metadata",
    response_model=FetchOut,
    dependencies=[Depends(require_perm("system:rag:manage"))],
)
async def fetch_metadata(
    req: DatasourceActionIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[RagAdminService, Depends(get_rag_admin_service_dep)],
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
    svc: Annotated[RagAdminService, Depends(get_rag_admin_service_dep)],
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


# ============ 知识覆盖报告(只读确定性纯聚合) ============


@router.get(
    "/coverage-report",
    response_model=CoverageReportOut,
    dependencies=[Depends(require_perm("system:rag:manage"))],
)
async def coverage_report(
    datasource_key: Annotated[str, Query(pattern=r"^[a-z][a-z0-9_]{1,63}$")],
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[RagAdminService, Depends(get_rag_admin_service_dep)],
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
        unregistered_tables=to_schema_table_out(report.unregistered_tables),
        unregistered_total=report.unregistered_total,
        structure_only_tables=to_schema_table_out(report.structure_only_tables),
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
        no_sample_tables=to_schema_table_out(report.no_sample_tables),
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


async def _tenant_id_by_key(session: AsyncSession, tenant_key: str) -> str | None:
    """按租户键查启用态租户主键;未命中返回 None。"""
    row = (
        await session.execute(
            text("SELECT id FROM public.sys_tenant WHERE tenant_key=:k AND status='ACTIVE'"),
            {"k": tenant_key},
        )
    ).scalar_one_or_none()
    return None if row is None else str(row)


async def _semantic_hints(
    session: AsyncSession, req: RetrieveIn, schema_hint: str
) -> tuple[str, str, list["MetricHit"]]:
    """语义段:按召回表集合生成关系/指标提示;失败降级为空提示。"""
    relations_hint = ""
    metric_hints = ""
    metric_hits: list[MetricHit] = []
    try:
        import re as _re

        from kongming.semantic.adapters import CallerDeptScopeAdapter
        from kongming.semantic.repository import SqlSemanticRepo
        from kongming.semantic.service import SemanticService

        tid_row = await _tenant_id_by_key(session, req.tenant_key)
        if tid_row:
            # 召回形态唯一:build_schema_hint=`schema.table(列…)`;
            # 「表 x.y」形态只存在于 agent 侧内省回退,不经此处
            tables = _re.findall(r"^([\w]+\.[\w]+)\(", schema_hint, _re.M)
            tables = list(dict.fromkeys(tables))
            relations_hint, metric_hints, metric_hits = await SemanticService(
                SqlSemanticRepo(session),
                # 指标注入面按调用者 data_scope 收窄(解析失败 service 内读面降级)
                caller_scope_resolver=CallerDeptScopeAdapter(session),
            ).enrich(
                tenant_id=tid_row,
                datasource_key=req.datasource_key,
                question=req.question,
                tables=tables,
                caller_user_id=None if req.user_id is None else str(req.user_id),
            )
    except Exception as _e:
        # 缺少语义层表时按迁移未执行处理并报错；其他异常降级为空提示，继续召回。
        if "UndefinedTable" in type(_e).__name__ or "undefined_table" in str(_e):
            log.exception("语义层表缺失(迁移未跑?),注入整体失效")
        else:
            log.warning("语义段组装失败(降级空串)", exc_info=True)
    return relations_hint, metric_hints, metric_hits


async def _doc_hints(session: AsyncSession, req: RetrieveIn) -> list[dict[str, str]]:
    """文档通道提示(降级空;与 schema 通道隔离)。"""
    doc_hints: list[dict[str, str]] = []
    try:
        from kongming.rag.documents_repo import SqlDocumentRepo
        from kongming.rag.documents_service import retrieve_docs
        from kongming.rag.entity_extract import build_doc_entity_extractor
        from kongming.rag.rerank import build_doc_reranker

        tid2 = await _tenant_id_by_key(session, req.tenant_key)
        if tid2:
            doc_hints = await retrieve_docs(
                SqlDocumentRepo(session, ts_config=get_settings().rag_fulltext_ts_config),
                get_agent_rag_client(get_redis()),
                tenant_id=tid2,
                datasource_key=req.datasource_key,
                question=req.question,
                # 未配置时为 None(无精排),精排失败在内部降级
                reranker=build_doc_reranker(),
                entity_extractor=build_doc_entity_extractor(),
                entity_quota=get_settings().doc_entity_query_max,
            )
    except Exception:
        log.warning("文档检索组装失败(降级空)", exc_info=True)
    return doc_hints


async def _skill_hints(session: AsyncSession, req: RetrieveIn) -> tuple[str, str, str]:
    """skill-pack 投影段(零包=空串;失败降级不阻断);返回 (terms, examples, persona)。"""
    skill_terms = skill_examples = skill_persona = ""
    try:
        from kongming.skillpack.repository import SqlSkillPackRepo
        from kongming.skillpack.service import SkillPackService

        tid3 = await _tenant_id_by_key(session, req.tenant_key)
        if tid3:
            proj = await SkillPackService(SqlSkillPackRepo(session)).projection(
                tenant_id=tid3, datasource_key=req.datasource_key
            )
            skill_terms, skill_examples, skill_persona = (
                proj.skill_terms,
                proj.skill_examples,
                proj.skill_persona,
            )
    except Exception:
        log.warning("skill-pack 投影组装失败(降级空)", exc_info=True)
    return skill_terms, skill_examples, skill_persona


@internal_router.post("/retrieve", response_model=RetrieveOut)
async def rag_retrieve(
    req: RetrieveIn,
    svc: Annotated[RagService, Depends(get_rag_service_dep)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RetrieveOut:
    try:
        ctx = await svc.recall(
            tenant_key=req.tenant_key,
            datasource_key=req.datasource_key,
            question=req.question,
            history=req.history,  # 多轮继承
            caller_user_id=None if req.user_id is None else str(req.user_id),
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="TENANT_NOT_FOUND") from None
    except DatasourceInvalid:
        raise HTTPException(status_code=400, detail="DATASOURCE_INVALID") from None
    # 语义提示用于收窄召回表集合；生成失败时传空提示继续召回。
    relations_hint, metric_hints, metric_hits_objs = await _semantic_hints(
        session, req, ctx.schema_hint
    )
    doc_hints = await _doc_hints(session, req)
    skill_terms, skill_examples, skill_persona = await _skill_hints(session, req)
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
        metric_hits=metric_hits_out,  # 结构化口径命中
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
    svc: Annotated[RagService, Depends(get_rag_service_dep)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> RecallProbeOut:
    """召回试跑(只读):三路命中及各自原生分、融合分、双条件判定结论。

    零副作用:SHALL NOT 修改配置、SHALL NOT 落库、SHALL NOT 触发召回缓存
    失效(不走 ctx 缓存)。两个高频场景:上传表说明后验证可召回性;闸门
    阈值标定(不等真实流量)。召回在调用者可见集合内(公共∪所在团队)。
    响应模型纯定型(键集=service 现值锁定,判定逻辑零变更)。
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


# ============ document-rag:文档管理端点 ============

doc_router = APIRouter(prefix="/system/document", tags=["document"])


def _doc_name_sensitive(name: str) -> bool:
    """上传文件名敏感信息检测(纯检测,不脱敏):复用护栏 pii_*/cred_ 规则
    的正则,命中即拒——文件名原样入库/回显/进 LLM 提示词,须在入口拦下;
    bankcard 规则既有 19/20 前缀排除(时间戳防误伤)对文件名同样生效。"""
    from kongming.guardrail.rules import REGISTRY

    for rule in REGISTRY:
        if (
            rule.guardrail_type == "input"
            and rule.name.startswith(("pii_", "cred_", "inj_"))
            # inj_ 一并拦:文件名经 source_ref 进召回文本→LLM 提示词,
            # 注入短语与 PII 同为入口防线
            and rule.pattern
            and re.search(rule.pattern, name)
        ):
            return True
    return False


async def _doc_tenant(
    session: AsyncSession, svc: RagService, user_id: str, tenant_key: str | None
) -> str:
    """统一 403 映射(document 各端点共用)。"""
    try:
        resolved = await svc.resolve_tenant(user_id, tenant_key)
    except TenantForbiddenApi:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    return resolved[0]


async def _assert_doc_datasource(
    session: AsyncSession, *, tenant_id: str, datasource_key: str
) -> None:
    """数据源存在+启用+归属校验(伪造同形 400;spec「伪造 datasource_key
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


@doc_router.get("/documents", response_model=list[DocumentItemOut])
async def list_documents(
    user: Annotated[dict[str, object], Depends(current_user)],
    _: Annotated[None, Depends(require_perm("system:document:manage"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    datasource_key: str = "default",
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> list[DocumentItemOut]:
    from kongming.rag.documents_repo import SqlDocumentRepo

    svc = get_rag_service_dep(session)
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


async def _read_doc_upload(file: UploadFile) -> tuple[str, bytes]:
    """校验文件名(非空/长度/敏感信息)并读取正文;返回 (文档名, 内容)。"""
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
    return doc_name, content


async def _ingest_doc_upload(
    session: AsyncSession, *, tenant_id: str, datasource_key: str, doc_name: str, text: str
) -> tuple[str, int, bool]:
    """分块+embedding+覆盖落库;空文档/超块数/并发同名冲突映射为对应状态码。"""
    from kongming.rag.documents_repo import SqlDocumentRepo
    from kongming.rag.documents_service import DocumentConflict, upload_document
    from kongming.rag.entity_extract import build_doc_entity_extractor

    try:
        return await upload_document(
            SqlDocumentRepo(session, ts_config=get_settings().rag_fulltext_ts_config),
            get_agent_rag_client(get_redis()),
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            doc_name=doc_name,
            text=text,
            entity_extractor=build_doc_entity_extractor(),
            sentence_boundary=get_settings().doc_chunk_sentence_boundary,
        )
    except DocEmpty:
        raise HTTPException(status_code=400, detail="文档内容为空") from None
    except TooManyChunks as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except DocumentConflict:
        raise HTTPException(status_code=409, detail="同名文档并发上传冲突,请重试") from None


@doc_router.post("/upload", response_model=DocUploadOut)
async def upload_doc(
    file: Annotated[UploadFile, File()],
    datasource_key: Annotated[str, Form(pattern=r"^[a-z][a-z0-9_]{1,63}$")],
    user: Annotated[dict[str, object], Depends(current_user)],
    _: Annotated[None, Depends(require_perm("system:document:manage"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> DocUploadOut:
    """文档上传:类型/大小沿用 分档;分块+embedding(子块)+覆盖落库。"""
    svc = get_rag_service_dep(session)
    tenant_id = await _doc_tenant(session, svc, str(user["sub"]), x_tenant_id)
    await _assert_doc_datasource(session, tenant_id=tenant_id, datasource_key=datasource_key)
    doc_name, content = await _read_doc_upload(file)
    try:
        text_content = await asyncio.to_thread(parse_to_text, content, file.filename or "")
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="文档解析失败") from None
    doc_id, blocks, skipped = await _ingest_doc_upload(
        session,
        tenant_id=tenant_id,
        datasource_key=datasource_key,
        doc_name=doc_name,
        text=text_content,
    )
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

    svc = get_rag_service_dep(session)
    tid = await _doc_tenant(session, svc, str(user["sub"]), x_tenant_id)
    ok = await SqlDocumentRepo(
        session, ts_config=get_settings().rag_fulltext_ts_config
    ).delete_document(doc_id=doc_id, tenant_id=tid)
    if not ok:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    await session.commit()


@doc_router.post("/documents/{doc_id}/enrich", response_model=DocEnrichOut)
async def enrich_doc(
    doc_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    _: Annotated[None, Depends(require_perm("system:document:manage"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> DocEnrichOut:
    """既有文档实体覆盖式补抽(先清后抽;免重传文件)。

    部署未启用实体抽取时 400 DOC_ENTITY_DISABLED(与能力未开启不可区分
    的显式拒绝);抽取失败降级无实体(200 entities=0),不报错。"""
    from kongming.rag.documents_repo import SqlDocumentRepo
    from kongming.rag.documents_service import DocNotFound, enrich_document
    from kongming.rag.entity_extract import build_doc_entity_extractor

    extractor = build_doc_entity_extractor()
    if extractor is None:
        raise HTTPException(status_code=400, detail="DOC_ENTITY_DISABLED") from None
    svc = get_rag_service_dep(session)
    tid = await _doc_tenant(session, svc, str(user["sub"]), x_tenant_id)
    try:
        n = await enrich_document(
            SqlDocumentRepo(session, ts_config=get_settings().rag_fulltext_ts_config),
            extractor,
            tenant_id=tid,
            doc_id=doc_id,
        )
    except DocNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return DocEnrichOut(doc_id=doc_id, entities=n)


@doc_router.get("/retrieval-probe", response_model=RetrievalProbeOut)
async def retrieval_probe(
    user: Annotated[dict[str, object], Depends(current_user)],
    _: Annotated[None, Depends(require_perm("system:document:manage"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    question: Annotated[str, Query(min_length=1, max_length=2000)],
    datasource_key: Annotated[str, Query(pattern=r"^[a-z][a-z0-9_]{1,63}$")] = "default",
    parent_top_k: Annotated[int, Query(ge=1, le=20)] = 5,
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> RetrievalProbeOut:
    """文档检索试跑(只读零副作用):逐路命中/融合名次/父块回填,与主检索同源。"""
    from kongming.rag.documents_repo import SqlDocumentRepo
    from kongming.rag.documents_service import probe_docs
    from kongming.rag.entity_extract import build_doc_entity_extractor
    from kongming.rag.rerank import build_doc_reranker

    svc = get_rag_service_dep(session)
    tid = await _doc_tenant(session, svc, str(user["sub"]), x_tenant_id)
    await _assert_doc_datasource(session, tenant_id=tid, datasource_key=datasource_key)
    settings = get_settings()
    out = await probe_docs(
        SqlDocumentRepo(session, ts_config=settings.rag_fulltext_ts_config),
        get_agent_rag_client(get_redis()),
        tenant_id=tid,
        datasource_key=datasource_key,
        question=question,
        reranker=build_doc_reranker(),
        parent_top_k=parent_top_k,
        entity_extractor=build_doc_entity_extractor(),
        entity_quota=settings.doc_entity_query_max,
    )
    return RetrievalProbeOut(**out)


@doc_router.get("/documents/{doc_id}/chunks", response_model=list[DocumentChunkOut])
async def list_doc_chunks(
    doc_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    _: Annotated[None, Depends(require_perm("system:document:manage"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> list[DocumentChunkOut]:
    from kongming.rag.documents_repo import SqlDocumentRepo

    svc = get_rag_service_dep(session)
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
