"""MCP 出站客户端(028):手写 JSON-RPC 2.0 over Streamable HTTP
(零新依赖,httpx 主依赖;design D1)。

响应双形态:application/json 直读;text/event-stream 按 SSE 帧聚合
data 行。大小闸:流式读取超 1MB 即中断(截断标记由调用方语义化)。
凭证纪律:headers 明文只在请求组装瞬时存在;异常路径只记状态码/
分类文案,不落 headers。
"""

import asyncio
import json
from typing import Any

import httpx
import structlog

from kongming.mcp.models import DiscoveredTool
from kongming.mcp.ports import McpCallError

_CONTENT_CAP = 1 * 1024 * 1024  # 结果内容截断(design D4:截断附标记回灌)
# 传输硬上限(R1 自审):截断发生在解析后(1MB 处截断 JSON 帧必毁结构),
# 传输层须更高硬闸防 DoS——8MB 超即结构化错误(平台不失败)
_TRANSPORT_CAP = 8 * 1024 * 1024
_PROTOCOL_VERSION = "2025-03-26"

log = structlog.get_logger()


def _sse_events(text: str) -> list[str]:
    """SSE 帧解析(迷你版):空行分帧,data: 行聚合(多行 data 按规范
    以 \\n 连接);非 data 行忽略。返回各帧 data 文本。"""
    events: list[str] = []
    data_lines: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if line == "":
            if data_lines:
                events.append("\n".join(data_lines))
                data_lines = []
            continue
        if line.startswith("data:"):
            data_lines.append(line[6:] if line.startswith("data: ") else line[5:])
        # event:/id:/retry:/注释(:开头)一律忽略——本客户端只消费 data
    if data_lines:
        events.append("\n".join(data_lines))
    return events


class HttpMcpCaller:
    """McpCaller 实现。timeout_sec 来自组合根(Settings);
    transport 仅供测试注入(ASGI 假 server 免外部网络)。"""

    def __init__(
        self, *, timeout_sec: int = 15, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._timeout = timeout_sec
        self._transport = transport

    async def _rpc(
        self,
        *,
        endpoint: str,
        headers: dict[str, str],
        method: str,
        params: dict[str, Any] | None,
        rpc_id: int,
    ) -> dict[str, Any]:
        payload = {"jsonrpc": "2.0", "id": rpc_id, "method": method}
        if params is not None:
            payload["params"] = params
        req_headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            **headers,
        }

        async def _do_request() -> tuple[str, str]:
            """发请求+流式读,返回 (响应文本, content-type)。"""
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout), transport=self._transport
            ) as client:
                async with client.stream(
                    "POST", endpoint, json=payload, headers=req_headers
                ) as resp:
                    if resp.status_code >= 400:
                        raise McpCallError("remote", f"HTTP {resp.status_code}")
                    ctype = resp.headers.get("content-type", "")
                    # 流式读+传输硬闸(8MB):超即中断报结构化错误;
                    # 语义截断在内容提取后(_CONTENT_CAP,整洁截断文本)
                    buf = bytearray()
                    async for chunk in resp.aiter_bytes(65536):
                        buf.extend(chunk)
                        if len(buf) > _TRANSPORT_CAP:
                            raise McpCallError(
                                "remote",
                                f"响应超 {_TRANSPORT_CAP // 1024 // 1024}MB 硬上限",
                            )
                    return buf.decode("utf-8", errors="replace"), ctype

        try:
            # R1-A2:全程预算——httpx.Timeout 是单次操作超时,慢滴响应
            # (每 10s 一字节)永不触发;wait_for 包整个交换守「15s」字面语义
            text, ctype = await asyncio.wait_for(
                _do_request(), timeout=self._timeout + 1
            )
        except TimeoutError as e:
            raise McpCallError("timeout", f"全程超 {self._timeout + 1}s") from e
        except httpx.TimeoutException as e:
            raise McpCallError("timeout", f"超 {self._timeout}s") from e
        except httpx.ConnectError as e:
            raise McpCallError("conn", type(e).__name__) from e
        except httpx.InvalidURL as e:
            raise McpCallError("protocol", f"endpoint 非法: {e}") from e
        except httpx.RequestError as e:
            raise McpCallError("conn", type(e).__name__) from e
        if "text/event-stream" in ctype:
            events = _sse_events(text)
            # 取最后一个能解析且 id 匹配的帧(流式形态可能有进度通知帧)
            body: dict[str, Any] | None = None
            for ev in events:
                try:
                    candidate = json.loads(ev)
                except json.JSONDecodeError:
                    continue
                if isinstance(candidate, dict) and candidate.get("id") == rpc_id:
                    body = candidate
            if body is None:
                raise McpCallError("protocol", "SSE 帧中无匹配响应")
        else:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as e:
                raise McpCallError("protocol", f"响应非 JSON: {text[:80]}") from e
            if not isinstance(parsed, dict):
                raise McpCallError("protocol", "响应非 JSON-RPC 对象")
            if parsed.get("id") != rpc_id:
                # R1-T2:与 SSE 形态同判据(两形态接受面对齐)
                raise McpCallError("protocol", "响应 id 不匹配")
            body = parsed
        if "error" in body:
            err = body["error"]
            msg = err.get("message", "?") if isinstance(err, dict) else str(err)
            raise McpCallError("remote", str(msg)[:300])
        result = body.get("result")
        if not isinstance(result, dict):
            raise McpCallError("protocol", "缺 result 字段")
        return result

    async def handshake(self, *, endpoint: str, headers: dict[str, str]) -> int:
        await self._rpc(
            endpoint=endpoint,
            headers=headers,
            method="initialize",
            params={
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "kongming", "version": "1.0"},
            },
            rpc_id=1,
        )
        await self._notify_initialized(endpoint, headers)
        tools = await self.list_tools(endpoint=endpoint, headers=headers)
        return len(tools)

    async def _notify_initialized(
        self, endpoint: str, headers: dict[str, str]
    ) -> None:
        """R1-A6:initialized 通告(fire-and-forget)——官方 SDK 有态 server
        在通告前可能拒绝后续请求;无态 server 回 202/404/405 皆容忍。
        会话头(Mcp-Session-Id)不协商(v1 无态形态,design D5)。"""
        payload = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout), transport=self._transport
            ) as client:
                # 慢滴防护:httpx read 超时是单次 socket 读间隔,server 每次滴
                # <timeout 一字节即永不触发——与 _rpc 同款全程预算(审查循环 F2),
                # 否则敌意 server 可无限挂死握手(创建即同步路径连带挂死)
                await asyncio.wait_for(
                    client.post(
                        endpoint,
                        json=payload,
                        headers={
                            "Accept": "application/json, text/event-stream",
                            "Content-Type": "application/json",
                            **headers,
                        },
                    ),
                    timeout=self._timeout + 1,
                )
        except Exception:  # noqa: BLE001  # 通告失败不阻断握手(无态兼容面)
            log.debug("initialized 通告未被接受(无态形态继续)", exc_info=True)

    async def list_tools(
        self, *, endpoint: str, headers: dict[str, str]
    ) -> list[DiscoveredTool]:
        result = await self._rpc(
            endpoint=endpoint, headers=headers, method="tools/list",
            params=None, rpc_id=2,
        )
        raw = result.get("tools")
        if not isinstance(raw, list):
            raise McpCallError("protocol", "tools/list 缺 tools 数组")
        out: list[DiscoveredTool] = []
        for item in raw:
            if not isinstance(item, dict) or not item.get("name"):
                continue  # 畸形条目跳过(宁缺毋滥,不炸整表)
            hints = item.get("annotations")
            read_only = bool(
                isinstance(hints, dict) and hints.get("readOnlyHint") is True
            )  # fail-closed:未标注=写
            schema = item.get("inputSchema")
            out.append(
                DiscoveredTool(
                    name=str(item["name"]),
                    description=str(item.get("description") or ""),
                    schema=schema if isinstance(schema, dict) else {"type": "object"},
                    read_only=read_only,
                )
            )
        return out

    async def call(
        self,
        *,
        endpoint: str,
        headers: dict[str, str],
        tool_name: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        result = await self._rpc(
            endpoint=endpoint,
            headers=headers,
            method="tools/call",
            params={"name": tool_name, "arguments": args},
            rpc_id=3,
        )
        content = result.get("content")
        texts: list[str] = []
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    texts.append(str(part.get("text") or ""))
        joined = "\n".join(texts) if texts else json.dumps(result, ensure_ascii=False)
        # 语义截断(spec「超大结果截断」):截断附标记,原始大小回传
        original_size = len(joined.encode("utf-8"))
        truncated = original_size > _CONTENT_CAP
        if truncated:
            joined = joined.encode("utf-8")[:_CONTENT_CAP].decode(
                "utf-8", errors="ignore"
            )
        return {
            "content": joined,
            "is_error": bool(result.get("isError")),
            "truncated": truncated,
            "original_size": original_size,
        }
