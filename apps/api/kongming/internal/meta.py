"""内部元数据通道(internal-meta spec):租户 schema 实时内省。

供 agent retrieve 节点消费(RAG 落地前的 schema 提示过渡来源);
内省内容只进 LLM 提示文本,不构成任何执行放行依据。
008:非 default 数据源经 DatasourceResolver 同构校验(存在+启用+归属,同形 400),
限其 default_schema 单 schema 内省,走动态池连接(不越出目标库)。
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from kongming.config import get_settings
from kongming.datasource.resolver import DatasourceInvalid, resolve_for_read
from kongming.db import get_session
from kongming.internal.executor import get_or_create_engine
from kongming.sqlfw.firewall import RESERVED_TABLE_NAMES, RESERVED_TABLE_PREFIXES

router = APIRouter(prefix="/internal", tags=["internal"])

log = logging.getLogger(__name__)


def _is_reserved(table_name: str) -> bool:
    """控制面保留表不进内省结果(005 评审:sys_user 等喂给 LLM = 凭证探测直通车)。"""
    name = table_name.lower()
    return name.startswith(RESERVED_TABLE_PREFIXES) or name in RESERVED_TABLE_NAMES


class MetaSchemaIn(BaseModel):
    tenant_key: str = Field(min_length=1, max_length=64)
    # 查询面 pattern 与注册边界同一规则(008 D4 口径一致性收口)
    datasource_key: str = Field(default="default", pattern=r"^[a-z][a-z0-9_]{1,63}$")


class MetaColumnOut(BaseModel):
    name: str
    data_type: str
    comment: str = ""


class MetaTableOut(BaseModel):
    # schema 与 BaseModel 方法同名,用别名规避遮蔽(序列化仍输出 schema 键)
    model_config = ConfigDict(populate_by_name=True)

    schema_: str = Field(alias="schema")
    name: str
    comment: str = ""
    columns: list[MetaColumnOut]


class MetaSchemaOut(BaseModel):
    tables: list[MetaTableOut]
    truncated: bool


async def _introspect(
    conn: AsyncSession | AsyncConnection, schemas: list[str]
) -> MetaSchemaOut:
    """按 schema 集内省表/列/注释(保留表排除+上限截断;默认与动态路径共用)。"""
    table_rows = (
        await conn.execute(
            text(
                "SELECT table_schema, table_name FROM information_schema.tables"
                " WHERE table_schema = ANY(CAST(:schemas AS text[]))"
                " AND table_type IN ('BASE TABLE', 'VIEW')"
                " ORDER BY table_schema, table_name"
            ),
            {"schemas": schemas},
        )
    ).all()

    max_tables = get_settings().meta_schema_max_tables
    visible = [r for r in table_rows if not _is_reserved(r.table_name)]
    truncated = len(visible) > max_tables
    kept = {(r.table_schema, r.table_name) for r in visible[:max_tables]}

    col_rows = (
        await conn.execute(
            text(
                "SELECT c.table_schema, c.table_name, c.column_name, c.data_type,"
                " obj_description(cls.oid, 'pg_class') AS table_comment,"
                " col_description(cls.oid, c.ordinal_position) AS column_comment"
                " FROM information_schema.columns c"
                " JOIN pg_class cls ON cls.relname = c.table_name"
                " JOIN pg_namespace n ON n.oid = cls.relnamespace"
                "   AND n.nspname = c.table_schema"
                " WHERE c.table_schema = ANY(CAST(:schemas AS text[]))"
                " ORDER BY c.table_schema, c.table_name, c.ordinal_position"
            ),
            {"schemas": schemas},
        )
    ).all()

    tables: dict[tuple[str, str], MetaTableOut] = {}
    for r in col_rows:
        key = (r.table_schema, r.table_name)
        if key not in kept:
            continue
        table = tables.setdefault(
            key,
            MetaTableOut(
                schema=r.table_schema,
                name=r.table_name,
                comment=r.table_comment or "",
                columns=[],
            ),
        )
        table.columns.append(
            MetaColumnOut(
                name=r.column_name,
                data_type=r.data_type,
                comment=r.column_comment or "",
            )
        )
    # 无列表也要出现(只有表注释的空表)
    for schema, name in sorted(kept):
        tables.setdefault((schema, name), MetaTableOut(schema=schema, name=name, columns=[]))

    return MetaSchemaOut(
        tables=[tables[k] for k in sorted(tables)], truncated=truncated
    )


@router.post("/meta/schema", response_model=MetaSchemaOut)
async def meta_schema(
    req: MetaSchemaIn, session: Annotated[AsyncSession, Depends(get_session)]
) -> MetaSchemaOut:
    # 租户解析:与执行路由同一 fail-closed 语义(未知/禁用同形 404)
    tenant_id = (
        await session.execute(
            text(
                "SELECT id FROM public.sys_tenant WHERE tenant_key = :k AND status = 'ACTIVE'"
            ),
            {"k": req.tenant_key},
        )
    ).scalar_one_or_none()
    if tenant_id is None:
        raise HTTPException(status_code=404, detail="TENANT_NOT_FOUND")

    if req.datasource_key == "default":
        # 默认路径:控制面库本库内省(零回归)
        return await _introspect(session, [f"tenant_{req.tenant_key}", "public"])

    # 动态源:存在+启用+归属校验+解密(同形 400 防探测,与执行路由同构;
    # 009 抽 resolver 公共,fetch-metadata 复用)
    try:
        row, plain = await resolve_for_read(
            session, tenant_id=str(tenant_id), datasource_key=req.datasource_key
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
    # 限 default_schema 单 schema 内省(internal-meta spec 008 delta)
    async with engine.connect() as conn:
        return await _introspect(conn, [row.default_schema])
