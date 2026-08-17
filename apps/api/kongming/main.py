"""Kongming 控制面入口。

路由分两类(ADR-002):
- 用户面 /auth/* /chat/* /admin/* —— 用户 JWT
- 内部面 /internal/* —— 仅限 agent 进程调用,内部服务令牌认证
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse

from kongming.auth.router import register_auth_error_handlers
from kongming.auth.router import router as auth_router
from kongming.auth.seed import seed_all
from kongming.config import get_settings
from kongming.db import get_sessionmaker
from kongming.internal.router import router as internal_router


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """启动即播种(幂等,rbac spec「种子数据」);DB 不可达则拒启(控制面 fail-closed)。"""
    async with get_sessionmaker()() as session:
        await seed_all(session, get_settings())
    yield


app = FastAPI(
    title="Kongming API",
    version="0.1.0",
    description="控制面:持凭证、做安全决策(ADR-002)",
    lifespan=lifespan,
)
app.include_router(auth_router)
app.include_router(internal_router)
register_auth_error_handlers(app)


@app.get("/healthz", tags=["ops"])
async def healthz() -> dict[str, str]:
    return {"status": "ok", "app": get_settings().app_name}


@app.middleware("http")
async def guard_internal_channel(request: Request, call_next):  # type: ignore[no-untyped-def]
    """/internal/* 必须带内部令牌;缺失/错误直接 403(命名:守门,而非拒绝未知)。
    注意:middleware 中抛 HTTPException 不会被异常处理器捕获(会变 500),须返回响应。"""
    if request.url.path.startswith("/internal"):
        token = request.headers.get("X-Internal-Token", "")
        expected = get_settings().internal_token
        if not expected or token != expected:
            return JSONResponse(status_code=403, content={"detail": "internal channel closed"})
    return await call_next(request)
