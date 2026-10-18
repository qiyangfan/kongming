"""MCP 出站客户端(028;adopt-mcp-sdk2 收编官方 SDK 2.x)。

协议载体=SDK `streamable_http_client` + `ClientSession`(跨修订协商/SSE 帧
聚合/双响应形态/会话语义由 SDK 承载,替换手写 JSON-RPC 栈);本适配层保留
平台语义:跨修订探测路径(现代 `server/discover` 无握手直连 → 旧修订
initialize 回退)、错误归一(timeout/conn/protocol/remote/unsupported 五类)、
结果语义截断(1MB 附标记)与凭证纪律(headers 明文只在客户端构造瞬时存在,
异常路径不落 headers;不裸抛——任何异常归一 McpCallError)。
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar, cast

import httpx2
import mcp_types as types
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError

from kongming.mcp.models import (
    DiscoveredPrompt,
    DiscoveredResource,
    DiscoveredTool,
    ProbeResult,
    PromptArgument,
)
from kongming.mcp.ports import McpCallError

_CONTENT_CAP = 1 * 1024 * 1024  # 结果内容截断(design D4:截断附标记回灌)
_METHOD_NOT_FOUND = -32601  # JSON-RPC 方法不存在(MCP 原语不支持形态,109)

log = logging.getLogger(__name__)

_T = TypeVar("_T")
_L = TypeVar("_L")


def _classify_mcp_error(e: MCPError) -> McpCallError:
    """SDK 协议错误 → 平台五类归一:方法不存在=unsupported(109,原语不支持
    形态);请求超时(-32001,SDK 逐请求读超时)=timeout;其余=remote。"""
    if e.error.code == _METHOD_NOT_FOUND:
        return McpCallError("unsupported", str(e.error.message)[:300])
    if e.error.code == types.REQUEST_TIMEOUT:
        return McpCallError("timeout", str(e.error.message)[:300])
    return McpCallError("remote", str(e.error.message)[:300])


class HttpMcpCaller:
    """McpCaller 实现(SDK 2.x 适配)。timeout_sec 来自组合根(Settings);
    单连接生命周期=每次调用即开即用(transport→session→时代探测→动作),
    不引入常驻连接池(design D1;高频同步场景再评估)。"""

    def __init__(self, *, timeout_sec: int = 15) -> None:
        self._timeout = timeout_sec

    # ---- 连接生命周期与错误归一 ----

    async def _run(
        self,
        *,
        endpoint: str,
        headers: dict[str, str],
        action: Callable[[ClientSession, types.ServerCapabilities], Awaitable[_T]],
    ) -> _T:
        """建立一次连接并在其上执行 action;全程预算 wait_for 包整个交换
        (R1-A2:传输超时是单次操作间隔,慢滴响应须总预算守「Ns」字面语义)。"""

        async def _inner() -> _T:
            async with httpx2.AsyncClient(
                trust_env=False,  # 出站受控:不采环境代理(SSRF 钉扎/内网回调语义)
                headers=headers,
                timeout=httpx2.Timeout(self._timeout, read=max(self._timeout, 30.0)),
            ) as client:
                async with streamable_http_client(url=endpoint, http_client=client) as (
                    read_stream,
                    write_stream,
                ):
                    async with ClientSession(
                        read_stream, write_stream, read_timeout_seconds=float(self._timeout)
                    ) as session:
                        caps = await _negotiate(session)
                        return await action(session, caps)

        try:
            return await asyncio.wait_for(_inner(), timeout=self._timeout + 1)
        except TimeoutError as e:
            raise McpCallError("timeout", f"全程超 {self._timeout + 1}s") from e
        except McpCallError:
            raise
        except httpx2.InvalidURL as e:
            raise McpCallError("protocol", f"endpoint 非法: {e}") from e
        except MCPError as e:
            # 协议层错误按码归一(方法不存在/读超时/远端拒答)
            raise _classify_mcp_error(e) from e
        except httpx2.TimeoutException as e:
            raise McpCallError("timeout", type(e).__name__) from e
        except httpx2.HTTPError as e:
            # 连接族/传输族未细分残族(HTTPError 基类;InvalidURL 已先行)
            raise McpCallError("conn", type(e).__name__) from e
        except RuntimeError as e:
            # SDK 修订协商失败(modern 无交集/服务器版本不可识)=协议不匹配
            # 归一为同步错误分类(spec delta:SHALL NOT 裸抛协议异常)
            raise McpCallError("protocol", str(e)[:300]) from e
        except BaseExceptionGroup as eg:
            # anyio 任务组把叶子错误打包组(SDK 会话/传输层),拆包归一
            raise _classify_group(eg) from eg
        except Exception as e:  # noqa: BLE001  # 平台不失败:残族归一 remote
            log.debug("mcp 出站残族异常归一 remote", exc_info=True)
            raise McpCallError("remote", type(e).__name__) from e

    # ---- 原语消费(转换 SDK 结果 → 域模型) ----

    @staticmethod
    def _tool_from(t: types.Tool) -> DiscoveredTool:
        read_only = bool(t.annotations is not None and t.annotations.read_only_hint is True)
        # fail-closed:未标注=写(SDK input_schema 必备,缺席已在 wire 校验拦截)
        schema = dict(t.input_schema)
        return DiscoveredTool(
            name=t.name,
            description=str(t.description or ""),
            schema=schema,
            read_only=read_only,
        )

    async def _list_tools(self, session: ClientSession) -> list[DiscoveredTool]:
        result = await session.list_tools()
        return [self._tool_from(t) for t in result.tools if t.name]

    async def _list_resources(self, session: ClientSession) -> list[DiscoveredResource]:
        result = await session.list_resources()
        out: list[DiscoveredResource] = []
        for r in result.resources:
            if not r.uri:
                continue  # 畸形条目跳过(白名单语义下 uri 是唯一键)
            out.append(
                DiscoveredResource(
                    uri=r.uri,
                    name=str(r.name or ""),
                    description=str(r.description or ""),
                    mime_type=str(r.mime_type or ""),
                )
            )
        return out

    async def _list_prompts(self, session: ClientSession) -> list[DiscoveredPrompt]:
        result = await session.list_prompts()
        out: list[DiscoveredPrompt] = []
        for p in result.prompts:
            if not p.name:
                continue  # 畸形条目跳过(按名拉取的唯一键)
            args = tuple(
                PromptArgument(
                    name=a.name,
                    description=str(a.description or ""),
                    required=a.required is True,
                )
                for a in (p.arguments or ())
                if a.name
            )
            out.append(
                DiscoveredPrompt(
                    name=p.name,
                    description=str(p.description or ""),
                    arguments=args,
                )
            )
        return out

    # ---- McpCaller 端口实现 ----

    async def probe(self, *, endpoint: str, headers: dict[str, str]) -> ProbeResult:
        """时代探测 + 三原语并行探测(109 design D4):能力未声明即时短路
        (不发请求);方法不存在(-32601)=不支持计数 0;超时/拒连/协议错=
        结构化失败。tools 是核心原语(能力恒声明,宣告过即须能列):tools/list
        回 -32601 属协议不当行为,按失败处理(防 sync 以 ok=True 原子替换为
        空工具快照)。单连接承载握手+三原语,耗时 ≈ 握手 + max(单原语)。"""

        async def _probe3(session: ClientSession, caps: types.ServerCapabilities) -> ProbeResult:
            probe_failures: dict[str, tuple[str, str]] = {}
            probe_unsupported: set[str] = set()

            async def _one(
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
                    probe_failures[name] = (e.kind, e.detail)
                    return []
                except MCPError as e:
                    # SDK 逐请求错误(读超时/方法不存在)单原语归一,不放大成
                    # 整个 probe 失败(单会话并行探测的隔离边界)
                    converted = _classify_mcp_error(e)
                    if converted.kind == "unsupported" and name != "tools":
                        probe_unsupported.add(name)
                        return []
                    probe_failures[name] = (converted.kind, converted.detail)
                    return []

            tools, resources, prompts = await asyncio.gather(
                _one("tools", True, lambda: self._list_tools(session)),
                _one(
                    "resources", caps.resources is not None, lambda: self._list_resources(session)
                ),
                _one("prompts", caps.prompts is not None, lambda: self._list_prompts(session)),
            )
            return ProbeResult(
                tools=tools,
                resources=resources,
                prompts=prompts,
                failures=probe_failures,
                unsupported=frozenset(probe_unsupported),
            )

        return await self._run(endpoint=endpoint, headers=headers, action=_probe3)

    async def read_resource(
        self, *, endpoint: str, headers: dict[str, str], uri: str
    ) -> dict[str, Any]:
        """resources/read(109):文本形态限定——blob(二进制)拒绝不进观察;
        语义截断同 tools/call(1MB 附标记,原始大小回传)。"""

        async def _read(session: ClientSession, caps: types.ServerCapabilities) -> dict[str, Any]:
            result = await session.read_resource(uri)
            texts: list[str] = []
            for part in result.contents:
                if isinstance(part, types.BlobResourceContents):
                    raise McpCallError("remote", "二进制资源不进观察(文本形态限定)")
                # Blob 已拒,contents 余者皆文本(union 收窄;getattr 防变体演进)
                texts.append(str(getattr(part, "text", "") or ""))
            joined = "\n".join(texts)
            original_size = len(joined.encode("utf-8"))
            truncated = original_size > _CONTENT_CAP
            if truncated:
                joined = joined.encode("utf-8")[:_CONTENT_CAP].decode("utf-8", errors="ignore")
            return {"content": joined, "truncated": truncated, "original_size": original_size}

        return await self._run(endpoint=endpoint, headers=headers, action=_read)

    async def get_prompt(
        self,
        *,
        endpoint: str,
        headers: dict[str, str],
        name: str,
        arguments: dict[str, str],
    ) -> dict[str, Any]:
        """prompts/get 按名渲染(109):管理面只读预览的实时数据源,不落库。"""

        async def _get(session: ClientSession, caps: types.ServerCapabilities) -> dict[str, Any]:
            result = await session.get_prompt(name, arguments)
            texts: list[str] = []
            for msg in result.messages:
                if isinstance(msg.content, types.TextContent):
                    texts.append(str(msg.content.text or ""))
            return {"text": "\n\n".join(texts)}

        return await self._run(endpoint=endpoint, headers=headers, action=_get)

    async def call(
        self,
        *,
        endpoint: str,
        headers: dict[str, str],
        tool_name: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        async def _call(session: ClientSession, caps: types.ServerCapabilities) -> dict[str, Any]:
            result = await session.call_tool(tool_name, args)
            texts = [str(c.text or "") for c in result.content if isinstance(c, types.TextContent)]
            joined = (
                "\n".join(texts)
                if texts
                else json.dumps(
                    result.model_dump(by_alias=True, mode="json", exclude_none=True),
                    ensure_ascii=False,
                )
            )
            # 语义截断(spec「超大结果截断」):截断附标记,原始大小回传
            original_size = len(joined.encode("utf-8"))
            truncated = original_size > _CONTENT_CAP
            if truncated:
                joined = joined.encode("utf-8")[:_CONTENT_CAP].decode("utf-8", errors="ignore")
            return {
                "content": joined,
                "is_error": bool(result.is_error),
                "truncated": truncated,
                "original_size": original_size,
            }

        return await self._run(endpoint=endpoint, headers=headers, action=_call)


async def _negotiate(session: ClientSession) -> types.ServerCapabilities:
    """跨修订探测路径(spec delta):现代 server 声明 2026-07-28 →
    `server/discover` 无会话握手直连;旧修订 server 无此方法(-32601)→
    initialize 握手透明兼容;其余错误上抛由 _run 归一(修订不匹配 =
    RuntimeError → protocol 分类)。"""
    try:
        result = await session.discover()
    except MCPError as e:
        if e.error.code != _METHOD_NOT_FOUND:
            raise
        init = await session.initialize()
        return init.capabilities
    return result.capabilities


def _flatten_group(eg: BaseExceptionGroup[BaseException]) -> list[BaseException]:
    out: list[BaseException] = []
    for x in eg.exceptions:
        if isinstance(x, BaseExceptionGroup):
            out.extend(_flatten_group(cast("BaseExceptionGroup[BaseException]", x)))
        else:
            out.append(x)
    return out


def _classify_group(eg: BaseExceptionGroup[BaseException]) -> McpCallError:
    """任务组异常组拆包归一:叶子按 McpCallError(本适配层,原样透传)/
    MCPError/超时/连接族择最先命中;全 Cancelled 叶子=wait_for 全程预算
    触发的取消,归 timeout。"""
    leaves = _flatten_group(eg)
    for x in leaves:
        if isinstance(x, McpCallError):
            return x
    if leaves and all(isinstance(x, asyncio.CancelledError) for x in leaves):
        return McpCallError("timeout", "全程预算(任务组取消)")
    for x in leaves:
        if isinstance(x, MCPError):
            return _classify_mcp_error(x)
    for x in leaves:
        if isinstance(x, (httpx2.TimeoutException, TimeoutError)):
            return McpCallError("timeout", type(x).__name__)
    for x in leaves:
        if isinstance(x, httpx2.HTTPError):
            return McpCallError("conn", type(x).__name__)
    return McpCallError("remote", type(leaves[0]).__name__ if leaves else "ExceptionGroup")
