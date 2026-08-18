"""audit 领域模型:SQL 审计条目(与 sys_sql_audit 列同源)。"""

from dataclasses import dataclass
from typing import Literal

Verdict = Literal["PASS", "BLOCKED", "ERROR"]


@dataclass(frozen=True)
class SqlAuditEntry:
    trace_id: str
    user_id: str | None
    tenant_id: str
    datasource_key: str
    question: str | None
    sql_text: str
    verdict: Verdict
    block_reason: str | None = None
    row_count: int | None = None
    duration_ms: int | None = None
