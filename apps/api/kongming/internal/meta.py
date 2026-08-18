"""内部元数据通道(internal-meta spec):租户 schema 实时内省。

供 agent retrieve 节点消费(RAG 落地前的 schema 提示过渡来源);
内省内容只进 LLM 提示文本,不构成任何执行放行依据。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.config import get_settings
from kongming.db import get_session
from kongming.sqlfw.firewall import RESERVED_TABLE_NAMES, RESERVED_TABLE_PREFIXES

router = APIRouter(prefix="/internal", tags=["internal"])


def _is_reserved(table_name: str) -> bool:
    """控制面保留表不进内省结果(005 评审:sys_user 等喂给 LLM = 凭证探测直通车)。"""
    name = table_name.lower()
    return name.startswith(RESERVED_TABLE_PREFIXES) or name in RESERVED_TABLE_NAMES


class MetaSchemaIn(BaseModel):
    tenant_key: str = Field(min_length=1, max_length=64)
    datasource_key: str = "default"


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
    if req.datasource_key != "default":
        # 动态数据源内省随数据源变更(与执行路由同一 fail-closed)
        raise HTTPException(status_code=400, detail="UNSUPPORTED_DATASOURCE")

    schemas = [f"tenant_{req.tenant_key}", "public"]
    table_rows = (
        await session.execute(
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
        await session.execute(
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
