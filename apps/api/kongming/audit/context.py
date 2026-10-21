"""审计日志从 contextvar 读取请求客户端 IP，避免由每个端点单独传递。

没有请求上下文的定时任务 IP 为空；由请求创建的后台任务沿用该请求的 IP。
"""

from contextvars import ContextVar

from starlette.types import ASGIApp, Receive, Scope, Send

client_ip_var: ContextVar[str | None] = ContextVar("kongming_client_ip", default=None)


class ClientIpMiddleware:
    """纯 ASGI 中间件:每请求写入 client host(无 client 信息时 None)。

    每请求覆写(set 不 reset)——contextvar 随请求 task 销毁,无跨请求泄漏;
    BaseHTTPMiddleware 会缓冲响应体,纯 ASGI 形态零开销。
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            client = scope.get("client")
            client_ip_var.set(client[0] if client else None)
        await self._app(scope, receive, send)
