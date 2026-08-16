"""内部受控通道(ADR-002):agent 进程回调的唯一入口。

设计约束:
- Agent 无凭证,SQL 执行 100% 收口于此;
- 每次调用走 SQL 防火墙 → (后续)租户校验 → 行级 data_scope → 只读账号执行;
- 全量落 sys_sql_audit(trace_id 贯穿)。
"""

from fastapi import APIRouter
from pydantic import BaseModel

from kongming.sqlfw.firewall import check

router = APIRouter(prefix="/internal", tags=["internal"])


class SqlExecuteRequest(BaseModel):
    trace_id: str
    tenant_id: str
    datasource_key: str = "default"
    sql_text: str
    user_id: str | None = None
    limit: int = 1000


class SqlExecuteResponse(BaseModel):
    verdict_passed: bool
    verdict_reason: str | None = None
    columns: list[str]
    rows: list[list[object]]
    duration_ms: int


@router.post("/sql/execute", response_model=SqlExecuteResponse)
async def sql_execute(req: SqlExecuteRequest) -> SqlExecuteResponse:
    verdict = check(req.sql_text)
    if not verdict.passed:
        # TODO(audit): 落 sys_sql_audit BLOCKED
        return SqlExecuteResponse(
            verdict_passed=False,
            verdict_reason=verdict.reason,
            columns=[],
            rows=[],
            duration_ms=0,
        )
    # TODO(datasource-exec): 按 datasource_key 路由只读连接池执行 + data_scope 注入 + 审计落库
    return SqlExecuteResponse(
        verdict_passed=True, verdict_reason=None, columns=[], rows=[], duration_ms=0
    )
