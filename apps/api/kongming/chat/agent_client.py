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


def parse_agent_reply(data: dict[str, object]) -> AgentReply:
    """agent 终态报文 → AgentReply(同步响应/流式 result 帧共用,单一定义防漂移)。"""
    verdict = data.get("verdict")
    error = data.get("error")
    insights_raw = data.get("insights")
    stats_raw = data.get("stats")
    columns_raw = data.get("columns")
    rows_raw = data.get("rows")
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
    ) -> AgentReply:
        payload = self._payload(
            trace_id=trace_id,
            tenant_key=tenant_key,
            question=question,
            user_id=user_id,
            history=history,
            datasource_key=datasource_key,
        )
        try:
            async with httpx.AsyncClient(
                base_url=self._base, timeout=self._timeout, headers=self._headers
            ) as client:
                resp = await client.post("/query", json=payload)
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
    ) -> None:
        """流式问答(017):消费 agent SSE 流,逐帧 (event, data_json) 回调;
        错误语义与同步 query 同(不可达/非 200/传输失败一律 AgentUnavailable,
        由调用方收敛 error 终态+FAILED 落库)。"""
        payload = self._payload(
            trace_id=trace_id,
            tenant_key=tenant_key,
            question=question,
            user_id=user_id,
            history=history,
            datasource_key=datasource_key,
        )
        decoder = SseDecoder()
        try:
            async with httpx.AsyncClient(
                base_url=self._base, timeout=self._timeout, headers=self._headers
            ) as client:
                async with client.stream("POST", "/query/stream", json=payload) as resp:
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
    ) -> dict[str, object]:
        return {
            "trace_id": trace_id,
            "tenant_key": tenant_key,
            "question": question,
            "user_id": user_id,
            "history": history,
            "datasource_key": datasource_key,
        }

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
