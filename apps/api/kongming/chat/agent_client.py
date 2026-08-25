"""Agent 面回调客户端(httpx):控制面 → agent 的唯一通道。

错误语义(nl2sql spec「控制面回调 Agent 面」):不可达/超时/非 200
一律抛 AgentUnavailable,由 service 映射友好错误——不向上抛裸异常、
不泄露内部地址与堆栈。
"""

import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

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
    insights: list[dict[str, str]] = field(default_factory=list)
    stats: dict[str, object] = field(default_factory=dict)
    columns: list[str] = field(default_factory=list)
    rows: list[list[object]] = field(default_factory=list)
    sql: str = ""
    retry_count: int = 0
    error: str | None = None
    # 035:bot 链路请求内同步采集(纯增;/query_react 响应携带,空=旧版 agent)
    usage_records: list[dict[str, object]] = field(default_factory=list)


def parse_agent_reply(data: dict[str, object]) -> AgentReply:
    """agent 终态报文 → AgentReply(同步响应/流式 result 帧共用,单一定义防漂移)。"""
    verdict = data.get("verdict")
    error = data.get("error")
    insights_raw = data.get("insights")
    stats_raw = data.get("stats")
    columns_raw = data.get("columns")
    rows_raw = data.get("rows")
    usage_raw = data.get("usage_records")
    return AgentReply(
        success=bool(data.get("success")),
        verdict=verdict if isinstance(verdict, str) else None,
        summary=str(data.get("summary") or ""),
        insights=[
            {"type": str(i.get("type", "")), "text": str(i.get("text", ""))}
            for i in insights_raw
            if isinstance(insights_raw, list) and isinstance(i, dict)
        ]
        if isinstance(insights_raw, list)
        else [],
        stats=dict(stats_raw) if isinstance(stats_raw, dict) else {},
        columns=[str(c) for c in columns_raw] if isinstance(columns_raw, list) else [],
        rows=[list(r) for r in rows_raw] if isinstance(rows_raw, list) else [],
        sql=str(data.get("sql") or ""),
        retry_count=retry if isinstance(retry := data.get("retry_count"), int) else 0,
        error=error if isinstance(error, str) else None,
        usage_records=(
            [dict(r) for r in usage_raw if isinstance(r, dict)]
            if isinstance(usage_raw, list)
            else []
        ),
    )


class AgentClient:
    def __init__(self, settings: Settings) -> None:
        self._base = settings.agent_base_url.rstrip("/")
        self._timeout = settings.agent_timeout_sec
        self._headers = {"X-Internal-Token": settings.internal_token or ""}

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
        attachment_ids: list[str] | None = None,  # 030
    ) -> AgentReply:
        """同步问答;react_tools 非 None 即走 ReAct 内核端点(019,tools 注入);
        memory_hint=022 记忆段(空=零回归)。"""
        payload = self._payload(
            trace_id=trace_id,
            tenant_key=tenant_key,
            question=question,
            user_id=user_id,
            history=history,
            datasource_key=datasource_key,
            react_tools=react_tools,
            memory_hint=memory_hint,
            attachment_ids=attachment_ids,
        )
        path = "/query_react" if react_tools is not None else "/query"
        try:
            async with httpx.AsyncClient(
                base_url=self._base, timeout=self._timeout, headers=self._headers
            ) as client:
                resp = await client.post(path, json=payload)
        except httpx.HTTPError as e:
            log.warning("agent 回调异常: %s", e)
            raise AgentUnavailable(str(e)) from e
        if resp.status_code != 200:
            log.warning("agent 回调非 200: HTTP %s", resp.status_code)
            raise AgentUnavailable(f"HTTP {resp.status_code}")
        try:
            reply = parse_agent_reply(resp.json())
        except (ValueError, TypeError, AttributeError) as e:
            # agent 返回 200 但报文非预期:同不可达收敛,不裸 500
            log.warning("agent 响应解析失败: %s", e)
            raise AgentUnavailable(f"bad reply: {e}") from e
        return reply

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
        attachment_ids: list[str] | None = None,  # 030
    ) -> None:
        """流式问答(017):消费 agent SSE 流,逐帧 (event, data_json) 回调;
        错误语义与同步 query 同(不可达/非 200/传输失败一律 AgentUnavailable,
        由调用方收敛 error 终态+FAILED 落库)。019:react_tools 非 None 走
        /query_react/stream(tools 注入,事件含 tool_call)。"""
        payload = self._payload(
            trace_id=trace_id,
            tenant_key=tenant_key,
            question=question,
            user_id=user_id,
            history=history,
            datasource_key=datasource_key,
            react_tools=react_tools,
            memory_hint=memory_hint,
            attachment_ids=attachment_ids,
        )
        path = "/query_react/stream" if react_tools is not None else "/query/stream"
        decoder = SseDecoder()
        try:
            async with httpx.AsyncClient(
                base_url=self._base, timeout=self._timeout, headers=self._headers
            ) as client:
                async with client.stream("POST", path, json=payload) as resp:
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

    @staticmethod
    def _payload(
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
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "trace_id": trace_id,
            "tenant_key": tenant_key,
            "question": question,
            "user_id": user_id,
            "history": history,
            "datasource_key": datasource_key,
        }
        if react_tools is not None:
            payload["tools"] = react_tools  # ReAct 内核:api 按授权注入(019)
        if memory_hint:
            payload["memory_hint"] = memory_hint  # 022
        if attachment_ids:
            payload["attachment_ids"] = attachment_ids  # 030
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
        model: str | None = None,  # 025:Bot 模型覆盖
        channel: str = "chat",  # 025:渠道标记
        collect_usage: bool = False,  # 035:仅 bot 链路置真(响应携带用量明细)
    ) -> AgentReply:
        """ReAct 显式调用(019/025:tools 注入 + Bot 系统提示);错误语义与 query 同。"""
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
        try:
            async with httpx.AsyncClient(
                base_url=self._base, timeout=self._timeout, headers=self._headers
            ) as client:
                resp = await client.post("/query_react", json=payload)
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
        """021 注册表模型名(025;失败返回空——fail-closed 校验语义)。"""
        try:
            async with httpx.AsyncClient(
                base_url=self._base, timeout=10.0, headers=self._headers
            ) as client:
                resp = await client.get("/internal/models")
        except httpx.HTTPError:
            return []
        if resp.status_code != 200:
            return []
        try:
            data = resp.json()
            return [str(m) for m in data.get("models") or []]
        except (ValueError, TypeError):
            return []

    async def extract_memory(self, *, question: str, answer: str) -> list[dict[str, str]]:
        """隐式记忆提取(022):agent /internal/extract-memory 回调;
        任何失败收敛空列表(后台任务静默跳过)。"""
        try:
            async with httpx.AsyncClient(
                base_url=self._base, timeout=30.0, headers=self._headers
            ) as client:
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
            data = resp.json()
            items = data.get("memories") or []
            return [
                {"content": str(i["content"]), "category": str(i.get("category") or "general")}
                for i in items
                if isinstance(i, dict) and isinstance(i.get("content"), str)
            ]
        except (ValueError, TypeError, KeyError) as e:
            log.warning("记忆提取响应解析失败: %s", e)
            return []

    async def rewrite_title(self, *, question: str) -> str | None:
        """会话标题改写(010):后台任务兜底语义——任何失败(不可达/非 200/降级
        应答/坏报文)一律返回 None,永不抛;调用方保持截断标题。"""
        try:
            async with httpx.AsyncClient(
                base_url=self._base, timeout=30.0, headers=self._headers
            ) as client:
                resp = await client.post(
                    "/internal/rewrite-title", json={"question": question}
                )
        except httpx.HTTPError as e:
            log.warning("标题改写回调异常: %s", e)
            return None
        if resp.status_code != 200:
            log.warning("标题改写非 200: HTTP %s", resp.status_code)
            return None
        try:
            data = resp.json()
            if not data.get("rewritten"):
                return None  # agent 侧降级(LLM 失败/输出非法)
            title = str(data.get("title") or "").strip()
        except (ValueError, TypeError, AttributeError) as e:
            log.warning("标题改写响应解析失败: %s", e)
            return None
        return title or None
