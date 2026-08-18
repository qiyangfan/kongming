"""内部受控通道(ADR-002):agent 进程回调的唯一入口。

设计约束:
- Agent 无凭证,SQL 执行 100% 收口于此;
- 纵深防御:内部令牌(middleware)→ 租户解析/绑定复校 → SQL 防火墙 →
  行级 data_scope → 防火墙复检 → 只读连接池(事务级 search_path);
- 全量落 sys_sql_audit(trace_id 贯穿,成功/拦截/失败三类)。
"""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.db import get_session
from kongming.internal import service

router = APIRouter(prefix="/internal", tags=["internal"])


class SqlExecuteRequest(BaseModel):
    trace_id: str = Field(min_length=1, max_length=64)  # 与 sys_sql_audit.trace_id 同宽
    tenant_key: str = Field(min_length=1, max_length=64)
    datasource_key: str = "default"
    sql_text: str = Field(min_length=1)
    user_id: uuid.UUID | None = None
    question: str | None = None
    limit: int = Field(default=1000, ge=1, le=10000)


class SqlExecuteResponse(BaseModel):
    verdict: Literal["PASS", "BLOCKED", "ERROR"]
    reason: str | None = None
    columns: list[str]
    rows: list[list[object]]
    row_count: int
    duration_ms: int


@router.post("/sql/execute", response_model=SqlExecuteResponse)
async def sql_execute(
    req: SqlExecuteRequest, session: Annotated[AsyncSession, Depends(get_session)]
) -> SqlExecuteResponse:
    try:
        result = await service.execute(
            session,
            trace_id=req.trace_id,
            tenant_key=req.tenant_key,
            datasource_key=req.datasource_key,
            sql_text=req.sql_text,
            user_id=None if req.user_id is None else str(req.user_id),
            question=req.question,
            limit=req.limit,
        )
    except service.TenantNotFound:
        raise HTTPException(status_code=404, detail="TENANT_NOT_FOUND") from None
    except service.TenantBindingForbidden:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    except service.UnsupportedDatasource:
        raise HTTPException(status_code=400, detail="UNSUPPORTED_DATASOURCE") from None
    return SqlExecuteResponse(
        verdict=result.verdict,  # type: ignore[arg-type]  # 三态由 service 契约保证
        reason=result.reason,
        columns=result.columns,
        rows=result.rows,
        row_count=result.row_count,
        duration_ms=result.duration_ms,
    )
