"""semantic 路由(023,semantic-layer spec):关系/指标管理 + introspection 触发。

全部端点 system:semantic:manage;tenant 头复校(chat 同构);datasource
归属校验经 008 resolver(同形 400 防探测)。
"""

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.router import current_user, require_perm
from kongming.db import get_session
from kongming.semantic.repository import SqlSemanticRepo
from kongming.semantic.service import RelationTableUnknown, SemanticService

log = logging.getLogger(__name__)

router = APIRouter(prefix="/system/semantic", tags=["semantic"])

_PG_NAME = r"^[a-zA-Z_][a-zA-Z0-9_]*$"  # PG 标识符(表名段)
# 040:手工关系表引用须两段限定名(schema.table)——知识条目与内省关系恒两段,
# 单段在存在性闸下必拒(静默死配置),语法层即拒语义更准(422 早失败于 400)
_TABLE_REF = r"^[a-zA-Z_][a-zA-Z0-9_]*\.[a-zA-Z_][a-zA-Z0-9_]*$"


class _RagKnowledgeLookup:
    """KnowledgeTableLookup 绑定(040):实现=rag 知识仓储存在性查询。"""

    def __init__(self, session: AsyncSession) -> None:
        from kongming.rag.repository import RagRepository

        self._repo = RagRepository(session)

    async def knowledge_table_exists(
        self, *, tenant_id: str, datasource_key: str, schema_table: str
    ) -> bool:
        return await self._repo.knowledge_table_exists(
            tenant_id=tenant_id, datasource_key=datasource_key, schema_table=schema_table
        )


async def _tenant_id(
    session: AsyncSession, user_id: str, tenant_key: str | None
) -> str:
    """与 memory/chat 同构的租户解析(ACTIVE+default 优先+tiebreak)。
    SQL 单一真理源=kongming.tenant.resolve_tenant(B1-B10 收口)。"""
    from kongming.tenant import resolve_tenant

    resolved = await resolve_tenant(session, user_id=user_id, tenant_key=tenant_key)
    if resolved is None:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    return resolved[0]


async def _assert_datasource_usable(
    session: AsyncSession, *, tenant_id: str, datasource_key: str
) -> None:
    """R1-H2:非 default 源校验存在+启用+归属(008 同形 400 防探测);
    default 恒可用(平台配置)。"""
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


class MetricIn(BaseModel):
    datasource_key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    metric_name: str = Field(min_length=1, max_length=128)
    sql_template: str = Field(min_length=1, max_length=4000)
    business_terms: list[str] = Field(default_factory=list, max_length=16)
    enabled: bool = True


class _DefaultIntrospector:
    """default 源:控制面会话直查(目标 schema 集 tenant_<key>+public)。"""
    # 023 v1 仅支持 default 源的 introspection;动态源 introspection 随
    # 真实外部 PG 数据源接入评估(design 挂账)
    def __init__(self, session: AsyncSession, tenant_key: str) -> None:
        self._s = session
        self._schemas = [f"tenant_{tenant_key}", "public"]

    async def fetch_fk_pairs(self, *, schemas: list[str]) -> list[dict[str, object]]:
        rows = (
            await self._s.execute(
                text(
                    "SELECT fk.table_schema, fk.table_name, fk.column_name,"
                    "       pk.table_schema AS f_schema, pk.table_name AS f_table,"
                    "       pk.column_name AS f_col, fk.constraint_name,"
                    "       fk.ordinal_position"
                    " FROM information_schema.table_constraints tc"
                    " JOIN information_schema.key_column_usage fk"
                    "   ON tc.constraint_name=fk.constraint_name"
                    "  AND tc.table_schema=fk.table_schema"
                    " JOIN information_schema.referential_constraints rc"
                    "   ON tc.constraint_name=rc.constraint_name"
                    "  AND tc.constraint_schema=rc.constraint_schema"
                    " JOIN information_schema.key_column_usage pk"
                    "   ON rc.unique_constraint_name=pk.constraint_name"
                    "  AND rc.unique_constraint_schema=pk.constraint_schema"
                    # R1-H1:列序配对(多列 FK 不笛卡尔积):被引用侧序号=
                    # 引用侧的 position_in_unique_constraint
                    "  AND pk.ordinal_position = fk.position_in_unique_constraint"
                    " WHERE tc.constraint_type='FOREIGN KEY'"
                    "   AND fk.table_schema = ANY(:ss)"
                ),
                {"ss": self._schemas},
            )
        ).mappings().all()
        return [
            {
                "source_table": f"{r['table_schema']}.{r['table_name']}",
                "target_table": f"{r['f_schema']}.{r['f_table']}",
                "left_col": str(r["column_name"]),
                "right_col": str(r["f_col"]),
            }
            for r in rows
        ]


@router.post("/relations/introspect", response_model=IntrospectOut)
async def introspect_relations(
    req: IntrospectIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    _: Annotated[None, Depends(require_perm("system:semantic:manage"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> IntrospectOut:
    tid = await _tenant_id(session, str(user["sub"]), x_tenant_id)
    # R1-H2:v1 仅支持 default 源 introspection(动态源张冠李戴防护;
    # 动态源内省随真实外部 PG 源接入评估)——同形 400 防探测
    if req.datasource_key != "default":
        raise HTTPException(status_code=400, detail="DATASOURCE_INVALID") from None
    # R1-L1:schema 集按解析出的租户反查(不直接用请求头,防无头时错配)
    tkey = (
        await session.execute(
            text("SELECT tenant_key FROM public.sys_tenant WHERE id=:i"), {"i": tid}
        )
    ).scalar_one()
    svc = SemanticService(SqlSemanticRepo(session))
    n = await svc.introspect(
        tenant_id=tid,
        datasource_key=req.datasource_key,
        introspector=_DefaultIntrospector(session, str(tkey)),
    )
    return IntrospectOut(relations=n)


@router.get("/relations", response_model=list[RelationOut])
async def list_relations(
    user: Annotated[dict[str, object], Depends(current_user)],
    _: Annotated[None, Depends(require_perm("system:semantic:manage"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    datasource_key: str = "default",
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> list[RelationOut]:
    tid = await _tenant_id(session, str(user["sub"]), x_tenant_id)
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
    user: Annotated[dict[str, object], Depends(current_user)],
    _: Annotated[None, Depends(require_perm("system:semantic:manage"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    tid = await _tenant_id(session, str(user["sub"]), x_tenant_id)
    await _assert_datasource_usable(
        session, tenant_id=tid, datasource_key=req.datasource_key
    )
    svc = SemanticService(SqlSemanticRepo(session), _RagKnowledgeLookup(session))
    try:
        row_id = await svc.add_relation(
            tenant_id=tid,
            datasource_key=req.datasource_key,
            source_table=req.source_table,
            target_table=req.target_table,
            join_keys=req.join_keys,
        )
    except RelationTableUnknown:
        raise HTTPException(
            status_code=400, detail="RELATION_TABLE_UNKNOWN"
        ) from None
    return {"id": row_id}


@router.delete("/relations/{relation_id}", status_code=204)
async def delete_relation(
    relation_id: uuid.UUID,
    user: Annotated[dict[str, object], Depends(current_user)],
    _: Annotated[None, Depends(require_perm("system:semantic:manage"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> None:
    tid = await _tenant_id(session, str(user["sub"]), x_tenant_id)
    ok = await SqlSemanticRepo(session).delete_relation(
        relation_id=str(relation_id), tenant_id=tid
    )
    if not ok:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    await session.commit()


@router.get("/metrics", response_model=list[MetricOut])
async def list_metrics(
    user: Annotated[dict[str, object], Depends(current_user)],
    _: Annotated[None, Depends(require_perm("system:semantic:manage"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    datasource_key: str = "default",
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> list[MetricOut]:
    tid = await _tenant_id(session, str(user["sub"]), x_tenant_id)
    rows = await SqlSemanticRepo(session).list_metrics(
        tenant_id=tid, datasource_key=datasource_key
    )
    return [
        MetricOut(
            id=str(r["id"]),
            metric_name=str(r["metric_name"]),
            sql_template=str(r["sql_template"]),
            business_terms=[str(t) for t in r["business_terms"] or []],
            enabled=bool(r["enabled"]),
            updated_at=str(r["updated_at"]),
        )
        for r in rows
    ]


@router.post("/metrics", status_code=201)
async def upsert_metric(
    req: MetricIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    _: Annotated[None, Depends(require_perm("system:semantic:manage"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    tid = await _tenant_id(session, str(user["sub"]), x_tenant_id)
    await _assert_datasource_usable(
        session, tenant_id=tid, datasource_key=req.datasource_key
    )
    row_id = await SqlSemanticRepo(session).upsert_metric(
        tenant_id=tid,
        datasource_key=req.datasource_key,
        metric={
            "metric_name": req.metric_name,
            "sql_template": req.sql_template,
            "business_terms": req.business_terms,
            "enabled": req.enabled,
        },
    )
    await session.commit()
    return {"id": row_id}


@router.delete("/metrics/{metric_id}", status_code=204)
async def delete_metric(
    metric_id: uuid.UUID,
    user: Annotated[dict[str, object], Depends(current_user)],
    _: Annotated[None, Depends(require_perm("system:semantic:manage"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> None:
    tid = await _tenant_id(session, str(user["sub"]), x_tenant_id)
    ok = await SqlSemanticRepo(session).delete_metric(metric_id=str(metric_id), tenant_id=tid)
    if not ok:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    await session.commit()
