"""控制面回调客户端(ADR-002:agent 获取数据与执行 SQL 的唯一途径)。

纪律:
- 全部请求携带 X-Internal-Token;agent 不持有任何数据库凭证;
- HTTP 层错误(403/404/5xx/超时/断连)一律收敛为领域结果,不裸抛——
  execute 收敛为 verdict=ERROR(供 selfheal 喂回),meta 内省收敛为 None
  (retrieve 降级空提示,nl2sql-agent spec「Schema 智能选取」)。
"""

import logging
from dataclasses import dataclass, field
from typing import Literal, cast

import httpx

from kongming_agent.settings import Settings

log = logging.getLogger(__name__)

Verdict = Literal["PASS", "BLOCKED", "ERROR"]


@dataclass(frozen=True)
class SqlExecuteOutcome:
    """/internal/sql/execute 的三态响应(HTTP 层异常已收敛为 ERROR)。"""

    verdict: Verdict
    reason: str | None = None
    columns: list[str] = field(default_factory=list[str])
    rows: list[list[object]] = field(default_factory=list[list[object]])
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
    """/internal/rag/retrieve 召回结果(009)。schema_hint 空=零命中(回退内省)。
    023 纯增:relations_hint/metric_hints 语义段(空串=无注入)。"""

    schema_hint: str
    history_sql: str = ""
    relations_hint: str = ""
    metric_hints: str = ""
    doc_hints: list[dict[str, str]] = field(default_factory=list[dict[str, str]])  # 024 文档段


@dataclass(frozen=True)
class ToolExecuteOutcome:
    """/internal/tool/execute 的响应(019;HTTP 层异常已收敛为 status=error)。"""

    status: str  # executed / rejected / error / rate_limited / confirm_required
    reason: str | None = None
    result: dict[str, object] | None = None
    confirm_id: str | None = None


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
        # httpx resp.json() 返回 Any,边界处一次收窄(运行时即 dict,cast 零行为变化)
        data = cast("dict[str, object]", resp.json())
        return SqlExecuteOutcome(
            verdict=cast("Verdict", data["verdict"]),
            reason=cast("str | None", data.get("reason")),
            columns=list(cast("list[str]", data.get("columns") or [])),
            rows=[
                list(cast("list[object]", r))
                for r in cast("list[object]", data.get("rows") or [])
            ],
            duration_ms=int(cast("int | float | str", data.get("duration_ms")) or 0),
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
        data = cast("dict[str, object]", resp.json())
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
                    for c in cast("list[dict[str, object]]", t.get("columns") or [])
                ],
            )
            for t in cast("list[dict[str, object]]", data.get("tables") or [])
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
        try:
            data = cast("dict[str, object]", resp.json())
        except ValueError:
            # 200 但非 JSON(网关回 HTML 错误页等):按失败回退内省,不裸抛
            # (013 挂账⑮;docstring「任何失败返回 None」自此兑现)
            log.warning("rag_retrieve 响应非 JSON,回退实时内省")
            return None
        return RagContext(
            schema_hint=str(data.get("schema_hint") or ""),
            history_sql=str(data.get("history_sql") or ""),
            relations_hint=str(data.get("relations_hint") or ""),
            metric_hints=str(data.get("metric_hints") or ""),
            doc_hints=[
                {
                    "source_ref": str(cast("dict[str, object]", h).get("source_ref") or ""),
                    "content": str(cast("dict[str, object]", h).get("content") or ""),
                }
                for h in cast("list[object]", data.get("doc_hints") or [])
                if isinstance(h, dict)
            ],
        )

    async def report_usage(self, records: list[dict[str, object]]) -> None:
        """LLM 用量上报(021):fire-and-forget;任何失败仅日志(统计口径非计费)。"""
        try:
            resp = await self._post("/internal/llm-usage", {"records": records})
            if resp.status_code != 200:
                log.warning("usage 上报非 200: HTTP %s(丢弃)", resp.status_code)
        except Exception as e:  # noqa: BLE001  # 上报永不阻断主链路
            log.warning("usage 上报失败(丢弃): %s", e)

    async def tool_execute(
        self,
        *,
        trace_id: str,
        tenant_key: str,
        datasource_key: str,
        user_id: str | None,
        tool_name: str,
        args: dict[str, object],
        channel: str = "chat",  # 025
    ) -> ToolExecuteOutcome:
        """通用工具执行回调(019):agent 只传工具名/参数/上下文,零凭证(ADR-002)。

        HTTP 层异常(超时/断连/5xx)收敛 status=error 喂回 LLM,不裸抛。
        """
        try:
            resp = await self._post(
                "/internal/tool/execute",
                {
                    "trace_id": trace_id,
                    "tenant_key": tenant_key,
                    "datasource_key": datasource_key,
                    "user_id": user_id,
                    "tool_name": tool_name,
                    "args": args,
                    "channel": channel,
                },
            )
        except httpx.HTTPError as e:
            log.warning("tool_execute 回调异常: %s", e)
            return ToolExecuteOutcome(status="error", reason=f"控制面不可达: {e}")
        if resp.status_code != 200:
            return ToolExecuteOutcome(
                status="error", reason=f"控制面拒绝(HTTP {resp.status_code})"
            )
        try:
            data = cast("dict[str, object]", resp.json())
        except ValueError:
            log.warning("tool_execute 响应非 JSON,按错误观察喂回")
            return ToolExecuteOutcome(status="error", reason="控制面响应畸形")
        # 守卫后收窄:isinstance(dict) 产物键型仍 Unknown,cast 后再入构造
        result_raw = data.get("result")
        return ToolExecuteOutcome(
            status=str(data.get("status") or "error"),
            reason=cast("str | None", data.get("reason")),
            result=cast("dict[str, object]", result_raw)
            if isinstance(result_raw, dict)
            else None,
            confirm_id=cast("str | None", data.get("confirm_id")),
        )

    async def attachment_content(
        self, att_id: str, *, tenant_key: str, user_id: str
    ) -> tuple[str, bytes] | None:
        """030:一次性取附件内容(内部端点绑定复校+消费语义);
        404/异常 → None(由调用方 fail-closed 文案化)。"""
        try:
            async with httpx.AsyncClient(
                base_url=self._base, timeout=self._timeout, headers=self._headers
            ) as client:
                resp = await client.get(
                    f"/internal/attachment/{att_id}/content",
                    params={"tenant_key": tenant_key, "user_id": user_id},
                )
        except httpx.HTTPError as e:
            log.warning("附件取用异常: %s", e)
            return None
        if resp.status_code != 200:
            return None
        return resp.headers.get("content-type", "image/png").split(";")[0], resp.content

    async def _post(self, path: str, payload: dict[str, object]) -> httpx.Response:
        async with httpx.AsyncClient(
            base_url=self._base, timeout=self._timeout, headers=self._headers
        ) as client:
            return await client.post(path, json=payload)
