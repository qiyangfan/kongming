"""Bot 的无状态 MCP 入站端点。

ASGI 中间件先做 1 MB 请求体闸(超限先回 413),再校验 api-key;鉴权后只公开
该 key 绑定 Bot 的单个工具,调用时继续校验工具名、代理身份、护栏、配额与
审计。客户端版本经 `server/discover` 或 `initialize` 协商。
"""

from __future__ import annotations

import logging
from typing import Any, cast

import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import MCPError
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from kongming.bot.service import ApiKeyInvalid, BotNotFound, BotService

log = logging.getLogger(__name__)

_BODY_MAX_BYTES = 1 * 1024 * 1024  # 与 trigger webhook 同值(免登录端点统一闸)
_QUESTION_MAX_LEN = 2000  # 与 web 端 BotChatIn max_length 同闸(创建者配额保护)


class _GateAuthMiddleware:
    """1MB 闸(未认证先拒)→ api-key 鉴权(401)→ 放行 SDK 路由。

    绑定解析绑定请求级会话(BotService 经 build_bot_service_with_session
    与 web 面同源装配),session 生命周期覆盖整个 SDK 请求处理(tools/call
    的 Bot 运行在内,与旧 Depends 形态同语义)。"""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        # 根级 Mount("")下本链收到全部未匹配路径:非 /mcp 保持 404 形态
        # (不进闸不进鉴权;Starlette Mount 前缀不匹配裸 /mcp,307 面不可接受)
        if scope["path"].rstrip("/") != "/mcp":
            await _json(send, 404, {"detail": "Not Found"})
            return
        headers = Headers(scope=scope)
        body = await _read_body(headers, receive, send)
        if body is None:  # 已回 413,或客户端提前断开(无需响应)
            return
        key = _extract_key(headers.get("authorization"), headers.get("x-api-key"))
        if not key:
            await _json(send, 401, {"detail": "API_KEY_REQUIRED"})
            return
        await self._authorize_and_forward(scope, body, send, key)

    async def _authorize_and_forward(self, scope: Scope, body: bytes, send: Send, key: str) -> None:
        """api-key 解析后把 key 信息挂 scope state,并以重放 body 调用 SDK 路由。"""
        from kongming.bot.service_factory import build_bot_service_with_session
        from kongming.db import get_sessionmaker

        async with get_sessionmaker()() as session:
            svc = build_bot_service_with_session(session)
            try:
                key_info = await svc.resolve_api_key(key)
            except ApiKeyInvalid:
                # 无效/撤销/绑定 Bot 停用同形 401(不暴露任何工具清单)
                await _json(send, 401, {"detail": "API_KEY_INVALID"})
                return
            scope.setdefault("state", {})["km_mcp_key"] = {
                "bot_key": str(key_info["bot_key"]),
                "tenant_id": str(key_info["tenant_id"]),
                "svc": svc,
            }
            await self._app(scope, _replay(body), send)


async def _read_body(headers: Headers, receive: Receive, send: Send) -> bytes | None:
    """读入请求体并守住 1 MB 闸;超限或中断返回 None(响应已写出或无需响应)。"""
    # 声明超限:不读 body 直接 413(闸前置于鉴权/解析;未认证客户端不可
    # 借超大 body 消耗服务端内存)
    declared = headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > _BODY_MAX_BYTES:
        await _json(send, 413, {"detail": "PAYLOAD_TOO_LARGE"})
        return None
    # 流式计数实际请求体,超限即返回 413
    body = bytearray()
    while True:
        message = await receive()
        if message["type"] == "http.request":
            body.extend(message.get("body", b""))
            if len(body) > _BODY_MAX_BYTES:
                await _json(send, 413, {"detail": "PAYLOAD_TOO_LARGE"})
                return None
            if not message.get("more_body"):
                return bytes(body)
        elif message["type"] == "http.disconnect":
            return None


def _replay(body: bytes) -> Receive:
    """把已读入的请求体缓存回放给 SDK 路由(body 流只能消费一次),之后报断开。"""
    sent_body = False

    async def receive() -> Message:
        nonlocal sent_body
        if not sent_body:
            sent_body = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    return receive


def _extract_key(authorization: str | None, x_api_key: str | None) -> str | None:
    """X-Api-Key(同款)或 Authorization: Bearer(MCP 客户端惯例)。"""
    if x_api_key:
        return x_api_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


async def _json(send: Send, status: int, payload: dict[str, str]) -> None:
    import json

    body = json.dumps(payload).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})


# ---- SDK tool 回调(key→Bot 绑定语义;装配见 build_mcp_inbound) ----


def _key_info(ctx: Any) -> dict[str, Any]:
    """从请求上下文取中间件解析结果(scope state 单点传递;缺失=装配缺陷)。"""
    request = ctx.request
    if request is None:
        raise MCPError(code=-32603, message="internal: 非 HTTP 载体")
    state_raw: object = request.scope.get("state", {})
    state = cast("dict[str, Any]", state_raw)
    info = state.get("km_mcp_key")
    if not isinstance(info, dict):
        raise MCPError(code=-32603, message="internal: 闸状态缺失")
    # isinstance 收窄产物 dict[Unknown, Unknown],cast 为契约键型
    return cast("dict[str, Any]", info)


def _bot_tool(bot_key: str) -> types.Tool:
    return types.Tool(
        name=f"bot__{bot_key}",
        description=f"Kongming Bot: {bot_key}",
        input_schema={
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "自然语言问题"},
            },
            "required": ["question"],
        },
    )


async def _on_list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
    """tools/list 恰返回该 key 绑定的 Bot(MCP 客户端无法感知其他 key/租户)。"""
    info = _key_info(ctx)
    return types.ListToolsResult(tools=[_bot_tool(str(info["bot_key"]))])


async def _on_call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
    info = _key_info(ctx)
    bot_key = str(info["bot_key"])
    if str(params.name or "") != f"bot__{bot_key}":
        # 其他 key/租户的 Bot 不可见不可调用(同形未知工具)
        raise MCPError(code=-32602, message="未知工具")
    args = params.arguments or {}
    question = str(args.get("question") or "")
    if not question.strip():
        raise MCPError(code=-32602, message="question 为空")
    if len(question) > _QUESTION_MAX_LEN:
        raise MCPError(code=-32602, message=f"question 超 {_QUESTION_MAX_LEN} 字符上限")
    svc = cast("BotService", info["svc"])
    try:
        out = await svc.run(
            bot_key=bot_key,
            question=question,
            channel="mcp",  # 渠道溯源
            tenant_id_hint=str(info["tenant_id"]),
        )
    except BotNotFound:
        raise MCPError(code=-32602, message="未知工具") from None
    except Exception:
        log.warning("mcp tools/call 执行异常", exc_info=True)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text="执行失败,请稍后再试")],
            is_error=True,
        )
    # 失败轮(护栏拦截/助手不可用)透 error 文本+isError——否则客户端
    # 见「成功+空文本」无法排障;error 本是用户向友好文案
    if not out.get("success"):
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=str(out.get("error") or "执行失败"))],
            is_error=True,
        )
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=str(out.get("summary") or ""))],
        is_error=False,
    )


class McpInboundProxy:
    """挂载间接层:session manager 不允许重复 run(SDK 硬约束),而模块级
    单例挂载只发生一次——每次 lifespan 由宿主调 rebuild 重建整链入站 app,
    代理恒指向当前实例(测试反复启停 lifespan 与生产单次 lifespan 同形)。"""

    def __init__(self) -> None:
        self._inner: ASGIApp | None = None

    def rebuild(self) -> Any:
        """重建入站 app,返回新 session_manager(宿主 lifespan enter 它)。"""
        app, session_manager = build_mcp_inbound()
        self._inner = app
        return session_manager

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        inner = self._inner
        if inner is None:  # lifespan 未运行(装配次序缺陷):显式不可用
            await _json(send, 503, {"detail": "MCP_NOT_READY"})
            return
        await inner(scope, receive, send)


def build_mcp_inbound() -> tuple[ASGIApp, Any]:
    """构造无状态 MCP 应用与鉴权包装,返回 session_manager。

    宿主 lifespan 必须进入 session_manager.run;端点为 POST /mcp。
    """
    server = Server(
        "kongming",
        version="1.0",
        on_list_tools=_on_list_tools,
        on_call_tool=_on_call_tool,
    )
    sdk_app = server.streamable_http_app(
        streamable_http_path="/mcp",  # 根级挂载,子路径=完整路径;端点恰为 POST /mcp
        json_response=True,
        stateless_http=True,
        max_request_body_size=_BODY_MAX_BYTES,  # SDK 内建第二道(次序语义由前置中间件保证)
        # 平台端点鉴权=api-key(体系),Host 校验(DNS rebinding 防护)不启用
        # ——与旧手写端点语义一致;防护缺位不构成新暴露面(凭证在头,浏览器
        # 跨站请求不带 key)。显式传 transport_security 同时关闭 SDK 对
        # localhost host 的自动白名单(生产域名 421 面)
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False, allowed_hosts=[], allowed_origins=[]
        ),
    )
    return _GateAuthMiddleware(sdk_app), server.session_manager
