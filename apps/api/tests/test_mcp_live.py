"""113 真实 MCP server 集成验证:生产 HttpMcpCaller(SDK 2.x 客户端)与
官方 SDK 实现的真实 server 全方法面闭环——109 的既有测试全走 mock,协议的
真实握手、真实分帧、真实 server 头语义从未验证;adopt-mcp-sdk2 起客户端
与 server 同为官方栈(双向一致性,「自我一致性」盲区消除)。

双形态(adopt-mcp-sdk2 spec delta):stateless(2026-07-28 现代形态,
server/discover 无会话直连)与 stateful(旧握手时代形态,initialize 兼容)。

skip-safe:mcp 包缺失或服务拉起失败 → pytest.skip(环境事实探测,不假失败)。
"""

import asyncio
import contextlib
import socket
from collections.abc import AsyncIterator
from typing import Any

import pytest

from kongming.mcp.client import HttpMcpCaller

_TOOL_NAME = "echo_tool"
_RESOURCE_URI = "data://greeting"
_PROMPT_NAME = "greet_prompt"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _build_server(stateless: bool) -> tuple[Any, Any]:
    """官方 SDK 真实 MCP server:一个工具/资源/prompt;形态由 stateless 决定。"""
    from mcp.server.mcpserver import MCPServer

    server = MCPServer("kongming-live-verify")

    @server.tool()
    async def echo_tool(text: str) -> str:  # pyright: ignore[reportUnusedFunction]  # 装饰器注册面
        """回显入参(真实往返断言载体)。"""
        return f"echo:{text}"

    @server.resource(_RESOURCE_URI)
    async def greeting() -> str:  # pyright: ignore[reportUnusedFunction]
        return "hello-from-live-resource"

    @server.prompt()
    async def greet_prompt(name: str) -> str:  # pyright: ignore[reportUnusedFunction]
        return f"请向 {name} 问候"

    app = server.streamable_http_app(stateless_http=stateless, json_response=True)
    return app, server.session_manager


async def _serve(app: Any, port: int) -> None:
    """uvicorn 完整承载 app(含 lifespan=session_manager.run)——生命周期
    进出都在 uvicorn 任务内(任务亲和;手动跨任务 enter/exit 会炸)。"""
    import uvicorn

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    await uvicorn.Server(config).serve()


@pytest.fixture()
async def live_mcp(request: pytest.FixtureRequest) -> AsyncIterator[tuple[HttpMcpCaller, str]]:
    """parametrize on request.param=(stateless,);uvicorn task 自持生命周期。"""
    stateless = request.param
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    app, _session_manager = _build_server(stateless)
    task = asyncio.create_task(_serve(app, port))
    caller = HttpMcpCaller(timeout_sec=10)
    try:
        yield_ = False
        # 就绪判定=TCP 可连(无状态端点对 GET 挂起不回,不能 HTTP 探测)
        for _ in range(50):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                    yield_ = True
                    break
            except OSError:
                await asyncio.sleep(0.1)
        if not yield_:
            pytest.skip("本地 MCP server 拉起失败(端口/环境问题)")
        yield caller, f"{base}/mcp"
    finally:
        task.cancel()
        with contextlib.suppress(BaseException):  # 结束清理:取消噪音不入门禁
            await task


@pytest.mark.parametrize("live_mcp", [True, False], indirect=True)
async def test_probe_discovers_all_three_primitives(
    live_mcp: tuple[HttpMcpCaller, str],
) -> None:
    """probe:双形态 server 全部真实可达(无状态=modern discover 直连;
    有态=initialize 握手兼容)——跨修订协商语义的实站验证。"""
    caller, endpoint = live_mcp
    result = await caller.probe(endpoint=endpoint, headers={})
    assert result.failures == {}
    tools = {t.name for t in result.tools}
    assert _TOOL_NAME in tools
    assert {r.uri for r in result.resources} == {_RESOURCE_URI}
    assert {p.name for p in result.prompts} == {_PROMPT_NAME}


@pytest.mark.parametrize("live_mcp", [True, False], indirect=True)
async def test_call_tool_real_roundtrip(live_mcp: tuple[HttpMcpCaller, str]) -> None:
    """call:真实 server 执行回显工具,参数与返回真实往返(双形态)。"""
    caller, endpoint = live_mcp
    out = await caller.call(
        endpoint=endpoint, headers={}, tool_name=_TOOL_NAME, args={"text": "kongming-113"}
    )
    assert out["is_error"] is False
    assert out["truncated"] is False
    assert "echo:kongming-113" in out["content"]


@pytest.mark.parametrize("live_mcp", [True, False], indirect=True)
async def test_read_resource_and_get_prompt_real(live_mcp: tuple[HttpMcpCaller, str]) -> None:
    """read_resource/get_prompt:真实读取内容与 prompt 消息(双形态)。"""
    caller, endpoint = live_mcp
    res = await caller.read_resource(endpoint=endpoint, headers={}, uri=_RESOURCE_URI)
    assert "hello-from-live-resource" in res["content"]
    prompt = await caller.get_prompt(
        endpoint=endpoint, headers={}, name=_PROMPT_NAME, arguments={"name": "隆中"}
    )
    assert "隆中" in prompt["text"]
