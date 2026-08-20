"""Agent 面回调客户端(httpx):控制面 → agent 的唯一通道。

错误语义(nl2sql spec「控制面回调 Agent 面」):不可达/超时/非 200
一律抛 AgentUnavailable,由 service 映射友好错误——不向上抛裸异常、
不泄露内部地址与堆栈。
"""

import logging
from dataclasses import dataclass, field

import httpx

from kongming.config import Settings

log = logging.getLogger(__name__)


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
        payload: dict[str, object] = {
            "trace_id": trace_id,
            "tenant_key": tenant_key,
            "question": question,
            "user_id": user_id,
            "history": history,
            "datasource_key": datasource_key,
        }
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
            data = resp.json()
            reply = AgentReply(
                success=bool(data.get("success")),
                verdict=data.get("verdict"),
                summary=str(data.get("summary") or ""),
                insights=[
                    {"type": str(i.get("type", "")), "text": str(i.get("text", ""))}
                    for i in data.get("insights") or []
                    if isinstance(i, dict)
                ],
                stats=dict(data.get("stats") or {}),
                columns=[str(c) for c in data.get("columns") or []],
                rows=[list(r) for r in data.get("rows") or []],
                sql=str(data.get("sql") or ""),
                retry_count=int(data.get("retry_count") or 0),
                error=data.get("error"),
            )
        except (ValueError, TypeError, AttributeError) as e:
            # agent 返回 200 但报文非预期:同不可达收敛,不裸 500
            log.warning("agent 响应解析失败: %s", e)
            raise AgentUnavailable(f"bad reply: {e}") from e
        return reply

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
