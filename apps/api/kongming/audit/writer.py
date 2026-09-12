"""audit 仓储:sys_sql_audit 写入(独立短事务,失败不阻断主流程)。

审计完整性铁律(audit spec):成功/失败/被拦截三类都落库;
写入自身失败仅 error 日志暴露,绝不影响查询响应。
"""

import logging

from sqlalchemy import text

from kongming.audit.models import SqlAuditEntry
from kongming.db import get_sessionmaker

log = logging.getLogger(__name__)

_TRACE_ID_MAX = 64  # sys_sql_audit.trace_id VARCHAR(64)
_REASON_MAX = 200  # block_reason VARCHAR(200)


class SqlAuditWriter:
    async def record(self, entry: SqlAuditEntry) -> None:
        trace_id = entry.trace_id
        if len(trace_id) > _TRACE_ID_MAX:
            log.warning("trace_id 超长截断落库: %s", trace_id)
            trace_id = trace_id[:_TRACE_ID_MAX]
        reason = entry.block_reason[:_REASON_MAX] if entry.block_reason else None
        try:
            async with get_sessionmaker()() as s:
                await s.execute(
                    text(
                        "INSERT INTO public.sys_sql_audit"
                        " (id, trace_id, user_id, tenant_id, datasource_key, question,"
                        "  sql_text, verdict, block_reason, row_count, duration_ms)"
                        " VALUES (gen_random_uuid(), :trace_id, :user_id, :tenant_id,"
                        "         :datasource_key, :question, :sql_text, :verdict,"
                        "         :block_reason, :row_count, :duration_ms)"
                    ),
                    {
                        "trace_id": trace_id,
                        "user_id": entry.user_id,
                        "tenant_id": entry.tenant_id,
                        "datasource_key": entry.datasource_key,
                        "question": entry.question,
                        "sql_text": entry.sql_text,
                        "verdict": entry.verdict,
                        "block_reason": reason,
                        "row_count": entry.row_count,
                        "duration_ms": entry.duration_ms,
                    },
                )
                await s.commit()
        except Exception:
            # 丢审计是严重事故:error 级暴露,但不阻断主流程(spec 立约)
            log.error("SQL 审计写入失败 trace_id=%s", trace_id, exc_info=True)
