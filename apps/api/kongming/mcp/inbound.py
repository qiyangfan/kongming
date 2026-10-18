"""MCP 入站端点(028;adopt-mcp-sdk2 收编官方 SDK 2.x 服务端)。

POST /mcp —— Bot as MCP tools。协议载体=SDK 低层 Server(双时代循环:
现代 `server/discover` 无会话直连声明 2026-07-28 修订;旧修订客户端经
initialize 握手兼容),无状态形态(json 响应;会话握手废除为规范本体)。
api-key 鉴权与 1MB 请求体闸以 ASGI 中间件前置于 SDK 路由(次序语义 spec
逐字:闸 MUST 前置于鉴权、notifications 短路与 body 完整缓冲解析——413
恒先于 401,声明超限不读 body,实测超限流式计数不完整缓冲)。key→Bot 绑定
解析在中间件单点完成(结果经 scope state 传递
给 tool 回调,零重复查库);tools/list 恰返回绑定 Bot(tools/call 校验
工具名同形拒,创建者代理身份/护栏/配额/审计不豁免)。
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
_QUESTION_MAX_LEN = 2000  # R1-A3:与 web 端 BotChatIn max_length 同闸(创建者配额保护)


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
        # 声明超限:不读 body 直接 413(闸前置于鉴权/解析;未认证客户端不可
        # 借超大 body 消耗服务端内存)
        declared = headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > _BODY_MAX_BYTES:
            await _json(send, 413, {"detail": "PAYLOAD_TOO_LARGE"})
            return
        # 实测体:流式计数,超限即 413(超大 body 不完整进内存);合法体缓存
        # 回放给 SDK 路由(body 流只能消费一次)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.request":
                body.extend(message.get("body", b""))
                if len(body) > _BODY_MAX_BYTES:
                    await _json(send, 413, {"detail": "PAYLOAD_TOO_LARGE"})
                    return
                if not message.get("more_body"):
                    break
            elif message["type"] == "http.disconnect":
                return
        key = _extract_key(headers.get("authorization"), headers.get("x-api-key"))
        if not key:
            await _json(send, 401, {"detail": "API_KEY_REQUIRED"})
            return
        from kongming.bot.router import build_bot_service_with_session
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
            sent_body = False

            async def replay() -> Message:
                nonlocal sent_body
                if not sent_body:
                    sent_body = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return {"type": "http.disconnect"}

            await self._app(scope, replay, send)


def _extract_key(authorization: str | None, x_api_key: str | None) -> str | None:
    """X-Api-Key(025 同款)或 Authorization: Bearer(MCP 客户端惯例)。"""
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
            channel="mcp",  # 028 渠道溯源
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
    # R1-A7:失败轮(护栏拦截/助手不可用)透 error 文本+isError——否则客户端
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
    单例挂载只发生一次——每次 lifespan 由宿主调 rebuild() 重建整链入站 app,
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
    """组合根:SDK 低层 Server → 无状态 streamable-http app → 闸/鉴权包装。

    返回 (app, session_manager);宿主 lifespan MUST enter
    `session_manager.run()`(挂载态 SDK 内建 lifespan 失效,迁移指南明文)。
    streamable_http_path="/mcp":根级挂载,子路径=完整路径;端点恰为 POST /mcp。
    声明修订=2026-07-28(无状态;现代客户端 server/discover 直连,旧修订
    客户端 initialize 握手兼容由 SDK 双时代循环承载)。
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
        # 平台端点鉴权=api-key(025 体系),Host 校验(DNS rebinding 防护)不启用
        # ——与旧手写端点语义一致;防护缺位不构成新暴露面(凭证在头,浏览器
        # 跨站请求不带 key)。显式传 transport_security 同时关闭 SDK 对
        # localhost host 的自动白名单(生产域名 421 面)
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False, allowed_hosts=[], allowed_origins=[]
        ),
    )
    return _GateAuthMiddleware(sdk_app), server.session_manager
