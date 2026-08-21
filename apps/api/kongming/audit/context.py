"""审计请求上下文(015 ip 链路):client host 经 contextvar 横切透传至
审计写入——操作日志的 ip 列记录是横切关注点,逐端点手传必漏
(调用点散布四域 15+ 处),contextvars 是正解(中间件写入,仓储读取,
调用点零改动)。BackgroundTasks/定时任务无请求上下文自然为 None
(语义正确:非用户请求无 ip);后台任务继承发起请求的上下文副本
(Python 语义,操作由该请求发起,记其 ip 合理)。
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
