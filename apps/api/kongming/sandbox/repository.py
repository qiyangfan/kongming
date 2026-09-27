"""sandbox 审计存储(027):独立短事务(与 019 AuditWriter 同款——
get_sessionmaker 独立会话,不占请求事务,失败仅日志返回 None)。"""

import logging

from sqlalchemy import text

from kongming.db import get_sessionmaker
from kongming.sandbox.models import SandboxOutcome
from kongming.uuid7 import uuid7

log = logging.getLogger(__name__)

_TAIL = 2000


class SqlSandboxRunRepo:
    """sys_sandbox_run 写入(027)。零框架纪律:本文件属基础设施层。"""

    async def record(
        self,
        *,
        trace_id: str,
        user_id: str,
        tenant_id: str,
        code: str,
        outcome: SandboxOutcome,
    ) -> str | None:
        row_id = str(uuid7())  # ADR-004:UUID v7 主键
        try:
            async with get_sessionmaker()() as s:
                await s.execute(
                    text(
                        "INSERT INTO public.sys_sandbox_run"
                        " (id, tenant_id, user_id, trace_id, code_text,"
                        "  data_rows, data_cols, stdout_tail, result_tail,"
                        "  images_count, duration_ms, exit_code, status, error_tail)"
                        " VALUES (:id, CAST(:tenant_id AS uuid), CAST(:user_id AS uuid),"
                        "         :trace_id, :code, :drows, :dcols, :stdout, :result,"
                        "         :imgs, :dur, :exit, :status, :error)"
                    ),
                    {
                        "id": row_id,
                        "tenant_id": tenant_id,
                        "user_id": user_id,
                        "trace_id": trace_id[:64],
                        "code": code[:8000],
                        "drows": outcome.data_rows,
                        "dcols": outcome.data_cols,
                        "stdout": outcome.stdout[-_TAIL:],
                        "result": (outcome.result_preview or "")[-_TAIL:],
                        "imgs": len(outcome.images),
                        "dur": outcome.duration_ms,
                        "exit": outcome.exit_code,
                        "status": outcome.status,
                        "error": (outcome.error or "")[-_TAIL:],
                    },
                )
                await s.commit()
            return row_id
        except Exception:
            # 审计失败不阻断主链路(与 019 同款);仅日志
            log.warning("sys_sandbox_run 落库失败(不阻断)", exc_info=True)
            return None
