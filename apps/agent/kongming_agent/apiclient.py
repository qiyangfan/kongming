"""控制面回调客户端(ADR-002:agent 获取数据与执行 SQL 的唯一途径)。

纪律:
- 全部请求携带 X-Internal-Token;agent 不持有任何数据库凭证;
- HTTP 层错误(403/404/5xx/超时/断连)一律收敛为领域结果,不裸抛——
  execute 收敛为 verdict=ERROR(供 selfheal 喂回),meta 内省收敛为 None
  (retrieve 降级空提示,nl2sql-agent spec「Schema 智能选取」)。
"""

import logging
from dataclasses import dataclass, field
from typing import Literal

import httpx

from kongming_agent.settings import Settings

log = logging.getLogger(__name__)

Verdict = Literal["PASS", "BLOCKED", "ERROR"]


@dataclass(frozen=True)
class SqlExecuteOutcome:
    """/internal/sql/execute 的三态响应(HTTP 层异常已收敛为 ERROR)。"""

    verdict: Verdict
    reason: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[list[object]] = field(default_factory=list)
    duration_ms: int = 0


@dataclass(frozen=True)
class MetaColumn:
    name: str
    data_type: str
    comment: str


@dataclass(frozen=True)
class MetaTable:
    schema: str
    name: str
    comment: str
    columns: list[MetaColumn]


@dataclass(frozen=True)
class SchemaMeta:
    tables: list[MetaTable]
    truncated: bool


@dataclass(frozen=True)
class RagContext:
    """/internal/rag/retrieve 召回结果(009)。schema_hint 空=零命中(回退内省)。"""

    schema_hint: str
    history_sql: str = ""


class ApiClient:
    def __init__(self, settings: Settings) -> None:
        self._base = settings.api_base_url.rstrip("/")
        self._timeout = settings.api_timeout_sec
        self._headers = {"X-Internal-Token": settings.internal_token or ""}

    async def sql_execute(
        self,
        *,
        trace_id: str,
        tenant_key: str,
        sql_text: str,
        user_id: str | None = None,
        question: str | None = None,
        datasource_key: str = "default",
    ) -> SqlExecuteOutcome:
        payload: dict[str, object] = {
            "trace_id": trace_id,
            "tenant_key": tenant_key,
            "datasource_key": datasource_key,
            "sql_text": sql_text,
            "user_id": user_id,
            "question": question,
        }
        try:
            resp = await self._post("/internal/sql/execute", payload)
        except httpx.HTTPError as e:
            log.warning("sql_execute 回调异常: %s", e)
            return SqlExecuteOutcome(verdict="ERROR", reason=f"控制面不可达: {e}")
        if resp.status_code != 200:
            # 租户非法/绑定失败/数据源不支持等 HTTP 拒绝:重试无意义但语义非权限拦截,
            # 收敛 ERROR 让 selfheal 走有限重试后终止(计数即保护)
            return SqlExecuteOutcome(
                verdict="ERROR", reason=f"控制面拒绝(HTTP {resp.status_code})"
            )
        data = resp.json()
        return SqlExecuteOutcome(
            verdict=data["verdict"],
            reason=data.get("reason"),
            columns=list(data.get("columns") or []),
            rows=[list(r) for r in data.get("rows") or []],
            duration_ms=int(data.get("duration_ms") or 0),
        )

    async def fetch_schema(
        self, *, tenant_key: str, datasource_key: str = "default"
    ) -> SchemaMeta | None:
        """租户 schema 内省;任何失败返回 None(retrieve 降级空提示,不抛错)。"""
        try:
            resp = await self._post(
                "/internal/meta/schema",
                {"tenant_key": tenant_key, "datasource_key": datasource_key},
            )
        except httpx.HTTPError as e:
            log.warning("fetch_schema 回调异常: %s", e)
            return None
        if resp.status_code != 200:
            log.warning("fetch_schema 被拒(HTTP %s)", resp.status_code)
            return None
        data = resp.json()
        tables = [
            MetaTable(
                schema=str(t.get("schema") or ""),
                name=str(t.get("name") or ""),
                comment=str(t.get("comment") or ""),
                columns=[
                    MetaColumn(
                        name=str(c.get("name") or ""),
                        data_type=str(c.get("data_type") or ""),
                        comment=str(c.get("comment") or ""),
                    )
                    for c in t.get("columns") or []
                ],
            )
            for t in data.get("tables") or []
        ]
        return SchemaMeta(tables=tables, truncated=bool(data.get("truncated")))

    async def rag_retrieve(
        self, *, tenant_key: str, datasource_key: str, question: str
    ) -> RagContext | None:
        """三路召回(009):问句用 understand 产出的改写问句(语义完整);
       任何失败返回 None(retrieve 回退实时内省,不抛错)。"""
        try:
            resp = await self._post(
                "/internal/rag/retrieve",
                {
                    "tenant_key": tenant_key,
                    "datasource_key": datasource_key,
                    "question": question,
                },
            )
        except httpx.HTTPError as e:
            log.warning("rag_retrieve 回调异常: %s", e)
            return None
        if resp.status_code != 200:
            log.warning("rag_retrieve 被拒(HTTP %s)", resp.status_code)
            return None
        data = resp.json()
        return RagContext(
            schema_hint=str(data.get("schema_hint") or ""),
            history_sql=str(data.get("history_sql") or ""),
        )

    async def _post(self, path: str, payload: dict[str, object]) -> httpx.Response:
        async with httpx.AsyncClient(
            base_url=self._base, timeout=self._timeout, headers=self._headers
        ) as client:
            return await client.post(path, json=payload)
