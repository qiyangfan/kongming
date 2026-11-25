"""chat 路由(薄适配层):POST /chat/query + 会话列表/消息历史/删除
+ 重命名/搜索/导出/执行计划+ 流式问答。

首个受权限点保护的端点(chat:execute,rbac spec 权限点校验机制首发);
租户上下文取 X-Tenant-Id 头(multitenant spec),缺省取用户默认租户。
"""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Path,
    Query,
    Response,
)
from fastapi.responses import StreamingResponse
from pydantic import TypeAdapter

from kongming.auth.deps import CurrentUser, require_perm
from kongming.chat.costgate import (
    ChatQuotaExceeded,
    ChatQuotaUnavailable,
    ChatRateLimited,
    quota_warn_var,
)
from kongming.chat.dependencies import (
    get_chat_service,
    get_session_service,
    get_suggestions_service,
)
from kongming.chat.domain_errors import (
    AttachmentNotFound,
    ExplainNoSql,
    InvalidDatasource,
    InvalidTitle,
    MessageNotFound,
    SessionNotFound,
    TenantForbidden,
    TurnNotFound,
    VoiceTurnInvalid,
)
from kongming.chat.finalize import AGENT_UNAVAILABLE_TEXT
from kongming.chat.memory_tasks import extract_memory_task
from kongming.chat.results import ChatOutcome
from kongming.chat.schemas import (
    STREAM_RESPONSES,
    ChatQueryIn,
    ChatQueryOut,
    ChatStreamEvent,
    ExplainIn,
    ExplainOut,
    ExportIn,
    RenameIn,
    RerunIn,
    SessionItemOut,
    SessionMessageOut,
    StreamErrorEvent,
    StreamErrorOut,
    StreamResultEvent,
    SuggestionsOut,
    TurnCancelOut,
    to_chat_query_out,
    to_session_message_out,
)
from kongming.chat.service import ChatService
from kongming.chat.session_service import ChatSessionService
from kongming.chat.stream_runner import OutboundFrame, StreamFailure, StreamHandle
from kongming.chat.suggestions import SuggestionsService
from kongming.chat.title_tasks import rewrite_session_title
from kongming.config import get_settings

log = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

STREAM_HEARTBEAT_SEC = 15.0  # SSE 心跳间隔(慢查询防中间层空闲断连)


@contextmanager
def _chat_query_errors() -> Generator[None]:
    """同步/流式问答共用的前置校验异常→HTTP 翻译(query-streaming spec「防线同构」:
    流式前段校验失败在流开始前以常规 JSON 错误响应)。"""
    try:
        yield
    except TenantForbidden:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    except ChatRateLimited:
        raise HTTPException(status_code=429, detail="CHAT_RATE_LIMITED") from None
    except ChatQuotaExceeded as e:
        raise HTTPException(
            status_code=429,
            detail=f"CHAT_QUOTA_EXCEEDED: {e.warn or 'CHAT_QUOTA'}",
            headers={"X-Quota-Warning": e.warn} if e.warn else None,
        ) from None
    except ChatQuotaUnavailable:
        raise HTTPException(status_code=503, detail="CHAT_QUOTA_UNAVAILABLE") from None
    except AttachmentNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except SessionNotFound:
        raise HTTPException(status_code=404, detail="SESSION_NOT_FOUND") from None
    except InvalidDatasource:
        # 不存在/禁用/越属同形(防 key 探测);与 internal 面同一 detail 码
        raise HTTPException(status_code=400, detail="DATASOURCE_INVALID") from None
    except VoiceTurnInvalid:
        raise HTTPException(status_code=400, detail="VOICE_TURN_INVALID") from None


@router.post(
    "/query",
    response_model=ChatQueryOut,
    dependencies=[Depends(require_perm("chat:execute"))],
    responses={
        429: {
            "description": (
                "请求超过速率或配额限制(detail=CHAT_RATE_LIMITED 或 CHAT_QUOTA_EXCEEDED);"
                "响应头 X-Quota-Warning 可能包含预警。"
            )
        },
        503: {"description": "配额数据不可用时拒绝请求(detail=CHAT_QUOTA_UNAVAILABLE)。"},
    },
)
async def chat_query(
    req: ChatQueryIn,
    user: CurrentUser,
    svc: Annotated[ChatService, Depends(get_chat_service)],
    background: BackgroundTasks,
    response: Response,
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> ChatQueryOut:
    with _chat_query_errors():
        outcome = await svc.query(
            user_id=user.user_id,
            tenant_key=x_tenant_id,
            question=req.question,
            session_id=None if req.session_id is None else str(req.session_id),
            persist=req.persist,
            history=req.history[-5:],  # 防御性截断(agent 侧另有限制)
            datasource_key=req.datasource_key,
            agent_mode=req.agent_mode,
            attachment_ids=[str(a) for a in req.attachment_ids],
        )
    # 标题两阶段生成:首个 SUCCESS 轮后异步改写(守卫/条件写双闸在任务内);
    # persist=false 临时对话无会话可改写;REJECTED/FAILED 轮不触发(违规/失败问句不作素材)
    if outcome.success and req.persist and outcome.session_id is not None:
        background.add_task(rewrite_session_title, outcome.session_id, svc.agent_for_tasks)
        # 隐式记忆(首 SUCCESS 轮同款触发点;best-effort;
        # 显式短路轮排除——防同内容再落一条 IMPLICIT);
        # 携带准备期人工版本快照,捕获失败(None)不挂任务
        if outcome.tenant_id and not outcome.memory_shortcut and outcome.pref_revision is not None:
            background.add_task(
                extract_memory_task,
                user_id=user.user_id,
                tenant_id=outcome.tenant_id,
                question=req.question,
                answer=outcome.summary,
                agent=svc.agent_for_tasks,
                pref_revision=outcome.pref_revision,
            )
    # 达配额预警线回落 X-Quota-Warning(不拒绝;与 网关切面同口径)
    warn = quota_warn_var.get()
    if warn:
        response.headers["X-Quota-Warning"] = warn
    return to_chat_query_out(outcome)


# ============ 对话冷启动建议问题(chat-suggestions) ============


@router.get(
    "/suggestions",
    response_model=SuggestionsOut,
    dependencies=[Depends(require_perm("chat:execute"))],
)
async def chat_suggestions(
    user: CurrentUser,
    svc: Annotated[SuggestionsService, Depends(get_suggestions_service)],
    x_tenant_id: Annotated[str | None, Header()] = None,
    datasource_key: Annotated[str, Query(pattern=r"^[a-z][a-z0-9_]{1,63}$")] = "default",
) -> SuggestionsOut:
    try:
        items = await svc.list_suggestions(
            user_id=user.user_id,
            tenant_key=x_tenant_id,
            datasource_key=datasource_key,
        )
    except TenantForbidden:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    return SuggestionsOut(items=items)


# ============ 流式问答(query-streaming) ============

_EVENT_ADAPTER: TypeAdapter[ChatStreamEvent] = TypeAdapter(ChatStreamEvent)


def _sse_data(data_json: str) -> str:
    """公开流只用默认 SSE 消息(type 在 JSON 内),命名 event: 行不再下发。"""
    return f"data: {data_json}\n\n"


def _prebuilt_error_data(reason_code: Literal["AGENT_UNAVAILABLE", "STREAM_OUTPUT_FAILED"]) -> str:
    """预制错误结尾的 data 载荷(构造期编码,发送路径上不再有可失败的编码)。"""
    out = StreamErrorOut(error=AGENT_UNAVAILABLE_TEXT, reason_code=reason_code)
    return _EVENT_ADAPTER.dump_json(StreamErrorEvent(type="error", data=out)).decode()


_AGENT_UNAVAILABLE_DATA = _prebuilt_error_data("AGENT_UNAVAILABLE")
_STREAM_OUTPUT_FAILED_DATA = _prebuilt_error_data("STREAM_OUTPUT_FAILED")


def _encode_outbound(item: OutboundFrame) -> str:
    """出站元素 → 信封 data 载荷;转换或编码失败按输出失败收尾(调用方处理)。"""
    if isinstance(item, ChatOutcome):
        # 统一出口:正常/业务失败/无需 Agent/已取消的终态都与同步响应同构
        return _EVENT_ADAPTER.dump_json(
            StreamResultEvent(type="result", data=to_chat_query_out(item))
        ).decode()
    if isinstance(item, StreamFailure):
        return _AGENT_UNAVAILABLE_DATA
    return _EVENT_ADAPTER.dump_json(item).decode()


async def _relay_gen(handle: StreamHandle) -> AsyncIterator[str]:
    """SSE 生成器:从后台任务队列转发公开事件;超时发心跳注释行;None 即收束。
    前端断开时本生成器被取消,后台任务(断线续存)继续不受影响。"""
    delivered = False
    saw_terminal = False
    try:
        while True:
            try:
                item = await asyncio.wait_for(handle.queue.get(), timeout=STREAM_HEARTBEAT_SEC)
            except TimeoutError:
                yield ": ping\n\n"
                continue
            if item is None:
                delivered = True
                break
            if isinstance(item, (ChatOutcome, StreamFailure)):
                saw_terminal = True
            try:
                yield _sse_data(_encode_outbound(item))
            except Exception:
                # 结尾转换/编码失败:发预制的输出失败结尾并结束;不改动已保存状态,
                # 不触发第二次落库(该错误只描述答案传输)
                log.exception("公开结尾事件编码失败,按输出失败收尾")
                saw_terminal = True
                yield _sse_data(_STREAM_OUTPUT_FAILED_DATA)
                return
        if not saw_terminal:
            # 队列提前结束且没有任何结尾:输出失败收尾(连接仍可写时恰发一条)
            yield _sse_data(_STREAM_OUTPUT_FAILED_DATA)
    finally:
        if not delivered:
            # 前端已收不到(断开、取消、发送失败):就地丢弃消费任务后续入队的帧直到
            # 终止标记(消费任务任何退出路径都必达它)——落库照常,但不在内存里为
            # 无人接收的帧堆队(结果集大时单轮可达数百 KB,并发遗弃轮次会叠加)
            while True:
                if await handle.queue.get() is None:
                    break


@router.post(
    "/query/stream",
    dependencies=[Depends(require_perm("chat:execute"))],
    response_class=StreamingResponse,
    responses={
        **STREAM_RESPONSES,
        429: {
            "description": (
                "请求超过速率或配额限制(detail=CHAT_RATE_LIMITED 或 CHAT_QUOTA_EXCEEDED);"
                "响应头 X-Quota-Warning 可能包含预警。"
            )
        },
        503: {"description": "配额数据不可用时拒绝请求(detail=CHAT_QUOTA_UNAVAILABLE)。"},
    },
)
async def chat_query_stream(
    req: ChatQueryIn,
    user: CurrentUser,
    svc: Annotated[ChatService, Depends(get_chat_service)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    with _chat_query_errors():
        handle = await svc.start_stream(
            user_id=user.user_id,
            tenant_key=x_tenant_id,
            question=req.question,
            session_id=None if req.session_id is None else str(req.session_id),
            persist=req.persist,
            history=req.history[-5:],
            datasource_key=req.datasource_key,
            agent_mode=req.agent_mode,
            attachment_ids=[str(a) for a in req.attachment_ids],
            voice_turn_token=req.voice_turn_token,
        )
    # 达预警线的 X-Quota-Warning 随流响应头回落(不拒绝)
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    warn = quota_warn_var.get()
    if warn:
        headers["X-Quota-Warning"] = warn
    return StreamingResponse(
        _relay_gen(handle),
        media_type="text/event-stream",
        headers=headers,
    )


@router.post(
    "/query/stream/{trace_id}/cancel",
    response_model=TurnCancelOut,
    dependencies=[Depends(require_perm("chat:execute"))],
)
async def cancel_query_stream(
    trace_id: Annotated[str, Path(min_length=1, max_length=64)],
    user: CurrentUser,
    svc: Annotated[ChatService, Depends(get_chat_service)],
) -> TurnCancelOut:
    """取消一轮在途流式问答;轮次标识取流式响应头 X-Trace-Id。"""
    try:
        settled = await svc.cancel_turn(user_id=user.user_id, trace_id=trace_id)
    except TurnNotFound:
        # 他人轮次、不存在的轮次与已结束未落库的轮次统一 404(不泄露轮次是否存在)
        raise HTTPException(status_code=404, detail="TURN_NOT_FOUND") from None
    return TurnCancelOut(outcome=settled.outcome, message_id=settled.message_id)


# ============ 会话列表/消息历史/删除(web-nav 支撑端点) ============


@router.post(
    "/artifacts/rerun",
    response_model=ChatQueryOut,
    dependencies=[Depends(require_perm("chat:execute"))],
)
async def rerun_artifact(
    req: RerunIn,
    user: CurrentUser,
    svc: Annotated[ChatService, Depends(get_chat_service)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> ChatQueryOut:
    try:
        outcome = await svc.rerun_artifact(
            user_id=user.user_id,
            tenant_key=x_tenant_id,
            message_id=str(req.message_id),
            sql=req.sql,
        )
    except TenantForbidden:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    except MessageNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except InvalidDatasource:
        raise HTTPException(status_code=400, detail="DATASOURCE_INVALID") from None
    return to_chat_query_out(outcome)


@router.get(
    "/sessions",
    response_model=list[SessionItemOut],
    dependencies=[Depends(require_perm("chat:execute"))],
)
async def list_sessions(
    user: CurrentUser,
    svc: Annotated[ChatSessionService, Depends(get_session_service)],
    x_tenant_id: Annotated[str | None, Header()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> list[SessionItemOut]:
    try:
        rows = await svc.list_sessions(
            user_id=user.user_id,
            tenant_key=x_tenant_id,
            limit=limit,
            keyword=q or "",
        )
    except TenantForbidden:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    return [
        SessionItemOut(
            id=s.id,
            title=s.title,
            updated_at=s.updated_at,  # type: ignore[arg-type]
        )
        for s in rows
    ]


@router.patch(
    "/sessions/{session_id}",
    response_model=SessionItemOut,
    dependencies=[Depends(require_perm("chat:execute"))],
)
async def rename_session(
    session_id: uuid.UUID,
    req: RenameIn,
    user: CurrentUser,
    svc: Annotated[ChatSessionService, Depends(get_session_service)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> SessionItemOut:
    try:
        s = await svc.rename_session(
            user_id=user.user_id,
            tenant_key=x_tenant_id,
            session_id=str(session_id),
            title=req.title,
        )
    except TenantForbidden:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    except SessionNotFound:
        # 不存在/越权/跨租户同形 404,不泄露会话存在性(chat spec)
        raise HTTPException(status_code=404, detail="SESSION_NOT_FOUND") from None
    except InvalidTitle:
        raise HTTPException(status_code=400, detail="TITLE_INVALID") from None
    return SessionItemOut(
        id=s.id,
        title=s.title,
        updated_at=s.updated_at,  # type: ignore[arg-type]
    )


@router.get(
    "/sessions/{session_id}/messages",
    response_model=list[SessionMessageOut],
    dependencies=[Depends(require_perm("chat:execute"))],
)
async def session_messages(
    session_id: uuid.UUID,
    user: CurrentUser,
    svc: Annotated[ChatSessionService, Depends(get_session_service)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> list[SessionMessageOut]:
    try:
        msgs = await svc.session_messages(
            user_id=user.user_id,
            tenant_key=x_tenant_id,
            session_id=str(session_id),
        )
    except TenantForbidden:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    except SessionNotFound:
        # 不存在、越权和跨租户请求统一返回 404，避免泄露会话是否存在。
        raise HTTPException(status_code=404, detail="SESSION_NOT_FOUND") from None
    return [to_session_message_out(m) for m in msgs]


@router.delete(
    "/sessions/{session_id}",
    status_code=204,
    dependencies=[Depends(require_perm("chat:execute"))],
)
async def delete_session(
    session_id: uuid.UUID,
    user: CurrentUser,
    svc: Annotated[ChatSessionService, Depends(get_session_service)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> None:
    try:
        await svc.delete_session(
            user_id=user.user_id, tenant_key=x_tenant_id, session_id=str(session_id)
        )
    except TenantForbidden:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    except SessionNotFound:
        raise HTTPException(status_code=404, detail="SESSION_NOT_FOUND") from None


# ============ 结果导出(chat spec「结果导出 CSV/Excel」) ============


@router.post(
    "/export",
    dependencies=[Depends(require_perm("chat:execute"))],
)
async def export_result(
    req: ExportIn,
    user: CurrentUser,
    svc: Annotated[ChatSessionService, Depends(get_session_service)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> Response:
    try:
        content, media_type, ext = await svc.export_result(
            user_id=user.user_id,
            tenant_key=x_tenant_id,
            fmt=req.format,
            columns=req.columns,
            rows=req.rows,
            max_rows=get_settings().export_max_rows,
        )
    except TenantForbidden:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    # 「查询结果-<时间戳>.<ext>」(chat spec);RFC 5987 filename* 携带中文名
    filename = quote(f"查询结果-{datetime.now(UTC):%Y%m%d-%H%M%S}.{ext}")
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


# ============ 执行计划(chat spec「EXPLAIN 执行计划」) ============


@router.post(
    "/explain",
    response_model=ExplainOut,
    dependencies=[Depends(require_perm("chat:execute"))],
)
async def explain_message(
    req: ExplainIn,
    user: CurrentUser,
    svc: Annotated[ChatService, Depends(get_chat_service)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> ExplainOut:
    try:
        view = await svc.explain_message(
            user_id=user.user_id, tenant_key=x_tenant_id, message_id=str(req.message_id)
        )
    except TenantForbidden:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    except MessageNotFound:
        # 不存在/越权/跨租户同形 404,不泄露消息存在性(与会话端点同语义)
        raise HTTPException(status_code=404, detail="MESSAGE_NOT_FOUND") from None
    except ExplainNoSql:
        raise HTTPException(status_code=400, detail="EXPLAIN_NO_SQL") from None
    except InvalidDatasource:
        raise HTTPException(status_code=400, detail="DATASOURCE_INVALID") from None
    return ExplainOut(success=view.success, plan=view.plan, error=view.error)
