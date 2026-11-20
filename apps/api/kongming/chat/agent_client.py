"""Agent 面回调客户端(httpx):控制面 → agent 的唯一通道。

错误语义(nl2sql spec「控制面回调 Agent 面」):不可达/超时/非 200
一律抛 AgentUnavailable,由 service 映射友好错误——不向上抛裸异常、
不泄露内部地址与堆栈。
"""

import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any, cast

import httpx

from kongming.chat.sse import SseDecoder
from kongming.config import Settings

log = logging.getLogger(__name__)

# 流式帧回调:(event, data_json);协程返回由调用方 await
StreamFrameHandler = Callable[[str, str], Coroutine[Any, Any, None]]


class AgentUnavailable(Exception):
    """Agent 面不可达/超时/异常响应(对用户只呈现友好文案)。"""


@dataclass(frozen=True)
class AgentReply:
    success: bool
    verdict: str | None = None
    summary: str = ""
    # default_factory 用带参泛型别名:裸 list/dict 工厂在 pyright strict 下产物 Unknown
    insights: list[dict[str, str]] = field(default_factory=list[dict[str, str]])
    stats: dict[str, object] = field(default_factory=dict[str, object])
    columns: list[str] = field(default_factory=list[str])
    rows: list[list[object]] = field(default_factory=list[list[object]])
    sql: str = ""
    retry_count: int = 0
    error: str | None = None
    # 管理面诊断信号:仅落库,不进用户响应/SSE(逐字段同构契约不破)
    resolved_question: str | None = None
    prompt_version: str | None = None
    # 兜底轮分类(CHITCHAT/CLARIFY;仅兜底轮非空;落库不进用户响应)
    fallback_type: str | None = None
    # 语义层口径命中:随 result_json 落库+回显,不进 SSE 用户事件
    metric_hits: list[dict[str, str | None]] = field(default_factory=list[dict[str, str | None]])
    # 文档道结构化来源:随 result_json 落库+回显;SQL 道恒空
    sources: list[dict[str, str]] = field(default_factory=list[dict[str, str]])
    # Bot 请求内收集用量记录,随 /query_react 响应返回。
    usage_records: list[dict[str, object]] = field(default_factory=list[dict[str, object]])


@dataclass(frozen=True)
class _QueryRequest:
    """同步/流式问答请求要素(公共键 + 可选携带项)。"""

    trace_id: str
    tenant_key: str
    question: str
    user_id: str | None
    history: list[str]
    datasource_key: str
    react_tools: list[dict[str, object]] | None = None
    memory_hint: str = ""
    attachment_ids: list[str] | None = None
    last_fallback: str = ""


@dataclass(frozen=True)
class _QueryPayload:
    """agent 终态报文中需守卫收窄的要素集(报文 → AgentReply 的解析产物)。"""

    insights: list[dict[str, str]]
    stats: dict[str, object]
    columns: list[str]
    rows: list[list[object]]
    usage_records: list[dict[str, object]]
    metric_hits: list[dict[str, str | None]]
    sources: list[dict[str, str]]

    @classmethod
    def of(cls, data: dict[str, object]) -> "_QueryPayload":
        """逐键守卫收窄;畸形键一律按空处理(不阻断解析)。"""
        insights_raw = data.get("insights")
        stats_raw = data.get("stats")
        columns_raw = data.get("columns")
        rows_raw = data.get("rows")
        usage_raw = data.get("usage_records")
        return cls(
            insights=_insights_of(insights_raw),
            stats=dict(cast(dict[str, object], stats_raw)) if isinstance(stats_raw, dict) else {},
            columns=(
                [str(c) for c in cast(list[object], columns_raw)]
                if isinstance(columns_raw, list)
                else []
            ),
            rows=(
                [list(cast(list[object], r)) for r in cast(list[object], rows_raw)]
                if isinstance(rows_raw, list)
                else []
            ),
            usage_records=(
                [
                    dict(entry)
                    for item in cast(list[object], usage_raw)
                    if (entry := _obj_dict(item)) is not None
                ]
                if isinstance(usage_raw, list)
                else []
            ),
            metric_hits=_metric_hits_of(data.get("metric_hits")),
            sources=_sources_of(data.get("sources")),
        )


def _obj_dict(value: object) -> dict[str, object] | None:
    """dict 守卫 + 键型收窄(httpx resp.json 的 Any 在 strict 下传播
    Unknown,边界处一次收窄)。"""
    return cast(dict[str, object], value) if isinstance(value, dict) else None


def _insights_of(raw: object) -> list[dict[str, str]]:
    """QueryOut.insights → AgentReply 载荷(非列表项跳过)。"""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    # isinstance 收窄产物为 list[Unknown],cast 一次再迭代(运行时不变)
    for item in cast(list[object], raw):
        entry = _obj_dict(item)
        if entry is not None:
            out.append({"type": str(entry.get("type", "")), "text": str(entry.get("text", ""))})
    return out


def _metric_hits_of(raw: object) -> list[dict[str, str | None]]:
    """QueryOut.metric_hits → AgentReply 载荷(守卫收窄;dict 且键全 str)。"""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str | None]] = []
    for item in cast("list[object]", raw):
        if not isinstance(item, dict):
            continue
        d = cast("dict[str, object]", item)
        mid = d.get("metric_id")
        name = d.get("metric_name")
        tpl = d.get("sql_template")
        updated = d.get("updated_at")
        if not isinstance(mid, str) or not isinstance(name, str):
            continue  # 畸形条目跳过(不阻断解析)
        out.append(
            {
                "metric_id": mid,
                "metric_name": name,
                "sql_template": tpl if isinstance(tpl, str) else "",
                "updated_at": updated if isinstance(updated, str) else None,
            }
        )
    return out


def _sources_of(raw: object) -> list[dict[str, str]]:
    """QueryOut.sources → AgentReply 载荷(守卫收窄;缺 citation_key 跳过)。"""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in cast("list[object]", raw):
        if not isinstance(item, dict):
            continue
        d = cast("dict[str, object]", item)
        key = d.get("citation_key")
        if not isinstance(key, str) or not key:
            continue
        out.append(
            {
                "citation_key": key,
                "source_ref": str(d.get("source_ref") or ""),
                "doc_name": str(d.get("doc_name") or ""),
            }
        )
    return out


def parse_agent_reply(data: dict[str, object]) -> AgentReply:
    """agent 终态报文 → AgentReply(同步响应/流式 result 帧共用,单一定义防漂移)。"""
    verdict = data.get("verdict")
    error = data.get("error")
    retry = data.get("retry_count")
    # 诊断信号解析(旧版 agent 无该键 → None)
    resolved_question = data.get("resolved_question")
    prompt_version = data.get("prompt_version")
    fallback_raw = data.get("fallback_type")
    payload = _QueryPayload.of(data)
    return AgentReply(
        success=bool(data.get("success")),
        verdict=verdict if isinstance(verdict, str) else None,
        summary=str(data.get("summary") or ""),
        insights=payload.insights,
        stats=payload.stats,
        columns=payload.columns,
        rows=payload.rows,
        sql=str(data.get("sql") or ""),
        retry_count=retry if isinstance(retry, int) else 0,
        error=error if isinstance(error, str) else None,
        # 诊断信号(旧版 agent 缺键 → None)
        resolved_question=resolved_question if isinstance(resolved_question, str) else None,
        prompt_version=prompt_version if isinstance(prompt_version, str) else None,
        fallback_type=fallback_raw if isinstance(fallback_raw, str) and fallback_raw else None,
        metric_hits=payload.metric_hits,
        sources=payload.sources,
        usage_records=payload.usage_records,
    )


def _react_payload(
    *,
    trace_id: str,
    tenant_key: str,
    question: str,
    user_id: str | None,
    history: list[str],
    datasource_key: str,
    tools: list[dict[str, object]],
    bot_system_prompt: str,
    model: str | None,
    channel: str,
    collect_usage: bool,
) -> dict[str, object]:
    """ReAct 显式调用请求体:基础键 + 按需携带的 Bot 系统提示/模型/渠道/用量标记。"""
    payload: dict[str, object] = {
        "trace_id": trace_id,
        "tenant_key": tenant_key,
        "question": question,
        "user_id": user_id,
        "history": history,
        "datasource_key": datasource_key,
        "tools": tools,
    }
    if bot_system_prompt:
        payload["bot_system_prompt"] = bot_system_prompt
    if model:
        payload["model"] = model
    if channel != "chat":
        payload["channel"] = channel
    if collect_usage:
        payload["collect_usage"] = True
    return payload


class AgentClient:
    def __init__(self, settings: Settings) -> None:
        self._base = settings.agent_base_url.rstrip("/")
        self._timeout = settings.agent_timeout_sec
        self._headers = {"X-Internal-Token": settings.internal_token or ""}
        # 报告生成是长任务(多轮 LLM+取数),独立超时预算(非单轮问答口径)
        self._report_timeout = settings.agent_report_timeout_sec
        # 任务编排是多步长任务,超时须覆盖任务墙钟预算上限(见 config)
        self._task_timeout = settings.agent_task_timeout_sec
        # 评审主张分解是长输出(≤12 主张×摘录),独立超时预算(非标题 30s 口径)
        self._scoring_timeout = settings.answer_scoring_timeout_sec
        # 看门狗判读是单次轻量调用,独立短超时(不占问答/编排预算)
        self._watchdog_timeout = settings.watchdog_judge_timeout_sec

    def _client(self, timeout: float | None = None) -> httpx.AsyncClient:
        """出站客户端(不采环境代理:SSRF 钉扎/内网回调语义;缺省用问答超时)。"""
        return httpx.AsyncClient(
            trust_env=False,
            base_url=self._base,
            timeout=self._timeout if timeout is None else timeout,
            headers=self._headers,
        )

    async def query(
        self,
        *,
        trace_id: str,
        tenant_key: str,
        question: str,
        user_id: str | None,
        history: list[str],
        datasource_key: str,
        react_tools: list[dict[str, object]] | None = None,
        memory_hint: str = "",
        attachment_ids: list[str] | None = None,
        last_fallback: str = "",
    ) -> AgentReply:
        """同步问答;react_tools 非 None 即走 ReAct 内核端点(tools 注入);
        memory_hint=记忆段(空=不下发记忆段);last_fallback=上一轮兜底
        分类(澄清防循环判据,空=不携带)。"""
        path = "/query_react" if react_tools is not None else "/query"
        try:
            async with self._client() as client:
                resp = await client.post(
                    path,
                    json=self._payload(
                        _QueryRequest(
                            trace_id=trace_id,
                            tenant_key=tenant_key,
                            question=question,
                            user_id=user_id,
                            history=history,
                            datasource_key=datasource_key,
                            react_tools=react_tools,
                            memory_hint=memory_hint,
                            attachment_ids=attachment_ids,
                            last_fallback=last_fallback,
                        )
                    ),
                )
        except httpx.HTTPError as e:
            log.warning("agent 回调异常: %s", e)
            raise AgentUnavailable(str(e)) from e
        if resp.status_code != 200:
            log.warning("agent 回调非 200: HTTP %s", resp.status_code)
            raise AgentUnavailable(f"HTTP {resp.status_code}")
        try:
            return parse_agent_reply(resp.json())
        except (ValueError, TypeError, AttributeError) as e:
            # agent 返回 200 但报文非预期:同不可达收敛,不裸 500
            log.warning("agent 响应解析失败: %s", e)
            raise AgentUnavailable(f"bad reply: {e}") from e

    async def query_stream(
        self,
        *,
        trace_id: str,
        tenant_key: str,
        question: str,
        user_id: str | None,
        history: list[str],
        datasource_key: str,
        on_frame: StreamFrameHandler,
        react_tools: list[dict[str, object]] | None = None,
        memory_hint: str = "",
        attachment_ids: list[str] | None = None,
        last_fallback: str = "",
    ) -> None:
        """流式问答:消费 agent SSE 流,逐帧 (event, data_json) 回调;
        错误语义与同步 query 同(不可达/非 200/传输失败一律 AgentUnavailable,
        由调用方收敛 error 终态+FAILED 落库)。react_tools 非 None 走
        /query_react/stream(tools 注入,事件含 tool_call)。"""
        await self._consume_query_stream(
            _QueryRequest(
                trace_id=trace_id,
                tenant_key=tenant_key,
                question=question,
                user_id=user_id,
                history=history,
                datasource_key=datasource_key,
                react_tools=react_tools,
                memory_hint=memory_hint,
                attachment_ids=attachment_ids,
                last_fallback=last_fallback,
            ),
            on_frame,
        )

    async def _consume_query_stream(self, req: _QueryRequest, on_frame: StreamFrameHandler) -> None:
        """按 SSE 逐帧回调(不被调用方覆写;query_stream 的对外语义由它承载)。"""
        path = "/query_react/stream" if req.react_tools is not None else "/query/stream"
        decoder = SseDecoder()
        try:
            async with (
                self._client() as client,
                client.stream("POST", path, json=self._payload(req)) as resp,
            ):
                if resp.status_code != 200:
                    log.warning("agent 流式回调非 200: HTTP %s", resp.status_code)
                    raise AgentUnavailable(f"HTTP {resp.status_code}")
                async for line in resp.aiter_lines():
                    frame = decoder.feed(line)
                    if frame is not None:
                        await on_frame(frame[0], frame[1])
        except httpx.HTTPError as e:
            log.warning("agent 流式回调异常: %s", e)
            raise AgentUnavailable(str(e)) from e

    async def suggest_questions(self, digest: str) -> list[str]:
        """建议问题生成(Suggestor 端口实现):回调 agent
        /internal/suggest-questions。降级契约:不可达/超时/非 200/报文畸形
        一律返回空清单(service 按生成失败收敛,不落缓存),不抛异常。"""
        try:
            async with self._client() as client:
                resp = await client.post("/internal/suggest-questions", json={"digest": digest})
        except httpx.HTTPError as e:
            log.warning("建议生成回调异常: %s", e)
            return []
        if resp.status_code != 200:
            log.warning("建议生成回调非 200: HTTP %s", resp.status_code)
            return []
        try:
            data = _obj_dict(resp.json())
        except (ValueError, TypeError, AttributeError) as e:
            # 200 但报文非 JSON(如中间层注入 HTML):同不可达收敛,不裸 500
            log.warning("建议生成响应解析失败: %s", e)
            return []
        if data is None or data.get("generated") is not True:
            return []
        raw = data.get("questions")
        if not isinstance(raw, list):
            return []
        return cast(list[str], raw)

    @staticmethod
    def _payload(req: "_QueryRequest") -> dict[str, object]:
        """问答请求体:公共键 + 按需携带的工具/记忆/附件/兜底分类。"""
        payload: dict[str, object] = {
            "trace_id": req.trace_id,
            "tenant_key": req.tenant_key,
            "question": req.question,
            "user_id": req.user_id,
            "history": req.history,
            "datasource_key": req.datasource_key,
        }
        if req.react_tools is not None:
            payload["tools"] = req.react_tools  # ReAct 内核:api 按授权注入
        if req.memory_hint:
            payload["memory_hint"] = req.memory_hint
        if req.attachment_ids:
            payload["attachment_ids"] = req.attachment_ids
        if req.last_fallback:
            payload["last_fallback"] = req.last_fallback  # 澄清防循环判据(缺省不携带)
        return payload

    async def query_react(
        self,
        *,
        trace_id: str,
        tenant_key: str,
        question: str,
        user_id: str | None,
        history: list[str],
        datasource_key: str,
        tools: list[dict[str, object]],
        bot_system_prompt: str = "",
        model: str | None = None,  # Bot 模型覆盖
        channel: str = "chat",  # 渠道标记
        collect_usage: bool = False,  # 仅 Bot 调用启用用量收集。
    ) -> AgentReply:
        """ReAct 显式调用(tools 注入 + Bot 系统提示);错误语义与 query 同。"""
        try:
            async with self._client() as client:
                resp = await client.post(
                    "/query_react",
                    json=_react_payload(
                        trace_id=trace_id,
                        tenant_key=tenant_key,
                        question=question,
                        user_id=user_id,
                        history=history,
                        datasource_key=datasource_key,
                        tools=tools,
                        bot_system_prompt=bot_system_prompt,
                        model=model,
                        channel=channel,
                        collect_usage=collect_usage,
                    ),
                )
        except httpx.HTTPError as e:
            log.warning("agent react 回调异常: %s", e)
            raise AgentUnavailable(str(e)) from e
        if resp.status_code != 200:
            raise AgentUnavailable(f"HTTP {resp.status_code}")
        try:
            return parse_agent_reply(resp.json())
        except (ValueError, TypeError, AttributeError) as e:
            raise AgentUnavailable(f"bad reply: {e}") from e

    async def list_models(self) -> list[str]:
        """注册表模型名(失败返回空——fail-closed 校验语义)。"""
        try:
            async with self._client(10.0) as client:
                resp = await client.get("/internal/models")
        except httpx.HTTPError:
            return []
        if resp.status_code != 200:
            return []
        try:
            data = _obj_dict(resp.json())
        except (ValueError, TypeError):
            return []
        if data is None:
            return []
        models: object = data.get("models") or []  # 注解 object:裸 or [] 产物 Unknown
        if not isinstance(models, list):
            return []  # 畸形报文 fail-closed(契约:失败返回空,不向模型校验路径抛)
        return [str(m) for m in cast(list[object], models)]

    async def extract_memory(self, *, question: str, answer: str) -> list[dict[str, object]]:
        """隐式偏好提取:agent /internal/extract-memory 回调槽位 patch;
        任何失败收敛空列表(后台任务静默跳过)。"""
        try:
            async with self._client(30.0) as client:
                resp = await client.post(
                    "/internal/extract-memory",
                    json={"question": question, "answer": answer[:2000]},
                )
        except httpx.HTTPError as e:
            log.warning("记忆提取回调异常: %s", e)
            return []
        if resp.status_code != 200:
            log.warning("记忆提取非 200: HTTP %s", resp.status_code)
            return []
        try:
            data = _obj_dict(resp.json())
        except (ValueError, TypeError, AttributeError) as e:
            log.warning("记忆提取响应解析失败: %s", e)
            return []
        if data is None:
            return []
        items: object = data.get("memories") or []  # 注解 object:裸 or [] 产物 Unknown
        if not isinstance(items, list):
            return []  # 畸形报文静默收敛(契约:任何失败返回空,永不抛)
        out: list[dict[str, object]] = []
        for item in cast(list[object], items):
            entry = _obj_dict(item)
            if entry is None:
                continue
            kept = {str(k): v for k, v in entry.items() if v is not None and v != "" and v != []}
            if kept:
                out.append(kept)
        return out

    async def rewrite_title(self, *, question: str) -> str | None:
        """会话标题改写:后台任务兜底语义——任何失败(不可达/非 200/降级
        应答/坏报文)一律返回 None,永不抛;调用方保持截断标题。"""
        try:
            async with self._client(30.0) as client:
                resp = await client.post("/internal/rewrite-title", json={"question": question})
        except httpx.HTTPError as e:
            log.warning("标题改写回调异常: %s", e)
            return None
        if resp.status_code != 200:
            log.warning("标题改写非 200: HTTP %s", resp.status_code)
            return None
        try:
            data = _obj_dict(resp.json())
        except (ValueError, TypeError, AttributeError) as e:
            log.warning("标题改写响应解析失败: %s", e)
            return None
        if data is None or not data.get("rewritten"):
            return None  # agent 侧降级(LLM 失败/输出非法)
        title = str(data.get("title") or "").strip()
        return title or None

    async def score_answer(
        self, *, material: str, trace_id: str = "", tenant_key: str = ""
    ) -> dict[str, object] | None:
        """答案评审回调:POST /internal/score-answer。降级契约同
        rewrite_title——不可达/非 200/坏报文/decomposed=False 一律返回
        None 永不抛(调用方不落评分);返回携带评审模型名(审计归因)。"""
        try:
            async with self._client(self._scoring_timeout) as client:
                resp = await client.post(
                    "/internal/score-answer",
                    json={
                        "material": material,
                        "trace_id": trace_id,
                        "tenant_key": tenant_key,
                    },
                )
        except httpx.HTTPError as e:
            log.warning("答案评审回调异常: %s", e)
            return None
        if resp.status_code != 200:
            log.warning("答案评审非 200: HTTP %s", resp.status_code)
            return None
        try:
            data = _obj_dict(resp.json())
        except (ValueError, TypeError, AttributeError) as e:
            log.warning("答案评审响应解析失败: %s", e)
            return None
        if data is None or not data.get("decomposed"):
            return None  # agent 侧降级(LLM 失败/输出不可解析)
        decomposition = data.get("decomposition")
        if not isinstance(decomposition, dict) or not decomposition:
            return None
        return {
            "decomposition": decomposition,
            "score_model": str(data.get("score_model") or ""),
        }

    async def judge_stagnation(
        self, *, goal: str, events: list[dict[str, object]], stale_sec: int
    ) -> dict[str, object] | None:
        """停滞判读回调:POST /internal/judge-stagnation。降级契约同
        score_answer——不可达/非 200/坏报文/judged=False 一律返回 None
        永不抛(调用方按判读不可用落标记事件走机械兜底)。"""
        try:
            async with self._client(self._watchdog_timeout) as client:
                resp = await client.post(
                    "/internal/judge-stagnation",
                    json={"goal": goal, "events": events, "stale_sec": stale_sec},
                )
        except httpx.HTTPError as e:
            log.warning("停滞判读回调异常: %s", e)
            return None
        if resp.status_code != 200:
            log.warning("停滞判读非 200: HTTP %s", resp.status_code)
            return None
        try:
            data = _obj_dict(resp.json())
        except (ValueError, TypeError, AttributeError) as e:
            log.warning("停滞判读响应解析失败: %s", e)
            return None
        if data is None or not data.get("judged"):
            return None  # agent 侧降级(LLM 失败/输出不可解析)
        verdict = str(data.get("verdict") or "")
        if verdict not in ("stagnant", "progressing"):
            return None
        return {"verdict": verdict, "reason": str(data.get("reason") or "")}

    async def judge_event(
        self, *, source: str, summary: str, goal_preview: str
    ) -> dict[str, object] | None:
        """事件立项判读回调:POST /internal/judge-event。降级契约同
        judge_stagnation——不可达/非 200/坏报文/judged=False 一律返回
        None 永不抛(调用方按判读不可用 SKIPPED,不默认立项)。"""
        try:
            async with self._client(self._watchdog_timeout) as client:
                resp = await client.post(
                    "/internal/judge-event",
                    json={"source": source, "summary": summary, "goal_preview": goal_preview},
                )
        except httpx.HTTPError as e:
            log.warning("事件判读回调异常: %s", e)
            return None
        if resp.status_code != 200:
            log.warning("事件判读非 200: HTTP %s", resp.status_code)
            return None
        try:
            data = _obj_dict(resp.json())
        except (ValueError, TypeError, AttributeError) as e:
            log.warning("事件判读响应解析失败: %s", e)
            return None
        if data is None or not data.get("judged"):
            return None  # agent 侧降级(LLM 失败/输出不可解析)
        verdict = str(data.get("verdict") or "")
        if verdict not in ("create", "skip"):
            return None
        return {"verdict": verdict, "reason": str(data.get("reason") or "")}

    async def run_task(self, payload: dict[str, object]) -> dict[str, object] | None:
        """任务编排回调:POST /internal/task-run;长任务独立超时
        (覆盖任务墙钟预算);不可达/非 200/坏报文返回 None(调用方收敛
        INTERRUPTED,可显式恢复),不裸抛。"""
        try:
            async with self._client(self._task_timeout) as client:
                resp = await client.post("/internal/task-run", json=payload)
        except httpx.HTTPError as e:
            log.warning("任务编排回调异常: %s", e)
            return None
        if resp.status_code != 200:
            log.warning("任务编排非 200: HTTP %s", resp.status_code)
            return None
        try:
            return _obj_dict(resp.json())
        except (ValueError, TypeError, AttributeError) as e:
            log.warning("任务编排响应解析失败: %s", e)
            return None

    async def generate_report(self, payload: dict[str, object]) -> dict[str, object] | None:
        """报告生成编排回调:POST /internal/generate-report。
        长任务(agent 侧多轮 LLM+取数),超时取独立预算;不可达/非 200/坏报文
        一律返回 None(调用方收敛 FAILED 终态),不裸抛。"""
        try:
            async with self._client(self._report_timeout) as client:
                resp = await client.post("/internal/generate-report", json=payload)
        except httpx.HTTPError as e:
            log.warning("报告生成回调异常: %s", e)
            return None
        if resp.status_code != 200:
            log.warning("报告生成非 200: HTTP %s", resp.status_code)
            return None
        try:
            return _obj_dict(resp.json())
        except (ValueError, TypeError, AttributeError) as e:
            log.warning("报告生成响应解析失败: %s", e)
            return None
