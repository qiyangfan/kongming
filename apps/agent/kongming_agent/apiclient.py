"""控制面回调客户端(ADR-002:agent 获取数据与执行 SQL 的唯一途径)。

纪律:
- 全部请求携带 X-Internal-Token;agent 不持有任何数据库凭证;
- HTTP 层错误(403/404/5xx/超时/断连)一律收敛为领域结果,不裸抛——
  execute 收敛为 verdict=ERROR(供 selfheal 喂回),meta 内省收敛为 None
  (retrieve 降级空提示,nl2sql-agent spec「Schema 智能选取」)。
"""

import logging
from dataclasses import dataclass, field
from functools import partial
from typing import Literal, cast

import httpx

from kongming_agent.retry import (
    CALLBACK_MAX_ATTEMPTS,
    CALLBACK_RETRY_BASE_MS,
    CALLBACK_RETRY_MAX_WAIT_MS,
    call_with_retry,
    is_connect_phase_error,
)
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
class MetricHit:
    """语义层口径命中(076 纯增):标识/名称/口径表达式/修订时间。
    与 metric_hints 文字段同源(api 侧派生),零凭证透传。"""

    metric_id: str = ""
    metric_name: str = ""
    sql_template: str = ""
    updated_at: str | None = None


@dataclass(frozen=True)
class RagContext:
    """/internal/rag/retrieve 召回结果(009)。schema_hint 空=零命中(回退内省)。
    023 纯增:relations_hint/metric_hints 语义段(空串=无注入)。
    076 纯增:metric_hits 结构化口径命中(空=无命中)。"""

    schema_hint: str
    history_sql: str = ""
    relations_hint: str = ""
    metric_hints: str = ""
    metric_hits: list[MetricHit] = field(default_factory=list[MetricHit])  # 076
    doc_hints: list[dict[str, str]] = field(default_factory=list[dict[str, str]])  # 024 文档段
    # 056 skill-pack 投影段(空串=零包零回归)
    skill_terms: str = ""
    skill_examples: str = ""
    skill_persona: str = ""


@dataclass(frozen=True)
class ToolExecuteOutcome:
    """/internal/tool/execute 的响应(019;HTTP 层异常已收敛为 status=error)。"""

    status: str  # executed / rejected / error / rate_limited / confirm_required
    reason: str | None = None
    result: dict[str, object] | None = None
    confirm_id: str | None = None


def _json_of(resp: httpx.Response) -> dict[str, object] | None:
    data = resp.json()
    return cast("dict[str, object]", data) if isinstance(data, dict) else None


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
            return SqlExecuteOutcome(verdict="ERROR", reason=f"控制面拒绝(HTTP {resp.status_code})")
        # httpx resp.json() 返回 Any,边界处一次收窄(运行时即 dict,cast 零行为变化)
        data = cast("dict[str, object]", resp.json())
        return SqlExecuteOutcome(
            verdict=cast("Verdict", data["verdict"]),
            reason=cast("str | None", data.get("reason")),
            columns=list(cast("list[str]", data.get("columns") or [])),
            rows=[
                list(cast("list[object]", r)) for r in cast("list[object]", data.get("rows") or [])
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
        self,
        *,
        tenant_key: str,
        datasource_key: str,
        question: str,
        history: list[str] | None = None,
    ) -> RagContext | None:
        """三路召回(009):问句用 understand 产出的改写问句(语义完整);
        047 多轮继承:history 末条走继承路补上轮依赖表;任何失败返回
        None(retrieve 回退实时内省,不抛错)。"""
        payload: dict[str, object] = {
            "tenant_key": tenant_key,
            "datasource_key": datasource_key,
            "question": question,
        }
        if history:
            payload["history"] = history
        try:
            resp = await self._post("/internal/rag/retrieve", payload)
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
        hits_raw = data.get("metric_hits")
        hits: list[MetricHit] = []
        if isinstance(hits_raw, list):
            for h in cast("list[object]", hits_raw):
                if not isinstance(h, dict):
                    continue
                hm = cast("dict[str, object]", h)
                hits.append(
                    MetricHit(
                        metric_id=str(hm.get("metric_id") or ""),
                        metric_name=str(hm.get("metric_name") or ""),
                        sql_template=str(hm.get("sql_template") or ""),
                        updated_at=(
                            str(hm["updated_at"]) if hm.get("updated_at") is not None else None
                        ),
                    )
                )
        data_get = data.get
        return RagContext(
            schema_hint=str(data_get("schema_hint") or ""),
            history_sql=str(data_get("history_sql") or ""),
            relations_hint=str(data_get("relations_hint") or ""),
            metric_hints=str(data_get("metric_hints") or ""),
            metric_hits=hits,
            skill_terms=str(data.get("skill_terms") or ""),
            skill_examples=str(data.get("skill_examples") or ""),
            skill_persona=str(data.get("skill_persona") or ""),
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
        """LLM 用量上报(021):fire-and-forget;任何失败仅日志(统计口径非计费)。
        072:不走 _post 重试包装——重复落库,任何失败都不重发。"""
        try:
            resp = await self._post_once("/internal/llm-usage", {"records": records})
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
        072:不走 _post 重试包装——工具副作用不假定幂等,任何失败都不重发。
        """
        try:
            resp = await self._post_once(
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
            return ToolExecuteOutcome(status="error", reason=f"控制面拒绝(HTTP {resp.status_code})")
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
            result=cast("dict[str, object]", result_raw) if isinstance(result_raw, dict) else None,
            confirm_id=cast("str | None", data.get("confirm_id")),
        )

    async def task_plan(
        self, *, task_id: str, tenant_key: str, steps: list[dict[str, object]]
    ) -> list[dict[str, object]] | None:
        """计划回写(090):返回落库后的步骤清单(含 id);被拒返回 None。"""
        try:
            resp = await self._post_once(
                "/internal/task/plan",
                {"task_id": task_id, "tenant_key": tenant_key, "steps": steps},
            )
        except httpx.HTTPError as e:
            log.warning("task_plan 回调异常: %s", e)
            return None
        if resp.status_code == 409:
            return []  # 步骤已存在(续跑):编排器改读既有清单
        if resp.status_code != 200:
            log.warning("task_plan 非 200: HTTP %s", resp.status_code)
            return None
        data = _json_of(resp)
        items = (data or {}).get("steps")
        out: list[dict[str, object]] = []
        if isinstance(items, list):
            out = [dict(cast("dict[str, object]", i)) for i in cast("list[object]", items)]
        return out

    async def task_steps(self, *, task_id: str, tenant_key: str) -> list[dict[str, object]] | None:
        """步骤清单读取(续跑路径;任何失败返回 None)。"""
        try:
            resp = await self._get_once(
                "/internal/task/steps",
                params={"task_id": task_id, "tenant_key": tenant_key},
            )
        except httpx.HTTPError as e:
            log.warning("task_steps 回调异常: %s", e)
            return None
        if resp.status_code != 200:
            return None
        data = _json_of(resp)
        items = (data or {}).get("steps")
        out: list[dict[str, object]] = []
        if isinstance(items, list):
            for i in cast("list[object]", items):
                if isinstance(i, dict):
                    out.append(cast("dict[str, object]", i))
        return out

    async def task_step_start(
        self, *, task_id: str, tenant_key: str, step_id: str, epoch: int = 0
    ) -> dict[str, object] | None:
        """步骤启动决策(execute/await_approval/pause/skip/abort);HTTP 层异常=None。"""
        try:
            resp = await self._post_once(
                "/internal/task/step-start",
                {
                    "task_id": task_id,
                    "tenant_key": tenant_key,
                    "step_id": step_id,
                    "epoch": epoch,
                },
            )
        except httpx.HTTPError as e:
            log.warning("task_step_start 回调异常: %s", e)
            return None
        return _json_of(resp) if resp.status_code == 200 else None

    async def task_step_result(self, **kw: object) -> dict[str, object] | None:
        """步骤结果回写(证据驱动;控制面返回 continue/reject 决策)。"""
        try:
            resp = await self._post_once("/internal/task/step-result", dict(kw))
        except httpx.HTTPError as e:
            log.warning("task_step_result 回调异常: %s", e)
            return None
        return _json_of(resp) if resp.status_code == 200 else None

    async def task_finish(
        self,
        *,
        task_id: str,
        tenant_key: str,
        status: str,
        fail_reason: str | None,
        epoch: int = 0,
    ) -> bool:
        """任务终态回写(DONE/FAILED);失败仅日志(终态另有兜底路径)。"""
        try:
            resp = await self._post_once(
                "/internal/task/finish",
                {
                    "task_id": task_id,
                    "tenant_key": tenant_key,
                    "epoch": epoch,
                    "status": status,
                    "fail_reason": fail_reason,
                },
            )
            return resp.status_code == 204
        except httpx.HTTPError as e:
            log.warning("task_finish 回调异常: %s", e)
            return False

    # ── 协作账本回调(091;开账 fail-closed,worker 行/终态尽力而为)──

    async def collab_start(
        self, *, task_id: str, tenant_key: str, step_id: str, depth: int = 1
    ) -> dict[str, object] | None:
        """协作开账(091):返回 {run_id, topology}(拓扑以控制面库内声明为准);
        开账失败返回 None(调用方 fail-closed——无账不执行,防游离协作面)。"""
        try:
            resp = await self._post_once(
                "/internal/task/collab-start",
                {
                    "task_id": task_id,
                    "tenant_key": tenant_key,
                    "step_id": step_id,
                    "depth": depth,
                },
            )
        except httpx.HTTPError as e:
            log.warning("collab_start 回调异常: %s", e)
            return None
        return _json_of(resp) if resp.status_code == 200 else None

    async def collab_worker_start(
        self,
        *,
        task_id: str,
        tenant_key: str,
        run_id: str,
        worker_no: int,
        subgoal: str,
        bot_key: str | None,
        trace_id: str,
    ) -> bool:
        """worker 派发落账(091);失败仅日志(账本尽力而为,执行照常)。"""
        try:
            resp = await self._post_once(
                "/internal/task/collab-worker-start",
                {
                    "task_id": task_id,
                    "tenant_key": tenant_key,
                    "run_id": run_id,
                    "worker_no": worker_no,
                    "subgoal": subgoal[:2000],
                    "bot_key": bot_key,
                    "trace_id": trace_id[:64],
                },
            )
            return resp.status_code == 204
        except httpx.HTTPError as e:
            log.warning("collab_worker_start 回调异常: %s", e)
            return False

    async def collab_worker_result(
        self,
        *,
        task_id: str,
        tenant_key: str,
        run_id: str,
        worker_no: int,
        status: str,
        conclusion: dict[str, object],
        evidence: dict[str, object],
        usage: dict[str, object],
        trace_id: str,
    ) -> bool:
        """worker 终态回写(091,用量按 worker 归集);失败仅日志。"""
        try:
            resp = await self._post_once(
                "/internal/task/collab-worker-result",
                {
                    "task_id": task_id,
                    "tenant_key": tenant_key,
                    "run_id": run_id,
                    "worker_no": worker_no,
                    "status": status,
                    "conclusion": conclusion,
                    "evidence": evidence,
                    "usage": usage,
                    "trace_id": trace_id[:64],
                },
            )
            return resp.status_code == 204
        except httpx.HTTPError as e:
            log.warning("collab_worker_result 回调异常: %s", e)
            return False

    async def collab_finish(
        self,
        *,
        task_id: str,
        tenant_key: str,
        run_id: str,
        verdict: str,
        pending_count: int,
        dissent: bool,
        summary: str,
    ) -> dict[str, object] | None:
        """协作运行终态回写(091);失败仅日志(重跑/取消路径收敛账本)。"""
        try:
            resp = await self._post_once(
                "/internal/task/collab-finish",
                {
                    "task_id": task_id,
                    "tenant_key": tenant_key,
                    "run_id": run_id,
                    "verdict": verdict,
                    "pending_count": pending_count,
                    "dissent": dissent,
                    "summary": summary[:8000],
                },
            )
        except httpx.HTTPError as e:
            log.warning("collab_finish 回调异常: %s", e)
            return None
        return _json_of(resp) if resp.status_code == 200 else None

    async def report_progress(self, *, report_id: str, phase: str) -> None:
        """报告生成进度回写(088):POST /internal/report/progress。
        进度是尽力而为信号:任何失败仅日志,不阻断编排主链路,不重发。"""
        try:
            resp = await self._post_once(
                "/internal/report/progress", {"report_id": report_id, "phase": phase}
            )
            if resp.status_code != 204 and resp.status_code != 200:
                log.warning("report_progress 非 2xx: HTTP %s", resp.status_code)
        except Exception as e:  # noqa: BLE001  # 进度回写永不阻断主链路
            log.warning("report_progress 失败(忽略): %s", e)

    async def attachment_content(
        self, att_id: str, *, tenant_key: str, user_id: str
    ) -> tuple[str, bytes] | None:
        """030:一次性取附件内容(内部端点绑定复校+消费语义);
        404/异常 → None(由调用方 fail-closed 文案化)。072:同 _post 的
        连接层分层重试口径(读超时=已送达,不重发)。"""
        try:
            resp = await self._get_retryable(
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
        """控制面 POST(只读类回调入口;072 分层重试):仅**连接层未送达**失败
        按回调预算快重试(design D2——读超时=已送达,执行副作用不可假定幂等,
        不重发;5xx 语义上亦已送达)。report_usage/tool_execute 走 _post_once
        (任何失败都不重试)。"""
        return await call_with_retry(
            partial(self._post_once, path, payload),
            max_attempts=CALLBACK_MAX_ATTEMPTS,
            base_delay_ms=CALLBACK_RETRY_BASE_MS,
            max_wait_ms=CALLBACK_RETRY_MAX_WAIT_MS,
            is_transient=is_connect_phase_error,
        )

    async def _post_once(self, path: str, payload: dict[str, object]) -> httpx.Response:
        async with httpx.AsyncClient(
            base_url=self._base, timeout=self._timeout, headers=self._headers
        ) as client:
            return await client.post(path, json=payload)

    async def _get_retryable(self, path: str, params: dict[str, str]) -> httpx.Response:
        """控制面 GET(030 附件取用;同 _post 的连接层分层重试口径)。"""
        return await call_with_retry(
            partial(self._get_once, path, params),
            max_attempts=CALLBACK_MAX_ATTEMPTS,
            base_delay_ms=CALLBACK_RETRY_BASE_MS,
            max_wait_ms=CALLBACK_RETRY_MAX_WAIT_MS,
            is_transient=is_connect_phase_error,
        )

    async def _get_once(self, path: str, params: dict[str, str]) -> httpx.Response:
        async with httpx.AsyncClient(
            base_url=self._base, timeout=self._timeout, headers=self._headers
        ) as client:
            return await client.get(path, params=params)
