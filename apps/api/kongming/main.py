"""Kongming 控制面入口。

路由分两类(ADR-002):
- 用户面 /auth/* /chat/* /admin/* —— 用户 JWT
- 内部面 /internal/* —— 内部服务令牌认证(agent 进程回调;chat 域的在途轮取消
  转发由同类 api 实例调用,同属内部通道)
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.requests import Request as _Req
from starlette.responses import JSONResponse, Response
from starlette.responses import Response as _Resp

from kongming import __version__ as _app_version
from kongming.adminauth.router import register_adminauth_error_handlers
from kongming.adminauth.router import router as admin_auth_router
from kongming.app_registry import bind_app
from kongming.attachment.router import chat_router as attachment_chat_router
from kongming.attachment.router import internal_router as attachment_internal_router
from kongming.audit.context import ClientIpMiddleware
from kongming.audit.router import router as audit_router
from kongming.auth.router import register_auth_error_handlers
from kongming.auth.router import router as auth_router
from kongming.auth.seed import seed_all
from kongming.bot.router import public_router as bot_public_router
from kongming.bot.router import router as bot_router
from kongming.chat.dependencies import install_turn_canceler
from kongming.chat.internal_router import router as chat_internal_router
from kongming.chat.router import router as chat_router
from kongming.config import get_settings
from kongming.connector.router import router as connector_router
from kongming.control.dependencies import get_control_runtime
from kongming.control.router import projection_router as control_projection_router
from kongming.control.router import read_router as control_read_router
from kongming.control.router import router as control_router
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
from kongming.logging_setup import setup_logging
from kongming.mcp.inbound import McpInboundProxy
from kongming.mcp.router import router as mcp_router
from kongming.memory.router import router as memory_router
from kongming.quality.router import router as quality_router
from kongming.rag.router import doc_router as rag_doc_router
from kongming.rag.router import internal_router as rag_internal_router
from kongming.rag.router import router as rag_router
from kongming.report.router import internal_router as report_internal_router
from kongming.report.router import router as report_router
from kongming.semantic.router import router as semantic_router
from kongming.skillpack.router import router as skillpack_router
from kongming.store.router import router as store_router
from kongming.stt.router import router as stt_router
from kongming.system.router import register_system_error_handlers
from kongming.system.router import router as system_router
from kongming.task.router import internal_router as task_internal_router
from kongming.task.router import router as task_router
from kongming.team.router import router as team_router
from kongming.toolcall import registry as tool_registry
from kongming.toolcall.router import chat_router as toolcall_chat_router
from kongming.toolcall.router import internal_router as toolcall_internal_router
from kongming.toolcall.router import router as toolcall_router
from kongming.trace import get_trace_id, new_trace_id, set_trace_id
from kongming.trigger.dependencies import start_scheduler as start_trigger_scheduler
from kongming.trigger.dependencies import stop_scheduler as stop_trigger_scheduler
from kongming.trigger.router import router as trigger_router
from kongming.trigger.router import webhook_router as trigger_webhook_router
from kongming.voice.router import router as voice_router
from kongming.workflow.router import router as workflow_router

log = logging.getLogger(__name__)


async def _daily_archive_loop() -> None:
    """每日 03:00 触发审计归档，逻辑在 run_archive。
    归档异常只记 error 日志,不阻断审计写入;任务可在下次调度时重试。"""
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
        except Exception:  # 调度循环永不退出
            log.exception("每日审计归档失败(下轮重试)")


async def _attachment_cleanup_loop() -> None:
    """孤儿附件清理循环：04 点后每小时扫描超 24h 仍 UPLOADED 的附件，
    行与文件双删(独立短会话,失败下小时轮重试)。"""
    while True:
        await asyncio.sleep(3600)
        try:
            from datetime import UTC, datetime

            # 窗口放宽到 4 点后全天,失败可当日重试
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


async def _task_watchdog_loop() -> None:
    """在途任务看门狗巡检循环:RUNNING/PLANNING 心跳超阈候选 → 单次停滞
    判读(agent 判读端点,不可用即落标记)→ 停滞/二次超窗 epoch 夺权收口。
    独立短会话,失败下轮重试;开关关闭时循环不创建(spec「运行中看门狗监督」)。"""
    from kongming.chat.agent_client import AgentClient
    from kongming.chat.dependencies import get_agent_client
    from kongming.db import get_sessionmaker as _gsm
    from kongming.task.collab_repo import SqlCollabLedgerRepo as _TaskRepo
    from kongming.task.ports import StagnationVerdict
    from kongming.task.watchdog import TaskWatchdog as _TaskSvc

    class _AgentJudge:
        """停滞判读端口适配(组合根绑定;None=判读不可用,由 AgentClient
        降级契约保证,巡检用例落标记后走机械兜底)。"""

        def __init__(self, client: AgentClient) -> None:
            self._client = client

        async def judge(
            self, *, goal: str, events_tail: list[dict[str, object]], stale_sec: int
        ) -> StagnationVerdict | None:
            out = await self._client.judge_stagnation(
                goal=goal, events=events_tail, stale_sec=stale_sec
            )
            if out is None:
                return None
            return StagnationVerdict(verdict=str(out["verdict"]), reason=str(out["reason"]))

    settings = get_settings()
    while True:
        await asyncio.sleep(settings.watchdog_interval_sec)
        try:
            async with _gsm()() as s:
                summary = await _TaskSvc(_TaskRepo(s)).patrol_stagnation(
                    stale_sec=settings.watchdog_stale_sec,
                    judge=_AgentJudge(get_agent_client()),
                )
                await s.commit()
            if summary["candidates"]:
                log.info(
                    "看门狗巡检: 候选=%d 判读=%d 夺权收口=%d",
                    summary["candidates"],
                    summary["judged"],
                    summary["taken_over"],
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # 调度循环永不退出
            log.warning("看门狗巡检异常(下轮重试)", exc_info=True)
            continue


def _warn_websearch_misconfiguration() -> None:
    """websearch 配置组合残缺时的 dev 告警降级（prod 由
    config.fail_closed_checks 拒启）。"""
    misconfig = get_settings().websearch_misconfiguration()
    if misconfig:
        log.warning("websearch:%s", misconfig)


def _warn_rerank_misconfiguration() -> None:
    """精排配置组合残缺时的 dev 告警降级（prod 由
    config.fail_closed_checks 拒启）。"""
    misconfig = get_settings().rerank_misconfiguration()
    if misconfig:
        log.warning("rerank:%s", misconfig)


def _warn_doc_entity_misconfiguration() -> None:
    """实体抽取配置组合残缺时的 dev 告警降级（prod 由
    config.fail_closed_checks 拒启）。"""
    misconfig = get_settings().doc_entity_misconfiguration()
    if misconfig:
        log.warning("doc_entity:%s", misconfig)


def _self_check_and_configure_sqlguard() -> None:
    """SQL 安全引擎启动自检(失败告警降级,不阻断)。"""
    from kongming.sqlfw import guard

    guard.configure(get_settings().sqlguard_mode)
    guard.startup_self_check()


async def _mark_stale_tasks_interrupted() -> None:
    """重启扫描——存量 RUNNING/PLANNING 任务标记 INTERRUPTED(不谎称完成),
    顺带清理挂起超 TTL 的审批步骤(必须经 service:repo 直调会漏掉审批清扫)。"""
    from kongming.db import get_sessionmaker as _gsm
    from kongming.task.collab_repo import SqlCollabLedgerRepo as SqlTaskRepo
    from kongming.task.service import TaskService

    async with _gsm()() as s:
        n = await TaskService(SqlTaskRepo(s)).mark_interrupted_stale()
        await s.commit()
        if n:
            log.info("启动扫描:%d 个存量任务标记 INTERRUPTED(含超时审批清理)", n)


async def _mark_stale_reports_failed() -> None:
    """进程中断滞留 GENERATING 的报告收敛 FAILED(不静默悬挂)。"""
    from kongming.db import get_sessionmaker as _gsm
    from kongming.report.repository import SqlReportRepo

    async with _gsm()() as s:
        nr = await SqlReportRepo(s).mark_interrupted_failed()
        await s.commit()
        if nr:
            log.info("启动扫描:%d 个滞留 GENERATING 的报告标记 FAILED", nr)


async def _startup(session: AsyncSession) -> None:
    """lifespan 启动段:检索配置自检 + 播种 + 存量任务/报告收敛 + 工具与域启停装配。"""
    from kongming.rag.catalog import assert_fulltext_ts_config_available

    # 全文检索必须使用数据库中存在的分词配置；配置无效时阻止启动。
    await assert_fulltext_ts_config_available(session, get_settings().rag_fulltext_ts_config)
    await seed_all(session, get_settings())
    await _mark_stale_tasks_interrupted()
    await _mark_stale_reports_failed()
    _warn_websearch_misconfiguration()  # 开发环境告警；生产环境配置检查会拒绝启动。
    _warn_rerank_misconfiguration()  # 与 WebSearch 使用相同的环境规则。
    _warn_doc_entity_misconfiguration()  # 与 WebSearch 使用相同的环境规则。
    tool_registry.activate(
        tool_registry.build_registry(
            websearch_provider=get_settings().websearch_provider,
            websearch_api_key=get_settings().websearch_api_key,
        )
    )
    # 域启停持久状态装载(重启/多副本以库收敛;无网关静默跳过)
    from kongming.toolcall.dependencies import load_persisted_domain_states

    await load_persisted_domain_states()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
    """启动即播种(幂等,rbac spec「种子数据」);DB 不可达则拒启(控制面 fail-closed)。
    启动时试构造凭证 cipher,非法密钥(坏 base64/非 16/32 字节)同样拒启,
    避免请求期才暴露为 500。teardown:清理只读执行池并取消后台任务。"""
    get_cipher(get_settings().datasource_encrypt_key)
    _self_check_and_configure_sqlguard()
    async with get_sessionmaker()() as session:
        await _startup(session)
    archive_task = asyncio.create_task(_daily_archive_loop())
    attach_task = asyncio.create_task(_attachment_cleanup_loop())
    # 看门狗开关关闭则不创建循环(停用即回到既有收口链行为)
    watchdog_task = (
        asyncio.create_task(_task_watchdog_loop()) if get_settings().watchdog_enabled else None
    )
    start_trigger_scheduler()  # cron 调度循环(30s tick,单进程形态)
    control_runtime = get_control_runtime()
    control_runtime.start()  # 控制面缺席不阻塞:仅启动后台循环

    # 语音侧系统取消端口注入(voice→chat 经窄签名反转;语义=用户显式取消)
    async def _voice_cancel(user_id: str, trace_id: str) -> None:
        from kongming.chat.dependencies import get_agent_client, get_turns_registry
        from kongming.chat.inflight import TurnCanceller
        from kongming.chat.repository import ChatRepository
        from kongming.db import get_sessionmaker

        async with get_sessionmaker()() as session:
            canceller = TurnCanceller(
                agent=get_agent_client(), turns=get_turns_registry(), repo=ChatRepository(session)
            )
            await canceller.cancel(user_id=user_id, trace_id=trace_id)

    install_turn_canceler(_voice_cancel)
    # /mcp 入站走 SDK 会话管理器生命周期(挂载态内建 lifespan 失效,宿主
    # MUST enter session_manager.run,SDK 迁移指南明文);每次 lifespan
    # 重建入站链——manager 禁止重复 run
    mcp_sm_cm = _mcp_inbound_proxy.rebuild().run()
    await mcp_sm_cm.__aenter__()
    try:
        yield
    finally:
        with contextlib.suppress(BaseException):
            await mcp_sm_cm.__aexit__(None, None, None)
        archive_task.cancel()
        attach_task.cancel()
        if watchdog_task is not None:
            watchdog_task.cancel()
        # 收割防 pending 残留
        await asyncio.gather(
            archive_task,
            attach_task,
            return_exceptions=True,
        )
        await stop_trigger_scheduler()
        await control_runtime.stop()
        await dispose_read_engines()


setup_logging(level=get_settings().log_level, json_logs=get_settings().environment == "prod")

app = FastAPI(
    title="Kongming API",
    version=_app_version,
    description="控制面:持凭证、做安全决策(ADR-002)",
    lifespan=lifespan,
)
bind_app(app)  # 非 DI 装配点(如 MCP 入站中间件)经注册表读取依赖覆写
# 大 JSON(尤其内部 schema 图)走传输压缩;SSE 由中间件按 text/event-stream 排除。
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)
# 请求 IP 通过 contextvars 传入操作日志，无需修改各调用点。
app.add_middleware(ClientIpMiddleware)


class _TraceIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: _Req, call_next):  # type: ignore[no-untyped-def]
        # 入站头透传,否则生成一次;chat/用量/审计与响应头用同一 contextvar 值
        tid = request.headers.get("X-Trace-Id") or new_trace_id()
        set_trace_id(tid)
        request.state.trace_id = tid
        response: _Resp = await call_next(request)
        response.headers["X-Trace-Id"] = get_trace_id() or tid
        return response


app.add_middleware(_TraceIdMiddleware)
app.include_router(auth_router)
app.include_router(admin_auth_router)
app.include_router(internal_router)
app.include_router(internal_meta_router)
app.include_router(chat_router)
app.include_router(chat_internal_router)
app.include_router(stt_router)
app.include_router(system_router)
app.include_router(datasource_router)
app.include_router(rag_router)
app.include_router(rag_internal_router)
app.include_router(rag_doc_router)
app.include_router(feedback_chat_router)
app.include_router(quality_router)
app.include_router(feedback_router)
app.include_router(audit_router)
app.include_router(toolcall_internal_router)
app.include_router(toolcall_chat_router)
app.include_router(toolcall_router)
app.include_router(guardrail_router)
app.include_router(llmusage_internal_router)
app.include_router(llmusage_router)
app.include_router(memory_router)
app.include_router(skillpack_router)
app.include_router(bot_router)
app.include_router(bot_public_router)
app.include_router(semantic_router)
app.include_router(mcp_router)
# /mcp 入站=SDK 无状态 streamable-http app+闸/鉴权前置包装
# (不再是 FastAPI 路由;代理间接层供 lifespan 期重建——session manager
# 不允许重复 run,测试反复启停 lifespan 与生产单次同形)
_mcp_inbound_proxy = McpInboundProxy()
app.include_router(workflow_router)
app.include_router(attachment_chat_router)
app.include_router(attachment_internal_router)
app.include_router(trigger_router)
app.include_router(trigger_webhook_router)
app.include_router(team_router)
app.include_router(store_router)
app.include_router(report_router)
app.include_router(report_internal_router)
app.include_router(task_router)
app.include_router(task_internal_router)
app.include_router(connector_router)
app.include_router(control_router)
app.include_router(control_read_router)
app.include_router(control_projection_router)
# 语音通话(ADR-013);根级 Mount 兜底之前注册(新路由须在其前)
app.include_router(voice_router)
register_auth_error_handlers(app)
register_adminauth_error_handlers(app)
register_system_error_handlers(app)
register_datasource_error_handlers(app)


@app.get("/healthz", tags=["ops"])
async def healthz() -> dict[str, str]:
    return {"status": "ok", "app": get_settings().app_name}


# /mcp 入站挂载(根级 Mount(""):Starlette 前缀挂载不匹配
# 裸 /mcp 会 307;根级须是最后一条路由兜住未匹配路径,非 /mcp 由闸层
# 404 守卫保持既有 404 形态)
app.mount("", _mcp_inbound_proxy)


_CONTROL_PUBLIC_PREFIXES = (
    "/healthz",
    "/auth/",
    "/admin-auth/",
    "/internal/",
    "/service-read/",
    "/mcp",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/favicon.ico",
)


@app.middleware("http")
async def guard_control_security(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """managed 且无有效安全类快照时,拒绝受保护操作(失败关闭,不看 stale)。"""
    runtime = get_control_runtime()
    if not runtime.managed:
        return await call_next(request)
    path = request.url.path
    if path.startswith(_CONTROL_PUBLIC_PREFIXES):
        return await call_next(request)
    if not await runtime.security_available():
        return JSONResponse(
            status_code=503, content={"detail": "control security context unavailable"}
        )
    return await call_next(request)


@app.middleware("http")
async def guard_internal_channel(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """/internal/* 必须带内部令牌；缺失/错误直接 403。
    注意：middleware 中抛 HTTPException 不会被异常处理器捕获(会变 500),须返回响应。"""
    if request.url.path.startswith("/internal"):
        token = request.headers.get("X-Internal-Token", "")
        expected = get_settings().internal_token
        if not expected or token != expected:
            return JSONResponse(status_code=403, content={"detail": "internal channel closed"})
    return await call_next(request)
