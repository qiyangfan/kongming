"""数据源管理与选项查询路由。管理操作需要 `system:datasource:manage`,选项查询
登录即可并按请求租户范围过滤;领域错误在路由层映射为对应 HTTP 状态码。"""

import logging
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.deps import CurrentUser, require_perm
from kongming.config import get_settings
from kongming.datasource import service as ds_service
from kongming.datasource.cipher import get_cipher
from kongming.datasource.models import (
    DatasourceError,
    DatasourceKeyImmutable,
    DatasourceKeyTaken,
    DatasourceNotFound,
    DatasourceTenantImmutable,
)
from kongming.datasource.repository import SqlDatasourceRepo
from kongming.datasource.service import DatasourceService
from kongming.datasource.tester import AsyncpgConnectionTester
from kongming.db import get_session
from kongming.internal import executor

router = APIRouter(prefix="/system/datasource", tags=["datasource"])

logger = logging.getLogger(__name__)

MANAGE = "system:datasource:manage"


class _ExecutorPoolEvictor:
    """PoolEvictor 端口 → internal.executor 定向失效(router 装配层反转)。"""

    async def evict(self, datasource_key: str) -> None:
        await executor.evict_engine(datasource_key)


def get_pool_evictor() -> _ExecutorPoolEvictor:
    return _ExecutorPoolEvictor()


def get_datasource_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    evictor: Annotated[_ExecutorPoolEvictor, Depends(get_pool_evictor)],
) -> DatasourceService:
    return DatasourceService(
        SqlDatasourceRepo(session),
        get_cipher(get_settings().datasource_encrypt_key),
        evictor,
        AsyncpgConnectionTester(),
    )


Service = Annotated[DatasourceService, Depends(get_datasource_service)]

# ---- 传输模型 ----


class DatasourceCreateIn(BaseModel):
    # key/default_schema 的 pattern 与保留字校验在领域层(400 口径,spec 立约);
    # 传输层只做长度兜底
    datasource_key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    tenant_id: uuid.UUID  # 租户归属显式指定(不取 X-Tenant-Id 隐式上下文)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=5432, ge=1, le=65535)
    database: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=128)
    default_schema: str = Field(default="public", max_length=64)
    readonly: bool = True


class DatasourceUpdateIn(BaseModel):
    """全量更新数据源配置;key 和 tenant_id 不可修改,密码为空或未提交时保持原值。"""

    datasource_key: str = Field(min_length=1, max_length=64)
    tenant_id: uuid.UUID
    name: str = Field(min_length=1, max_length=128)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=5432, ge=1, le=65535)
    database: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=1, max_length=128)
    password: str | None = Field(default=None, min_length=1, max_length=128)
    default_schema: str = Field(default="public", max_length=64)
    readonly: bool = True


class TestConnectionIn(BaseModel):
    id: uuid.UUID | None = None  # 编辑态:密码留空时取库存密文补位
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=5432, ge=1, le=65535)
    database: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=1, max_length=128)
    password: str | None = Field(default=None, min_length=1, max_length=128)


class DatasourceItemOut(BaseModel):
    id: str
    datasource_key: str
    name: str
    tenant_id: str
    tenant_key: str
    tenant_name: str
    host: str
    port: int
    database: str
    username: str
    default_schema: str
    readonly: bool
    status: str
    created_at: datetime


class DatasourcePageOut(BaseModel):
    total: int
    items: list[DatasourceItemOut]


class DatasourceOptionOut(BaseModel):
    datasource_key: str
    name: str


class TestConnectionOut(BaseModel):
    ok: bool
    reason: str | None = None


# ---- 选项(登录即可的用户面接口;声明须先于 /{ds_id} 系路由) ----


@router.get("/options", response_model=list[DatasourceOptionOut])
async def list_options(
    user: CurrentUser,
    svc: Service,
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> list[DatasourceOptionOut]:
    try:
        opts = await svc.options(user_id=user.user_id, tenant_key=x_tenant_id)
    except ds_service.OptionsTenantForbidden:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    return [DatasourceOptionOut(datasource_key=o.datasource_key, name=o.name) for o in opts]


# ---- 管理端点(权限点 system:datasource:manage;全局视角) ----


@router.get("", response_model=DatasourcePageOut, dependencies=[Depends(require_perm(MANAGE))])
async def page_datasources(
    svc: Service,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 10,
    keyword: Annotated[str, Query(max_length=64)] = "",
) -> DatasourcePageOut:
    total, items = await svc.page(page=page, size=size, keyword=keyword)
    return DatasourcePageOut(
        total=total,
        items=[
            DatasourceItemOut(
                id=d.id,
                datasource_key=d.datasource_key,
                name=d.name,
                tenant_id=d.tenant_id,
                tenant_key=d.tenant_key,
                tenant_name=d.tenant_name,
                host=d.host,
                port=d.port,
                database=d.database,
                username=d.username,
                default_schema=d.default_schema,
                readonly=d.readonly,
                status=d.status,
                created_at=d.created_at,
            )
            for d in items
        ],
    )


@router.post("", status_code=201, dependencies=[Depends(require_perm(MANAGE))])
async def create_datasource(
    req: DatasourceCreateIn, user: CurrentUser, svc: Service
) -> dict[str, str]:
    ds_id = await svc.create(
        actor_id=user.user_id,
        datasource_key=req.datasource_key,
        name=req.name,
        tenant_id=str(req.tenant_id),
        host=req.host,
        port=req.port,
        database=req.database,
        username=req.username,
        password=req.password,
        default_schema=req.default_schema,
        readonly=req.readonly,
    )
    return {"id": ds_id}


@router.post(
    "/test", response_model=TestConnectionOut, dependencies=[Depends(require_perm(MANAGE))]
)
async def test_connection(req: TestConnectionIn, svc: Service) -> TestConnectionOut:
    result = await svc.test_connection(
        ds_id=None if req.id is None else str(req.id),
        host=req.host,
        port=req.port,
        database=req.database,
        username=req.username,
        password=req.password,
    )
    return TestConnectionOut(ok=result.ok, reason=result.reason)


@router.put("/{ds_id}", status_code=204, dependencies=[Depends(require_perm(MANAGE))])
async def update_datasource(
    ds_id: uuid.UUID,
    req: DatasourceUpdateIn,
    user: CurrentUser,
    svc: Service,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    await svc.update(
        actor_id=user.user_id,
        ds_id=str(ds_id),
        datasource_key=req.datasource_key,
        tenant_id=str(req.tenant_id),
        name=req.name,
        host=req.host,
        port=req.port,
        database=req.database,
        username=req.username,
        password=req.password,
        default_schema=req.default_schema,
        readonly=req.readonly,
        # 连接信息变更级联失效建议缓存(组合根绑定请求会话,同事务)
        suggestion_evictor=_SuggestionEvictor(session),
    )


@router.post("/{ds_id}/enable", status_code=204, dependencies=[Depends(require_perm(MANAGE))])
async def enable_datasource(ds_id: uuid.UUID, user: CurrentUser, svc: Service) -> None:
    await svc.set_status(actor_id=user.user_id, ds_id=str(ds_id), active=True)


@router.post("/{ds_id}/disable", status_code=204, dependencies=[Depends(require_perm(MANAGE))])
async def disable_datasource(ds_id: uuid.UUID, user: CurrentUser, svc: Service) -> None:
    await svc.set_status(actor_id=user.user_id, ds_id=str(ds_id), active=False)


@router.delete("/{ds_id}", status_code=204, dependencies=[Depends(require_perm(MANAGE))])
async def delete_datasource(
    ds_id: uuid.UUID,
    user: CurrentUser,
    svc: Service,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    # 删除后失效 RAG 缓存；失效失败只记录告警，不回滚删除。
    from kongming.rag.cache import ContextCache
    from kongming.redis_client import get_redis

    await svc.delete(
        actor_id=user.user_id,
        ds_id=str(ds_id),
        ctx_evictor=ContextCache(get_redis()).evict,
        # 语义层级联(与删除同事务;组合根绑定请求会话)
        semantic_cleaner=_SemanticCleanerAdapter().bind(session),
        # 建议缓存级联失效(与删除同事务,不留孤儿行)
        suggestion_evictor=_SuggestionEvictor(session),
    )


class _SemanticCleanerAdapter:
    """组合根:级联清理走请求会话(与删除同事务)。"""

    def __init__(self) -> None:
        self._session: AsyncSession | None = None

    def bind(self, session: AsyncSession) -> "_SemanticCleanerAdapter":
        self._session = session
        return self

    async def delete_by_datasource(self, *, tenant_id: str, datasource_key: str) -> None:
        from kongming.semantic.repository import SqlSemanticRepo

        if self._session is None:  # bind 未调用=组合根缺陷,fail-fast
            raise RuntimeError("SemanticCleanerAdapter 未绑定会话")
        await SqlSemanticRepo(self._session).delete_by_datasource(
            tenant_id=tenant_id, datasource_key=datasource_key
        )
        # 文档同域级联(chunks 经 FK CASCADE 随 documents 清)
        from kongming.rag.documents_repo import SqlDocumentRepo

        await SqlDocumentRepo(
            self._session, ts_config=get_settings().rag_fulltext_ts_config
        ).delete_by_datasource(tenant_id=tenant_id, datasource_key=datasource_key)


class _SuggestionEvictor:
    """组合根:建议缓存级联失效走请求会话(与变更/删除同事务)。"""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def evict_by_datasource(self, datasource_key: str) -> None:
        from kongming.chat.repository import SuggestionRepository

        await SuggestionRepository(self._s).delete_by_datasource(datasource_key)


# ---- 领域错误 → HTTP 映射 ----

_STATUS_BY_TYPE: tuple[tuple[type[DatasourceError], int], ...] = (
    (DatasourceNotFound, 404),
    (DatasourceKeyTaken, 409),
    (DatasourceKeyImmutable, 400),
    (DatasourceTenantImmutable, 400),
)


def register_datasource_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DatasourceError)
    async def _(_: Request, e: DatasourceError) -> JSONResponse:
        code = next((c for t, c in _STATUS_BY_TYPE if isinstance(e, t)), 400)
        return JSONResponse(status_code=code, content={"detail": str(e)})
