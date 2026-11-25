"""semantic 路由(semantic-layer spec):关系/指标管理 + introspection 触发。

全部端点 system:semantic:manage;tenant 头复校(chat 同构);datasource
归属校验经 resolver(同形 400 防探测)。
"""

import logging
import uuid
from typing import TYPE_CHECKING, Annotated, cast

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.deps import CurrentUser, require_perm
from kongming.datasource.resolver import DatasourceInvalid, resolve_for_read
from kongming.db import get_session
from kongming.internal.executor import get_or_create_engine
from kongming.semantic.introspection import (
    ConnectionFkIntrospector,
    fetch_fk_pairs,
)
from kongming.semantic.repository import SqlSemanticRepo
from kongming.semantic.service import RelationTableUnknown, SemanticService

if TYPE_CHECKING:
    from kongming.rag.cache import ContextCache

log = logging.getLogger(__name__)

router = APIRouter(prefix="/system/semantic", tags=["semantic"])

_PG_NAME = r"^[a-zA-Z_][a-zA-Z0-9_]*$"  # PG 标识符(表名段)
# 手工关系表引用须两段限定名(schema.table)——知识条目与内省关系恒两段,
# 单段在存在性闸下必拒(静默死配置),语法层即拒语义更准(422 早失败于 400)
_TABLE_REF = r"^[a-zA-Z_][a-zA-Z0-9_]*\.[a-zA-Z_][a-zA-Z0-9_]*$"


class _RagKnowledgeLookup:
    """KnowledgeTableLookup 绑定:实现=rag 知识仓储存在性查询。"""

    def __init__(self, session: AsyncSession) -> None:
        from kongming.rag.repository import RagRepository

        self._repo = RagRepository(session)

    async def knowledge_table_exists(
        self, *, tenant_id: str, datasource_key: str, schema_table: str
    ) -> bool:
        return await self._repo.tenant.knowledge_table_exists(
            tenant_id=tenant_id, datasource_key=datasource_key, schema_table=schema_table
        )


async def _tenant_id(session: AsyncSession, user_id: str, tenant_key: str | None) -> str:
    """与 memory/chat 同构的租户解析(ACTIVE+default 优先+tiebreak)。
    SQL 单一真理源=kongming.tenant.resolve_tenant。"""
    from kongming.tenant import resolve_tenant

    resolved = await resolve_tenant(session, user_id=user_id, tenant_key=tenant_key)
    if resolved is None:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    return resolved[0]


async def _assert_datasource_usable(
    session: AsyncSession, *, tenant_id: str, datasource_key: str
) -> None:
    """非 default 源校验存在+启用+归属(同形 400 防探测);
    default 恒可用(平台配置)。"""
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


class RelationIn(BaseModel):
    datasource_key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    source_table: str = Field(min_length=1, max_length=128, pattern=_TABLE_REF)
    target_table: str = Field(min_length=1, max_length=128, pattern=_TABLE_REF)
    join_keys: list[dict[str, str]] = Field(min_length=1)


class IntrospectIn(BaseModel):
    datasource_key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")


class IntrospectOut(BaseModel):
    relations: int


class RelationOut(BaseModel):
    id: str
    source_table: str
    target_table: str
    join_keys: list[dict[str, str]]
    source: str
    updated_at: str


class MetricOut(BaseModel):
    id: str
    metric_name: str
    sql_template: str
    business_terms: list[str]
    enabled: bool
    updated_at: str
    visible_scope: str = "ALL"
    dept_id: str | None = None


class MetricIn(BaseModel):
    datasource_key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    metric_name: str = Field(min_length=1, max_length=128)
    sql_template: str = Field(min_length=1, max_length=4000)
    business_terms: list[str] = Field(default_factory=list, max_length=16)
    enabled: bool = True
    # 可见范围档位——DEPT 档必须带归属部门(文案级拒);ALL 档 dept_id 不携带
    visible_scope: str = Field(default="ALL", pattern=r"^(ALL|DEPT)$")
    dept_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _dept_required_for_dept_scope(self) -> "MetricIn":
        if self.visible_scope == "DEPT" and self.dept_id is None:
            raise ValueError("DEPT 可见范围必须选择归属部门")
        return self


class _DefaultIntrospector:
    """default 源:控制面会话直查(目标 schema 集 tenant_<key>+public)。"""

    def __init__(self, session: AsyncSession, tenant_key: str) -> None:
        self._s = session
        self._schemas = [f"tenant_{tenant_key}", "public"]

    async def fetch_fk_pairs(self, *, schemas: list[str]) -> list[dict[str, object]]:
        del schemas
        return await fetch_fk_pairs(self._s, schemas=self._schemas)


def _recall_ctx_evictor() -> "ContextCache":
    """将关系写入后的检索缓存失效端口绑定到 RAG ContextCache。"""
    from kongming.rag.cache import ContextCache
    from kongming.redis_client import get_redis

    return ContextCache(get_redis())


@router.post("/relations/introspect", response_model=IntrospectOut)
async def introspect_relations(
    req: IntrospectIn,
    user: CurrentUser,
    _: Annotated[None, Depends(require_perm("system:semantic:manage"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> IntrospectOut:
    tid = await _tenant_id(session, user.user_id, x_tenant_id)
    await _assert_datasource_usable(session, tenant_id=tid, datasource_key=req.datasource_key)
    svc = SemanticService(SqlSemanticRepo(session), recall_ctx_evictor=_recall_ctx_evictor())
    if req.datasource_key == "default":
        # schema 集按解析出的租户反查(不直接用请求头,防无头时错配)
        tkey = (
            await session.execute(
                text("SELECT tenant_key FROM public.sys_tenant WHERE id=:i"), {"i": tid}
            )
        ).scalar_one()
        n = await svc.introspect(
            tenant_id=tid,
            datasource_key=req.datasource_key,
            introspector=_DefaultIntrospector(session, str(tkey)),
        )
        return IntrospectOut(relations=n)

    try:
        row, plain = await resolve_for_read(
            session, tenant_id=tid, datasource_key=req.datasource_key
        )
    except DatasourceInvalid:
        raise HTTPException(status_code=400, detail="DATASOURCE_INVALID") from None
    engine = await get_or_create_engine(
        req.datasource_key,
        URL.create(
            "postgresql+asyncpg",
            username=row.username,
            password=plain,
            host=row.host,
            port=row.port,
            database=row.database,
        ),
    )
    async with engine.connect() as conn:
        n = await svc.introspect(
            tenant_id=tid,
            datasource_key=req.datasource_key,
            introspector=ConnectionFkIntrospector(conn, schemas=[row.default_schema]),
        )
    return IntrospectOut(relations=n)


@router.get("/relations", response_model=list[RelationOut])
async def list_relations(
    user: CurrentUser,
    _: Annotated[None, Depends(require_perm("system:semantic:manage"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    datasource_key: str = "default",
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> list[RelationOut]:
    tid = await _tenant_id(session, user.user_id, x_tenant_id)
    rows = await SqlSemanticRepo(session).list_relations(
        tenant_id=tid, datasource_key=datasource_key
    )
    return [
        RelationOut(
            id=str(r["id"]),
            source_table=str(r["source_table"]),
            target_table=str(r["target_table"]),
            join_keys=list(r["join_keys"]),
            source=str(r["source"]),
            updated_at=str(r["updated_at"]),
        )
        for r in rows
    ]


@router.post("/relations", status_code=201)
async def add_relation(
    req: RelationIn,
    user: CurrentUser,
    _: Annotated[None, Depends(require_perm("system:semantic:manage"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    tid = await _tenant_id(session, user.user_id, x_tenant_id)
    await _assert_datasource_usable(session, tenant_id=tid, datasource_key=req.datasource_key)
    svc = SemanticService(
        SqlSemanticRepo(session),
        _RagKnowledgeLookup(session),
        recall_ctx_evictor=_recall_ctx_evictor(),  # 提交后失效检索缓存
    )
    try:
        row_id = await svc.add_relation(
            tenant_id=tid,
            datasource_key=req.datasource_key,
            source_table=req.source_table,
            target_table=req.target_table,
            join_keys=req.join_keys,
        )
    except RelationTableUnknown:
        raise HTTPException(status_code=400, detail="RELATION_TABLE_UNKNOWN") from None
    return {"id": row_id}


@router.delete("/relations/{relation_id}", status_code=204)
async def delete_relation(
    relation_id: uuid.UUID,
    user: CurrentUser,
    _: Annotated[None, Depends(require_perm("system:semantic:manage"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> None:
    tid = await _tenant_id(session, user.user_id, x_tenant_id)
    # 删除用例入服务层(commit + 按被删行数据源精确失效检索缓存)
    ok = await SemanticService(
        SqlSemanticRepo(session), recall_ctx_evictor=_recall_ctx_evictor()
    ).delete_relation(relation_id=str(relation_id), tenant_id=tid)
    if not ok:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None


@router.get("/metrics", response_model=list[MetricOut])
async def list_metrics(
    user: CurrentUser,
    _: Annotated[None, Depends(require_perm("system:semantic:manage"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    datasource_key: str = "default",
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> list[MetricOut]:
    tid = await _tenant_id(session, user.user_id, x_tenant_id)
    rows = await SqlSemanticRepo(session).list_metrics(tenant_id=tid, datasource_key=datasource_key)
    return [
        MetricOut(
            id=str(r["id"]),
            metric_name=str(r["metric_name"]),
            sql_template=str(r["sql_template"]),
            business_terms=[str(t) for t in cast("list[object]", r["business_terms"] or [])],
            enabled=bool(r["enabled"]),
            updated_at=str(r["updated_at"]),
            visible_scope=str(r.get("visible_scope") or "ALL"),
            dept_id=None if r.get("dept_id") is None else str(r["dept_id"]),
        )
        for r in rows
    ]


@router.post("/metrics", status_code=201)
async def upsert_metric(
    req: MetricIn,
    user: CurrentUser,
    _: Annotated[None, Depends(require_perm("system:semantic:manage"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    tid = await _tenant_id(session, user.user_id, x_tenant_id)
    await _assert_datasource_usable(session, tenant_id=tid, datasource_key=req.datasource_key)
    dept_id: str | None = None
    if req.visible_scope == "DEPT" and req.dept_id is not None:
        # 归属部门存在性+同租户校验(键级校验在模型,租户匹配在端点——
        # 防跨租户 dept_id 借写)。部门停用不拒(同口径:启停是呈现标志)。
        row = (
            (
                await session.execute(
                    text("SELECT tenant_id FROM public.sys_dept WHERE id = :d"),
                    {"d": str(req.dept_id)},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None or str(row["tenant_id"]) != tid:
            raise HTTPException(status_code=400, detail="DEPT_INVALID") from None
        dept_id = str(req.dept_id)
    row_id = await SqlSemanticRepo(session).upsert_metric(
        tenant_id=tid,
        datasource_key=req.datasource_key,
        metric={
            "metric_name": req.metric_name,
            "sql_template": req.sql_template,
            "business_terms": req.business_terms,
            "enabled": req.enabled,
            "visible_scope": req.visible_scope,
            "dept_id": dept_id,
        },
    )
    await session.commit()
    return {"id": row_id}


@router.delete("/metrics/{metric_id}", status_code=204)
async def delete_metric(
    metric_id: uuid.UUID,
    user: CurrentUser,
    _: Annotated[None, Depends(require_perm("system:semantic:manage"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> None:
    tid = await _tenant_id(session, user.user_id, x_tenant_id)
    ok = await SqlSemanticRepo(session).delete_metric(metric_id=str(metric_id), tenant_id=tid)
    if not ok:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    await session.commit()
