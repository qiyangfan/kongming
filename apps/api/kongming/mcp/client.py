"""MCP 出站客户端(028):手写 JSON-RPC 2.0 over Streamable HTTP
(零新依赖,httpx 主依赖;design D1)。

响应双形态:application/json 直读;text/event-stream 按 SSE 帧聚合
data 行。大小闸:流式读取超 1MB 即中断(截断标记由调用方语义化)。
凭证纪律:headers 明文只在请求组装瞬时存在;异常路径只记状态码/
分类文案,不落 headers。
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, cast

import httpx

from kongming.mcp.models import (
    DiscoveredPrompt,
    DiscoveredResource,
    DiscoveredTool,
    ProbeResult,
    PromptArgument,
)
from kongming.mcp.ports import McpCallError

_CONTENT_CAP = 1 * 1024 * 1024  # 结果内容截断(design D4:截断附标记回灌)
# 传输硬上限(R1 自审):截断发生在解析后(1MB 处截断 JSON 帧必毁结构),
# 传输层须更高硬闸防 DoS——8MB 超即结构化错误(平台不失败)
_TRANSPORT_CAP = 8 * 1024 * 1024
_PROTOCOL_VERSION = "2025-03-26"
_METHOD_NOT_FOUND = -32601  # JSON-RPC 方法不存在(MCP 原语不支持形态,109)

log = logging.getLogger(__name__)


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
        # 113:streamable-http 会话头,按 endpoint 隔离(server 下发才存在;
        # 无状态 server 无头零影响)。实例为每请求新建/组合根单例均安全:
        # 同 endpoint 的会话复用正是规范语义,异 endpoint 天然隔离
        self._sessions: dict[str, str] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}

    async def _rpc(
        self,
        *,
        endpoint: str,
        headers: dict[str, str],
        method: str,
        params: dict[str, Any] | None,
        rpc_id: int,
    ) -> dict[str, Any]:
        payload: dict[str, object] = {"jsonrpc": "2.0", "id": rpc_id, "method": method}
        if params is not None:
            payload["params"] = params
        req_headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            **headers,
        }

        async def _do_request() -> tuple[str, str]:
            """发请求+流式读,返回 (响应文本, content-type)。"""
            # 113 会话透传(streamable-http 规范):initialize 的响应头携带
            # mcp-session-id,后续请求 MUST 带上——按 endpoint 隔离存储,
            # server 不下发头(无状态形态)则不带,零影响
            session_id = self._sessions.get(endpoint)
            if session_id is not None:
                req_headers["Mcp-Session-Id"] = session_id
            async with httpx.AsyncClient(
                trust_env=False,  # 出站受控:不采环境代理(SSRF 钉扎/内网回调语义)
                timeout=httpx.Timeout(self._timeout),
                transport=self._transport,
            ) as client:
                async with client.stream(
                    "POST", endpoint, json=payload, headers=req_headers
                ) as resp:
                    if resp.status_code >= 400:
                        raise McpCallError("remote", f"HTTP {resp.status_code}")
                    new_session = resp.headers.get("mcp-session-id")
                    if new_session:
                        self._sessions[endpoint] = new_session
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
            text, ctype = await asyncio.wait_for(_do_request(), timeout=self._timeout + 1)
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
                if not isinstance(candidate, dict):
                    continue
                typed = cast("dict[str, Any]", candidate)
                if typed.get("id") == rpc_id:
                    body = typed
            if body is None:
                raise McpCallError("protocol", "SSE 帧中无匹配响应")
        else:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as e:
                raise McpCallError("protocol", f"响应非 JSON: {text[:80]}") from e
            if not isinstance(parsed, dict):
                raise McpCallError("protocol", "响应非 JSON-RPC 对象")
            body = cast("dict[str, Any]", parsed)
            if body.get("id") != rpc_id:
                # R1-T2:与 SSE 形态同判据(两形态接受面对齐)
                raise McpCallError("protocol", "响应 id 不匹配")
        if "error" in body:
            # JSON-RPC error 字段契约上为对象;dict | object 联合使 isinstance
            # 收窄产物键型已知(dict[str, Any])
            err: dict[str, Any] | object = body["error"]
            # 预放宽的 object 别名:isinstance 不污染其类型,else 支路 str() 不带 Unknown
            err_text: object = err
            err_d = cast("dict[str, Any]", err) if isinstance(err, dict) else None
            msg = err_d.get("message", "?") if err_d is not None else str(err_text)
            # 方法不存在(-32601)= 原语不支持形态(109,design D4):
            # 独立分类,探测层收敛为「计数 0 不算失败」
            if err_d is not None and err_d.get("code") == _METHOD_NOT_FOUND:
                raise McpCallError("unsupported", str(msg)[:300])
            raise McpCallError("remote", str(msg)[:300])
        result = body.get("result")
        if not isinstance(result, dict):
            raise McpCallError("protocol", "缺 result 字段")
        return cast("dict[str, Any]", result)

    async def _initialize_params(self) -> dict[str, Any]:
        return {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "kongming", "version": "1.0"},
        }

    async def _ensure_session(self, *, endpoint: str, headers: dict[str, str]) -> None:
        """懒握手(113,真实 server 验证暴露):streamable-http 规范要求
        initialize 后携 Mcp-Session-Id,而调用面(client 每请求新建)此前
        对 stateful server 的后续请求恒缺会话头。按 endpoint 加锁防并发
        双重 initialize;已有会话零开销。"""
        if self._sessions.get(endpoint) is not None:
            return
        lock = self._session_locks.setdefault(endpoint, asyncio.Lock())
        async with lock:
            if self._sessions.get(endpoint) is not None:
                return
            await self._rpc(
                endpoint=endpoint,
                headers=headers,
                method="initialize",
                params=await self._initialize_params(),
                rpc_id=1,
            )
            await self._notify_initialized(endpoint, headers)

    async def probe(self, *, endpoint: str, headers: dict[str, str]) -> ProbeResult:
        """initialize 握手 + 三原语并行探测(109,design D4):能力未声明
        即时短路(不发请求);方法不存在(-32601)= 不支持计数 0;超时/拒连/
        协议错 = 结构化失败。「只有工具的简单 server」零感知;三原语并行
        发起,探测耗时 ≈ 握手 + max(单原语)不随原语数线性叠加。"""
        init = await self._rpc(
            endpoint=endpoint,
            headers=headers,
            method="initialize",
            params=await self._initialize_params(),
            rpc_id=1,
        )
        await self._notify_initialized(endpoint, headers)
        caps = init.get("capabilities")
        caps_d = cast("dict[str, Any]", caps) if isinstance(caps, dict) else {}

        probe_failures: dict[str, tuple[str, str]] = {}
        probe_unsupported: set[str] = set()

        async def _probe(
            name: str, declared: bool, fetch: Callable[[], Awaitable[list[Any]]]
        ) -> list[Any]:
            if not declared:
                probe_unsupported.add(name)  # 能力未声明:即时短路不发请求
                return []
            try:
                return await fetch()
            except McpCallError as e:
                if e.kind == "unsupported" and name != "tools":
                    # -32601 方法不存在=可选原语不支持,计数 0(109 design D4)
                    probe_unsupported.add(name)
                    return []
                # tools 是核心原语(能力恒声明,宣告过即须能列):tools/list 回
                # -32601 属协议不当行为,按失败处理——评审修复,原「unsupported」
                # 分类会让 sync 以 ok=True 原子替换为空工具快照(静默清目录)
                probe_failures[name] = (e.kind, e.detail)
                return []

        tools, resources, prompts = await asyncio.gather(
            _probe("tools", True, lambda: self.list_tools(endpoint=endpoint, headers=headers)),
            _probe(
                "resources",
                "resources" in caps_d,
                lambda: self.list_resources(endpoint=endpoint, headers=headers),
            ),
            _probe(
                "prompts",
                "prompts" in caps_d,
                lambda: self.list_prompts(endpoint=endpoint, headers=headers),
            ),
        )
        return ProbeResult(
            tools=cast("list[DiscoveredTool]", tools),
            resources=cast("list[DiscoveredResource]", resources),
            prompts=cast("list[DiscoveredPrompt]", prompts),
            failures=probe_failures,
            unsupported=frozenset(probe_unsupported),
        )

    async def _notify_initialized(self, endpoint: str, headers: dict[str, str]) -> None:
        """R1-A6:initialized 通告(fire-and-forget)——官方 SDK 有态 server
        在通告前可能拒绝后续请求;无态 server 回 202/404/405 皆容忍。
        会话头(Mcp-Session-Id)不协商(v1 无态形态,design D5)。"""
        payload = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        try:
            async with httpx.AsyncClient(
                trust_env=False,  # 出站受控:不采环境代理(SSRF 钉扎/内网回调语义)
                timeout=httpx.Timeout(self._timeout),
                transport=self._transport,
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

    async def list_tools(self, *, endpoint: str, headers: dict[str, str]) -> list[DiscoveredTool]:
        await self._ensure_session(endpoint=endpoint, headers=headers)
        result = await self._rpc(
            endpoint=endpoint,
            headers=headers,
            method="tools/list",
            params=None,
            rpc_id=2,
        )
        raw = result.get("tools")
        if not isinstance(raw, list):
            raise McpCallError("protocol", "tools/list 缺 tools 数组")
        out: list[DiscoveredTool] = []
        for raw_item in cast("list[object]", raw):
            # isinstance 收窄产物键型未知,收窄键型后再判 name(仅类型断言)
            if not isinstance(raw_item, dict) or not cast("dict[str, object]", raw_item).get(
                "name"
            ):
                continue  # 畸形条目跳过(宁缺毋滥,不炸整表)
            item = cast("dict[str, Any]", raw_item)
            hints = item.get("annotations")
            hints_d = cast("dict[str, Any]", hints) if isinstance(hints, dict) else None
            read_only = bool(
                hints_d is not None and hints_d.get("readOnlyHint") is True
            )  # fail-closed:未标注=写
            schema = item.get("inputSchema")
            out.append(
                DiscoveredTool(
                    name=str(item["name"]),
                    description=str(item.get("description") or ""),
                    schema=cast("dict[str, Any]", schema)
                    if isinstance(schema, dict)
                    else {"type": "object"},
                    read_only=read_only,
                )
            )
        return out

    async def list_resources(
        self, *, endpoint: str, headers: dict[str, str]
    ) -> list[DiscoveredResource]:
        """resources/list 发现(109):畸形条目跳过(宁缺毋滥),uri 必须非空。"""
        await self._ensure_session(endpoint=endpoint, headers=headers)
        result = await self._rpc(
            endpoint=endpoint,
            headers=headers,
            method="resources/list",
            params=None,
            rpc_id=4,
        )
        raw = result.get("resources")
        if not isinstance(raw, list):
            raise McpCallError("protocol", "resources/list 缺 resources 数组")
        out: list[DiscoveredResource] = []
        for raw_item in cast("list[object]", raw):
            if not isinstance(raw_item, dict):
                continue
            item = cast("dict[str, Any]", raw_item)
            uri = item.get("uri")
            if not isinstance(uri, str) or not uri:
                continue  # 畸形条目跳过(白名单语义下 uri 是唯一键)
            out.append(
                DiscoveredResource(
                    uri=uri,
                    name=str(item.get("name") or ""),
                    description=str(item.get("description") or ""),
                    mime_type=str(item.get("mimeType") or ""),
                )
            )
        return out

    async def list_prompts(
        self, *, endpoint: str, headers: dict[str, str]
    ) -> list[DiscoveredPrompt]:
        """prompts/list 发现(109):仅清单(name/description/arguments)。"""
        await self._ensure_session(endpoint=endpoint, headers=headers)
        result = await self._rpc(
            endpoint=endpoint,
            headers=headers,
            method="prompts/list",
            params=None,
            rpc_id=5,
        )
        raw = result.get("prompts")
        if not isinstance(raw, list):
            raise McpCallError("protocol", "prompts/list 缺 prompts 数组")
        out: list[DiscoveredPrompt] = []
        for raw_item in cast("list[object]", raw):
            if not isinstance(raw_item, dict):
                continue
            item = cast("dict[str, Any]", raw_item)
            pname = item.get("name")
            if not isinstance(pname, str) or not pname:
                continue  # 畸形条目跳过(按名拉取的唯一键)
            args_raw = item.get("arguments")
            args: list[PromptArgument] = []
            if isinstance(args_raw, list):
                for raw_arg in cast("list[object]", args_raw):
                    if not isinstance(raw_arg, dict):
                        continue
                    arg = cast("dict[str, Any]", raw_arg)
                    arg_name = arg.get("name")
                    if not isinstance(arg_name, str) or not arg_name:
                        continue
                    args.append(
                        PromptArgument(
                            name=arg_name,
                            description=str(arg.get("description") or ""),
                            required=arg.get("required") is True,
                        )
                    )
            out.append(
                DiscoveredPrompt(
                    name=pname,
                    description=str(item.get("description") or ""),
                    arguments=tuple(args),
                )
            )
        return out

    async def read_resource(
        self, *, endpoint: str, headers: dict[str, str], uri: str
    ) -> dict[str, Any]:
        """resources/read(109):文本形态限定——blob(二进制)拒绝不进观察;
        语义截断同 tools/call(1MB 附标记,原始大小回传)。"""
        await self._ensure_session(endpoint=endpoint, headers=headers)
        result = await self._rpc(
            endpoint=endpoint,
            headers=headers,
            method="resources/read",
            params={"uri": uri},
            rpc_id=6,
        )
        contents = result.get("contents")
        if not isinstance(contents, list):
            raise McpCallError("protocol", "resources/read 缺 contents 数组")
        texts: list[str] = []
        for raw_part in cast("list[object]", contents):
            if not isinstance(raw_part, dict):
                continue
            part = cast("dict[str, Any]", raw_part)
            if "blob" in part:
                # 内容形态兜底(design Non-goal:二进制资源不进观察)
                raise McpCallError("remote", "二进制资源不进观察(文本形态限定)")
            if "text" in part:
                texts.append(str(part.get("text") or ""))
        joined = "\n".join(texts)
        original_size = len(joined.encode("utf-8"))
        truncated = original_size > _CONTENT_CAP
        if truncated:
            joined = joined.encode("utf-8")[:_CONTENT_CAP].decode("utf-8", errors="ignore")
        return {
            "content": joined,
            "truncated": truncated,
            "original_size": original_size,
        }

    async def get_prompt(
        self,
        *,
        endpoint: str,
        headers: dict[str, str],
        name: str,
        arguments: dict[str, str],
    ) -> dict[str, Any]:
        """prompts/get 按名渲染(109):管理面只读预览的实时数据源,不落库。"""
        await self._ensure_session(endpoint=endpoint, headers=headers)
        result = await self._rpc(
            endpoint=endpoint,
            headers=headers,
            method="prompts/get",
            params={"name": name, "arguments": arguments},
            rpc_id=7,
        )
        messages = result.get("messages")
        if not isinstance(messages, list):
            raise McpCallError("protocol", "prompts/get 缺 messages 数组")
        texts: list[str] = []
        for raw_msg in cast("list[object]", messages):
            if not isinstance(raw_msg, dict):
                continue
            content = cast("dict[str, Any]", raw_msg).get("content")
            if isinstance(content, dict) and cast("dict[str, Any]", content).get("type") == "text":
                texts.append(str(cast("dict[str, Any]", content).get("text") or ""))
        return {"text": "\n\n".join(texts)}

    async def call(
        self,
        *,
        endpoint: str,
        headers: dict[str, str],
        tool_name: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        await self._ensure_session(endpoint=endpoint, headers=headers)
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
            for raw_part in cast("list[object]", content):
                if not isinstance(raw_part, dict):
                    continue
                part = cast("dict[str, Any]", raw_part)
                if part.get("type") == "text":
                    texts.append(str(part.get("text") or ""))
        joined = "\n".join(texts) if texts else json.dumps(result, ensure_ascii=False)
        # 语义截断(spec「超大结果截断」):截断附标记,原始大小回传
        original_size = len(joined.encode("utf-8"))
        truncated = original_size > _CONTENT_CAP
        if truncated:
            joined = joined.encode("utf-8")[:_CONTENT_CAP].decode("utf-8", errors="ignore")
        return {
            "content": joined,
            "is_error": bool(result.get("isError")),
            "truncated": truncated,
            "original_size": original_size,
        }
