"""rag 路由(009):知识库管理端点(/system/rag,租户视角+system:rag:manage)
与内部召回端点(/internal/rag/retrieve——api 全局内部令牌 middleware 守门)。

上传链路:multipart(file+datasource_key+schema_name)→ 文本类直读/PDF·Word
线程内解析 → Markdown `## 表名` 切块 → service 落库。
"""

import asyncio
import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.router import current_user, require_perm
from kongming.config import get_settings
from kongming.datasource.models import PG_IDENTIFIER_PATTERN
from kongming.db import get_session
from kongming.rag.agent_callback import get_agent_rag_client
from kongming.rag.cache import ContextCache
from kongming.rag.chunker import EmptyDocument as DocEmpty
from kongming.rag.chunker import TooManyChunks
from kongming.rag.docparse import parse_to_text, supported_extension
from kongming.rag.models import (
    DatasourceInvalid,
    EmptyDocument,
    InvalidSchemaName,
    TableBlock,
    TableNameTooLong,
    TooManyBlocks,
    UnsupportedFileType,
)
from kongming.rag.parser import parse_markdown
from kongming.rag.repository import RagRepository
from kongming.rag.service import RagService
from kongming.rag.service import TenantForbidden as TenantForbiddenApi
from kongming.redis_client import get_redis

log = logging.getLogger(__name__)

router = APIRouter(prefix="/system/rag", tags=["rag"])
internal_router = APIRouter(prefix="/internal/rag", tags=["internal"])

MAX_TABLE_BLOCKS = 500  # 单文档表块上限(防大文档切块风暴,design D7)


def get_rag_service(session: AsyncSession) -> RagService:
    """RagService 公共组合根(019 toolcall 复用同源;013 单例工厂同调)。"""
    client = get_agent_rag_client(get_redis())  # 013:进程内单例,连接池跨请求复用
    return RagService(
        RagRepository(session),
        embedder=client,
        describer=client,
        cache=ContextCache(get_redis()),
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
) -> UploadOut:
    # 校验序:租户白名单复校先于文件读取(未绑定租户者不消耗解析资源;复审 R5)
    try:
        resolved2 = await svc.resolve_tenant(str(user["sub"]), x_tenant_id)
        tenant_id = resolved2[0]
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
            datasource_key=datasource_key,
            schema_name=schema_name,
            blocks=blocks,
        )
    except TenantForbiddenApi:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    except DatasourceInvalid:
        raise HTTPException(status_code=400, detail="DATASOURCE_INVALID") from None
    return UploadOut(tables=tables)


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


class KnowledgePageOut(BaseModel):
    items: list[KnowledgeItemOut]
    total: int
    page: int
    size: int


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
    )
    return KnowledgePageOut(
        items=[
            KnowledgeItemOut(
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
            )
            for e in entries
        ],
        total=total,
        page=page,
        size=size,
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
    result = await svc.edit_table(
        actor_id=str(user["sub"]),
        tenant_id=tenant_id,
        table_id=table_id,
        table_comment=body.table_comment,
        alias=body.alias,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return KnowledgeItemOut(
        id=result.id,
        datasource_key=result.datasource_key,
        schema_name=result.schema_name,
        table_name=result.table_name,
        table_comment=result.table_comment,
        columns_comment=result.columns_comment,
        sample_queries=result.sample_queries,
        source_type=result.source_type,
        updated_at=result.updated_at,  # type: ignore[arg-type]
        alias=result.alias,
    )


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
    deleted = await svc.delete_table(
        actor_id=str(user["sub"]), tenant_id=tenant_id, table_id=table_id
    )
    if deleted is None:
        raise HTTPException(status_code=404, detail="KNOWLEDGE_NOT_FOUND") from None


# ============ fetch-metadata / reindex ============


class DatasourceActionIn(BaseModel):
    datasource_key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")


class FetchOut(BaseModel):
    found: int
    enriched: int
    skipped: int


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
    return FetchOut(found=result.found, enriched=result.enriched, skipped=result.skipped)


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
    return ReindexOut(
        deleted=result.deleted, rebuilt=result.rebuilt, enriched=result.enriched
    )


# ============ 内部召回端点(agent 回调;/internal 前缀由全局 middleware 令牌守门) ============


class RetrieveIn(BaseModel):
    tenant_key: str = Field(min_length=1, max_length=64)
    datasource_key: str = Field(default="default", pattern=r"^[a-z][a-z0-9_]{1,63}$")
    question: str = Field(min_length=1, max_length=2000)


class RetrieveOut(BaseModel):
    schema_hint: str
    history_sql: str
    # 023 语义段(纯增;空串=无注入,agent 零回归)
    relations_hint: str = ""
    metric_hints: str = ""
    # 024 文档段(纯增;空=无命中,双通道隔离)
    doc_hints: list[dict[str, str]] = []


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
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="TENANT_NOT_FOUND") from None
    except DatasourceInvalid:
        raise HTTPException(status_code=400, detail="DATASOURCE_INVALID") from None
    # 023:语义段组装(召回表集合收窄;失败降级空串不影响召回主链路)
    relations_hint = ""
    metric_hints = ""
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
            relations_hint, metric_hints = await SemanticService(
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
                SqlDocumentRepo(session),
                get_agent_rag_client(get_redis()),
                tenant_id=str(tid2),
                datasource_key=req.datasource_key,
                question=req.question,
            )
    except Exception:
        log.warning("文档检索组装失败(降级空)", exc_info=True)
    return RetrieveOut(
        schema_hint=ctx.schema_hint,
        history_sql=ctx.history_sql,
        relations_hint=relations_hint,
        metric_hints=metric_hints,
        doc_hints=doc_hints,
    )


# ============ 024 document-rag:文档管理端点 ============

doc_router = APIRouter(prefix="/system/document", tags=["document"])


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
        await session.execute(
            text(
                "SELECT status, tenant_id FROM public.sys_datasource"
                " WHERE datasource_key = :k"
            ),
            {"k": datasource_key},
        )
    ).mappings().one_or_none()
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
    rows = await SqlDocumentRepo(session).list_documents(
        tenant_id=tid, datasource_key=datasource_key
    )
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
    await _assert_doc_datasource(
        session, tenant_id=tenant_id, datasource_key=datasource_key
    )
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
    try:
        text_content = await asyncio.to_thread(parse_to_text, content, file.filename or "")
    except Exception:
        raise HTTPException(status_code=400, detail="文档解析失败") from None
    try:
        doc_id, blocks, skipped = await upload_document(
            SqlDocumentRepo(session),
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
    ok = await SqlDocumentRepo(session).delete_document(doc_id=doc_id, tenant_id=tid)
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
    rows = await SqlDocumentRepo(session).list_chunks(doc_id=doc_id, tenant_id=tid)
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
