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
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.router import current_user, require_perm
from kongming.config import get_settings
from kongming.datasource.models import PG_IDENTIFIER_PATTERN
from kongming.db import get_session
from kongming.rag.agent_callback import AgentRagClient
from kongming.rag.cache import ContextCache
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


def _get_service(session: Annotated[AsyncSession, Depends(get_session)]) -> RagService:
    client = AgentRagClient(get_redis())
    return RagService(
        RagRepository(session),
        embedder=client,
        describer=client,
        cache=ContextCache(get_redis()),
    )


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
        tenant_id, _ = await svc.resolve_tenant(str(user["sub"]), x_tenant_id)
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
        tenant_id, _ = await svc.resolve_tenant(str(user["sub"]), x_tenant_id)
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
            )
            for e in entries
        ],
        total=total,
        page=page,
        size=size,
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
        tenant_id, _ = await svc.resolve_tenant(str(user["sub"]), x_tenant_id)
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


@internal_router.post("/retrieve", response_model=RetrieveOut)
async def rag_retrieve(
    req: RetrieveIn, svc: Annotated[RagService, Depends(_get_service)]
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
    return RetrieveOut(schema_hint=ctx.schema_hint, history_sql=ctx.history_sql)
