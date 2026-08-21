"""Kongming 控制面入口。

路由分两类(ADR-002):
- 用户面 /auth/* /chat/* /admin/* —— 用户 JWT
- 内部面 /internal/* —— 仅限 agent 进程调用,内部服务令牌认证
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse

from kongming.audit.context import ClientIpMiddleware
from kongming.audit.router import router as audit_router
from kongming.auth.router import register_auth_error_handlers
from kongming.auth.router import router as auth_router
from kongming.auth.seed import seed_all
from kongming.chat.router import router as chat_router
from kongming.config import get_settings
from kongming.datasource.cipher import get_cipher
from kongming.datasource.router import register_datasource_error_handlers
from kongming.datasource.router import router as datasource_router
from kongming.db import get_sessionmaker
from kongming.feedback.router import chat_router as feedback_chat_router
from kongming.feedback.router import router as feedback_router
from kongming.internal.executor import dispose_read_engines
from kongming.internal.meta import router as internal_meta_router
from kongming.internal.router import router as internal_router
from kongming.rag.router import internal_router as rag_internal_router
from kongming.rag.router import router as rag_router
from kongming.system.router import register_system_error_handlers
from kongming.system.router import router as system_router

log = logging.getLogger(__name__)


async def _daily_archive_loop() -> None:
    """每日 03:00 归档薄壳(015):调度归调度,逻辑全在 run_archive(可测)。
    一切异常仅 error 日志——归档失败不阻断审计写入(宪法 Article III;
    归档幂等,下轮重试)。"""
    from kongming.audit.archive import run_archive

    while True:
        try:
            now = datetime.now(UTC)
            next_run = (now + timedelta(days=1)).replace(hour=3, minute=0, second=0, microsecond=0)
            await asyncio.sleep((next_run - now).total_seconds())
            settings = get_settings()
            async with get_sessionmaker()() as session:
                result = await run_archive(
                    session,
                    days=settings.audit_archive_days,
                    archive_dir=settings.audit_archive_dir,
                )
            log.info("每日审计归档完成: %s", result)
        except Exception:  # noqa: BLE001  # 调度循环永不退出
            log.error("每日审计归档失败(下轮重试)", exc_info=True)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """启动即播种(幂等,rbac spec「种子数据」);DB 不可达则拒启(控制面 fail-closed)。
    启动试构造凭证 cipher:非法密钥(坏 base64/非 16/32 字节)即拒启——
    prod 缺配已由 config fail-closed 拦截,非法值同属不可服务状态(R1-S3:
    此前请求期才炸 500)。teardown:只读执行池全量清理(008 接线)+
    每日归档调度任务(015)。"""
    get_cipher(get_settings().datasource_encrypt_key)
    async with get_sessionmaker()() as session:
        await seed_all(session, get_settings())
    archive_task = asyncio.create_task(_daily_archive_loop())
    try:
        yield
    finally:
        archive_task.cancel()
        await asyncio.gather(archive_task, return_exceptions=True)  # R1-B2:收割防 pending 残留
        await dispose_read_engines()


app = FastAPI(
    title="Kongming API",
    version="0.1.0",
    description="控制面:持凭证、做安全决策(ADR-002)",
    lifespan=lifespan,
)
# 015 ip 链路:client host 经 contextvar 横切至操作日志写入(调用点零改动)
app.add_middleware(ClientIpMiddleware)
app.include_router(auth_router)
app.include_router(internal_router)
app.include_router(internal_meta_router)
app.include_router(chat_router)
app.include_router(system_router)
app.include_router(datasource_router)
app.include_router(rag_router)
app.include_router(rag_internal_router)
app.include_router(feedback_chat_router)
app.include_router(feedback_router)
app.include_router(audit_router)
register_auth_error_handlers(app)
register_system_error_handlers(app)
register_datasource_error_handlers(app)


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
