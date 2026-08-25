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

from kongming.attachment.router import chat_router as attachment_chat_router
from kongming.attachment.router import internal_router as attachment_internal_router
from kongming.audit.context import ClientIpMiddleware
from kongming.audit.router import router as audit_router
from kongming.auth.router import register_auth_error_handlers
from kongming.auth.router import router as auth_router
from kongming.auth.seed import seed_all
from kongming.billing.router import router as billing_router
from kongming.bot.router import public_router as bot_public_router
from kongming.bot.router import router as bot_router
from kongming.chat.router import router as chat_router
from kongming.config import get_settings
from kongming.datasource.cipher import get_cipher
from kongming.datasource.router import register_datasource_error_handlers
from kongming.datasource.router import router as datasource_router
from kongming.db import get_sessionmaker
from kongming.feedback.router import chat_router as feedback_chat_router
from kongming.feedback.router import router as feedback_router
from kongming.guardrail.router import router as guardrail_router
from kongming.internal.executor import dispose_read_engines
from kongming.internal.meta import router as internal_meta_router
from kongming.internal.router import router as internal_router
from kongming.llmusage.router import internal_router as llmusage_internal_router
from kongming.llmusage.router import router as llmusage_router
from kongming.mcp.inbound import router as mcp_inbound_router
from kongming.mcp.router import router as mcp_router
from kongming.memory.router import router as memory_router
from kongming.rag.router import doc_router as rag_doc_router
from kongming.rag.router import internal_router as rag_internal_router
from kongming.rag.router import router as rag_router
from kongming.semantic.router import router as semantic_router
from kongming.store.router import router as store_router
from kongming.system.router import register_system_error_handlers
from kongming.system.router import router as system_router
from kongming.team.router import router as team_router
from kongming.toolcall.router import chat_router as toolcall_chat_router
from kongming.toolcall.router import internal_router as toolcall_internal_router
from kongming.toolcall.router import router as toolcall_router
from kongming.trigger.router import router as trigger_router
from kongming.trigger.router import start_scheduler as start_trigger_scheduler
from kongming.trigger.router import stop_scheduler as stop_trigger_scheduler
from kongming.trigger.router import webhook_router as trigger_webhook_router
from kongming.workflow.router import router as workflow_router

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


async def _monthly_invoice_loop() -> None:
    """034:月首出账薄壳——每日 04:30 检查,每月 1 日为上月未出账租户
    生成账单(幂等;与 015 归档调度同款独立短会话)。"""
    while True:
        await asyncio.sleep(3600)  # 小时轮(首 tick 立即判)
        try:
            from datetime import UTC, datetime

            now = datetime.now(UTC)
            if now.day != 1 or now.hour < 4:
                continue
            from kongming.auth.repository import SqlOperLogRepo
            from kongming.billing.repository import SqlBillingRepo
            from kongming.billing.service import BillingService

            class _InvOper:
                def __init__(self, s) -> None:  # type: ignore[no-untyped-def]
                    self._r = SqlOperLogRepo(s)

                async def log(self, *, action: str, target: str, detail: dict[str, object]) -> None:
                    await self._r.add(None, action, target, detail)

            async with get_sessionmaker()() as s:
                # R1-S1:出账审计注入(generate 内部 commit 连带落库)
                n = await BillingService(
                    SqlBillingRepo(s), _InvOper(s)
                ).generate_monthly_invoices()
            if n:
                log.info("月首出账完成: %d 条", n)
        except asyncio.CancelledError:
            raise
        except Exception:
            # R1-A2:失败不睡 20h——下一小时轮重试(出账幂等,重试安全;
            # 此前失败即睡到 2 号,该月账单永久缺失)
            log.warning("月首出账异常(下小时轮重试)", exc_info=True)
            continue
        # 每日最多一次(成功出账/非出账日后睡到次日)
        await asyncio.sleep(20 * 3600)


async def _payment_expire_loop() -> None:
    """035:支付单过期兜底薄壳——每日 04 点窗口扫描,CREATED 过期单置
    EXPIRED(惰性过期为主路径,本壳兜底;与 034 出账同款独立短会话+
    失败下小时轮重试)。"""
    while True:
        await asyncio.sleep(3600)
        try:
            from datetime import UTC, datetime

            # 审查循环 B2:窗口放宽到 4 点后全天,失败可当日重试
            # (幂等;成功后 20h 睡眠防重复)
            if datetime.now(UTC).hour < 4:
                continue
            from kongming.auth.repository import SqlOperLogRepo
            from kongming.billing.repository import SqlBillingRepo
            from kongming.billing.service import BillingService

            class _ExpOper:
                def __init__(self, s) -> None:  # type: ignore[no-untyped-def]
                    self._r = SqlOperLogRepo(s)

                async def log(self, *, action: str, target: str, detail: dict[str, object]) -> None:
                    await self._r.add(None, action, target, detail)

            async with get_sessionmaker()() as s:
                n = await BillingService(
                    SqlBillingRepo(s), _ExpOper(s)
                ).expire_overdue_payments()
                await s.commit()
            if n:
                log.info("支付单过期兜底: %d 单置 EXPIRED", n)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("支付单过期兜底异常(下小时轮重试)", exc_info=True)
            continue
        await asyncio.sleep(20 * 3600)


async def _attachment_cleanup_loop() -> None:
    """036⑤:孤儿附件清理薄壳——每日 04 点窗口扫超 24h UPLOADED
    (行+文件双删;独立短会话,失败下小时轮重试,与 035 同款)。"""
    while True:
        await asyncio.sleep(3600)
        try:
            from datetime import UTC, datetime

            # 审查循环 B2:窗口放宽到 4 点后全天,失败可当日重试
            # (幂等;成功后 20h 睡眠防重复)
            if datetime.now(UTC).hour < 4:
                continue
            from kongming.attachment.cleanup import cleanup_orphan_attachments

            async with get_sessionmaker()() as s:
                n = await cleanup_orphan_attachments(s)
            if n:
                log.info("孤儿附件清理: %d 件", n)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("孤儿附件清理异常(下小时轮重试)", exc_info=True)
            continue
        await asyncio.sleep(20 * 3600)


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
    billing_task = asyncio.create_task(_monthly_invoice_loop())  # 034:月首出账薄壳
    expire_task = asyncio.create_task(_payment_expire_loop())  # 035:支付单过期兜底
    attach_task = asyncio.create_task(_attachment_cleanup_loop())  # 036⑤:孤儿附件清理
    start_trigger_scheduler()  # 031:cron 调度薄壳(30s tick,单进程形态)
    try:
        yield
    finally:
        archive_task.cancel()
        billing_task.cancel()
        expire_task.cancel()
        attach_task.cancel()
        # R1-B2:收割防 pending 残留
        await asyncio.gather(
            archive_task, billing_task, expire_task, attach_task,
            return_exceptions=True,
        )
        await stop_trigger_scheduler()
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
app.include_router(rag_doc_router)
app.include_router(feedback_chat_router)
app.include_router(feedback_router)
app.include_router(audit_router)
app.include_router(toolcall_internal_router)
app.include_router(toolcall_chat_router)
app.include_router(toolcall_router)
app.include_router(guardrail_router)
app.include_router(llmusage_internal_router)
app.include_router(llmusage_router)
app.include_router(memory_router)
app.include_router(bot_router)
app.include_router(bot_public_router)
app.include_router(semantic_router)
app.include_router(mcp_router)
app.include_router(mcp_inbound_router)
app.include_router(workflow_router)
app.include_router(attachment_chat_router)
app.include_router(attachment_internal_router)
app.include_router(trigger_router)
app.include_router(trigger_webhook_router)
app.include_router(team_router)
app.include_router(store_router)
app.include_router(billing_router)
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
