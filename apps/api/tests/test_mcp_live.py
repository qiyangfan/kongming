"""113 真实 MCP server 集成验证:生产 HttpMcpCaller 与官方 SDK 实现的
真实 server(HTTP/SSE)全方法面闭环——109 的既有测试全走 mock transport,
协议的真实握手(notify initialized)、SSE 真实分帧、真实 server 头语义
从未验证。真实性的判定标准=server 侧由第三方官方实现(mcp SDK FastMCP),
非手写桩。

skip-safe:mcp 包缺失(未装 dev 依赖)或服务拉起失败 → pytest.skip
(与 test_rag_fulltext_config 的 zhparser 探测同形态),CI 无该依赖不假失败。
"""

import asyncio
import contextlib
import socket
from collections.abc import AsyncIterator

import httpx
import pytest

from kongming.mcp.client import HttpMcpCaller

try:  # dev 依赖(mcp<2)缺失 → 本文件全部 skip(环境事实探测,不假失败)
    from mcp.server.fastmcp import FastMCP
except ImportError as _e:  # pragma: no cover - 取决于环境
    FastMCP = None  # type: ignore[assignment,misc]
    _FASTMCP_IMPORT_ERROR: object | None = _e
else:
    _FASTMCP_IMPORT_ERROR = None

pytestmark = pytest.mark.skipif(
    _FASTMCP_IMPORT_ERROR is not None,
    reason="dev 依赖 mcp<2 未安装(装后真跑)",
)

_TOOL_NAME = "echo_tool"
_RESOURCE_URI = "data://greeting"
_PROMPT_NAME = "greet_prompt"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def _serve(port: int) -> None:
    """官方 SDK 的真实 MCP server(SSE transport):一个工具/资源/prompt。"""
    server = FastMCP("kongming-live-verify", host="127.0.0.1", port=port)

    @server.tool()
    async def echo_tool(text: str) -> str:
        """回显入参(真实往返断言载体)。"""
        return f"echo:{text}"

    @server.resource(_RESOURCE_URI)
    async def greeting() -> str:
        return "hello-from-live-resource"

    @server.prompt()
    async def greet_prompt(name: str) -> str:
        return f"请向 {name} 问候"

    import uvicorn

    # streamable-http 形态(POST 单请求、响应 JSON 或 SSE 流)——与 109 生产
    # client 的请求形态匹配(FastMCP 1.x 的 sse_app 是 GET+POST 双通道旧形态)
    config = uvicorn.Config(
        server.streamable_http_app(), host="127.0.0.1", port=port, log_level="warning"
    )
    await uvicorn.Server(config).serve()


@pytest.fixture()
async def live_mcp() -> AsyncIterator[tuple[HttpMcpCaller, str]]:
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    task = asyncio.create_task(_serve(port))
    caller = HttpMcpCaller(timeout_sec=10)
    try:
        # 轮询至端口可连(服务就绪),整体兜底超时防悬挂
        ready = False
        for _ in range(50):
            try:
                async with httpx.AsyncClient(timeout=0.5) as hc:
                    await hc.get(base)
                ready = True
                break
            except httpx.HTTPError:
                await asyncio.sleep(0.1)
        if not ready:
            pytest.skip("本地 MCP server 拉起失败(端口/环境问题)")
        yield caller, f"{base}/mcp"
    finally:
        task.cancel()
        with contextlib.suppress(BaseException):  # 结束清理:取消噪音不入门禁
            await task


async def test_probe_discovers_all_three_primitives(live_mcp: tuple[HttpMcpCaller, str]) -> None:
    """probe:initialize 真实握手 + 三原语并行探测,全部真实可达。"""
    caller, endpoint = live_mcp
    result = await caller.probe(endpoint=endpoint, headers={})
    assert result.failures == {}
    tools = {t.name for t in result.tools}
    assert _TOOL_NAME in tools
    assert {r.uri for r in result.resources} == {_RESOURCE_URI}
    assert {p.name for p in result.prompts} == {_PROMPT_NAME}


async def test_call_tool_real_roundtrip(live_mcp: tuple[HttpMcpCaller, str]) -> None:
    """call:真实 server 执行回显工具,参数与返回真实往返。"""
    caller, endpoint = live_mcp
    out = await caller.call(
        endpoint=endpoint, headers={}, tool_name=_TOOL_NAME, args={"text": "kongming-113"}
    )
    assert out["is_error"] is False
    assert out["truncated"] is False
    assert "echo:kongming-113" in out["content"]


async def test_read_resource_and_get_prompt_real(live_mcp: tuple[HttpMcpCaller, str]) -> None:
    """read_resource/get_prompt:真实读取内容与 prompt 消息。"""
    caller, endpoint = live_mcp
    res = await caller.read_resource(endpoint=endpoint, headers={}, uri=_RESOURCE_URI)
    assert "hello-from-live-resource" in res["content"]
    prompt = await caller.get_prompt(
        endpoint=endpoint, headers={}, name=_PROMPT_NAME, arguments={"name": "隆中"}
    )
    assert "隆中" in prompt["text"]
