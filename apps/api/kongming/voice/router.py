"""语音通话路由:建交通话(牵线)、挂断;文字事件通道(WebSocket)在状态机接线后挂载。

鉴权复用权限点 chat:execute(不新增);WebSocket 带不了自定义请求头,凭一次性
票据鉴权(D8),路由鉴权守卫不覆盖 WebSocket,票据语义有独立测试守护。
"""

import asyncio
import contextlib
import json
import logging
import time
from typing import Annotated, Any, cast

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel

from kongming.auth.deps import CurrentUser, require_perm
from kongming.chat.dependencies import (
    get_tickets_store,
    get_turn_canceler,
    get_voice_registry,
    get_voice_service,
)
from kongming.voice.errors import (
    AdmissionUnavailable,
    ConcurrencyFull,
    OfferInvalid,
    SignalingFailed,
    UserCallConflict,
)
from kongming.voice.models import CallSession
from kongming.voice.service import OFFER_MAX_BYTES, VoiceService
from kongming.voice.state_machine import (
    BrowserSend,
    CallStateMachine,
    Command,
    ModelSend,
    TurnCancel,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/voice", tags=["voice"])


class VoiceEstablishOut(BaseModel):
    """建交通话响应:连接应答、一次性通道票据与通话标识。"""

    answer: str
    ticket: str
    call_id: str


class VoiceCapabilityOut(BaseModel):
    """通话能力探测响应。"""

    enabled: bool


class VoiceHangUpOut(BaseModel):
    """挂断响应。"""

    ok: bool


# 类型别名以大写开头(PEP 8;pyright 禁止小写/下划线变量进类型表达式)
VoiceServiceDep = Annotated[VoiceService, Depends(get_voice_service)]


@router.post(
    "/calls",
    response_model=VoiceEstablishOut,
    dependencies=[Depends(require_perm("chat:execute"))],
)
async def establish_call(
    request: Request,
    user: CurrentUser,
    svc: VoiceServiceDep,
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> VoiceEstablishOut:
    """建交通话:body 为浏览器生成的实时连接请求(SDP offer 文本),原样转发牵线。

    返回连接应答与一次性通道票据;模型服务密钥与内部接入地址不出现在任何
    下发给浏览器的内容中(ADR-013)。
    """
    if not svc.enabled():
        raise HTTPException(status_code=503, detail="VOICE_NOT_ENABLED")
    offer = await _read_offer_limited(request)
    try:
        answer, ticket = await svc.establish(
            user_id=user.user_id, tenant_key=x_tenant_id or "", offer=offer
        )
    except OfferInvalid:
        raise HTTPException(status_code=400, detail="VOICE_OFFER_INVALID") from None
    except UserCallConflict:
        raise HTTPException(status_code=409, detail="VOICE_CALL_EXISTS") from None
    except ConcurrencyFull:
        raise HTTPException(status_code=409, detail="VOICE_BUSY") from None
    except AdmissionUnavailable:
        raise HTTPException(status_code=503, detail="VOICE_UNAVAILABLE") from None
    except SignalingFailed:
        raise HTTPException(status_code=502, detail="VOICE_SIGNALING_FAILED") from None
    return VoiceEstablishOut(answer=answer.decode(), ticket=ticket.token, call_id=ticket.call_id)


async def _read_offer_limited(request: Request) -> bytes:
    """流式读取连接请求并守住大小上限:先读后判会把超大 body 全量留在内存。"""
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > OFFER_MAX_BYTES:
            raise HTTPException(status_code=400, detail="VOICE_OFFER_INVALID")
        chunks.append(chunk)
    return b"".join(chunks)


@router.delete(
    "/calls/{call_id}",
    response_model=VoiceHangUpOut,
    dependencies=[Depends(require_perm("chat:execute"))],
)
async def hang_up(call_id: str, user: CurrentUser, svc: VoiceServiceDep) -> VoiceHangUpOut:
    """挂断:仅本人可挂;结束通话并关闭其文字事件通道(不让已释放的通话继续可用)。"""
    session = svc.calls.get(call_id)
    if session is None or session.user_id != user.user_id:
        raise HTTPException(status_code=404, detail="NOT_FOUND")
    await svc.end_call(session, end_reason="hung_up")
    channel = _CHANNELS.pop(call_id, None)
    if channel is not None:
        with contextlib.suppress(Exception):
            await channel.close(code=1000)
    return VoiceHangUpOut(ok=True)


@router.get(
    "/calls/capability",
    response_model=VoiceCapabilityOut,
    dependencies=[Depends(require_perm("chat:execute"))],
)
async def call_capability(svc: VoiceServiceDep) -> VoiceCapabilityOut:
    """通话能力探测:页面加载时据此决定入口是否出现(未配置即无入口)。

    探测端点本身返回 200 加显式启用位——若它也 503,页面就无法区分
    「未启用」与「探测失败」,入口不出现便无从实现。
    """
    return VoiceCapabilityOut(enabled=svc.enabled())


# ============ 文字事件通道(WebSocket,只做收发转交;D11 适配层薄) ============

_TURN_BIND_WAIT_SEC = 5.0

# 静默通话的墙钟检查粒度:时长上限的收口不依赖模型事件驱动(双方都不出声也要到点收口)
_CALL_TICK_SEC = 30.0

# 在途通道表:挂断端点据此关闭仍开着的文字事件通道(单节点内存表,与通话表同前提)
_CHANNELS: dict[str, WebSocket] = {}


# 类型别名以大写开头(PEP 8;pyright 禁止小写/下划线变量进类型表达式)


@router.websocket("/calls/channel")
async def call_channel(
    websocket: "WebSocket",
    ticket: Annotated[str, Query(min_length=1)],
    svc: VoiceServiceDep,
) -> None:
    """文字事件通道:浏览器把模型会话事件原样转给后端,后端指示转发的再发回。

    凭一次性票据鉴权(token 即凭据);票据缺失/复用/通话不在途一律直接关闭,
    不泄露通话是否存在。协议决策全部在后端状态机,浏览器只搬运。
    """
    registry = get_voice_registry()
    await websocket.accept()
    consumed = await get_tickets_store().consume(ticket)
    if consumed is None:
        await websocket.close(code=4401)
        return
    session = svc.calls.get(consumed.call_id)
    if session is None or session.user_id != consumed.user_id:
        await websocket.close(code=4404)
        return
    _CHANNELS[consumed.call_id] = websocket
    machine = CallStateMachine(
        session,
        binding=_CallTurnBinding(registry, session),
        tool_name="ask_kongming",
        max_call_seconds=svc.max_call_seconds,
        model=svc.model,
    )
    try:
        for command in machine.on_channel_open():
            await _dispatch(machine, websocket, registry, command)
        await _pump_channel(machine, websocket, registry, svc)
    except WebSocketDisconnect:
        pass
    finally:
        _CHANNELS.pop(consumed.call_id, None)
        await _close_out(svc, machine, consumed.call_id)


async def _pump_channel(
    machine: CallStateMachine,
    websocket: "WebSocket",
    registry: Any,
    svc: VoiceService,
) -> None:
    """通道消息循环:接收模型事件并驱动状态机,无事件时按粒度检查墙钟上限。"""
    while True:
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=_CALL_TICK_SEC)
        except TimeoutError:
            # 静默通话(双方都不出声)没有模型事件可驱动墙钟检查:到点主动收口
            if await _expire_if_due(machine, websocket, registry, svc):
                return
            continue
        event = _parse_event(raw)
        if event is None:
            continue
        if await _run_model_event(machine, websocket, registry, svc, event):
            return


def _parse_event(raw: str) -> dict[str, Any] | None:
    """解析模型事件 JSON 对象;非法载荷或非对象返回 None(跳帧不断通道)。"""
    try:
        loaded = json.loads(raw)
    except ValueError:
        return None
    return cast("dict[str, Any]", loaded) if isinstance(loaded, dict) else None


class _CallTurnBinding:
    """签发关联字段时并入通话会话上下文(状态机只要新令牌,注册表需要本人与通话)。"""

    def __init__(self, registry: Any, session: "CallSession") -> None:
        self._registry = registry
        self._session = session

    def new_turn_token(self) -> str:
        return cast(
            "str",
            self._registry.issue(user_id=self._session.user_id, call_id=self._session.call_id),
        )


class _DeadWebSocket:
    """通道断开后的空端点:send 静默丢弃(结束通知无处送达)。"""

    async def send_json(self, payload: dict[str, Any]) -> None:
        return None


async def _close_out(svc: VoiceService, machine: CallStateMachine, call_id: str) -> None:
    """通道收口:结束通知尽力而为;清理关联字段;落通话审计。"""
    dead = _DeadWebSocket()
    for command in machine.on_channel_closed():
        with contextlib.suppress(Exception):
            await _dispatch(machine, dead, get_voice_registry(), command)
    get_voice_registry().drop_call(call_id=call_id)
    await svc.end_call(machine.session, end_reason="channel_closed")


async def _run_model_event(
    machine: CallStateMachine,
    websocket: Any,
    registry: Any,
    svc: VoiceService,
    event: dict[str, Any],
) -> bool:
    """处理一条模型事件;返回是否已收口(到时长上限)。"""
    etype = event.get("type")
    if etype == "error":
        log.warning(
            "语音模型返回错误 call_id=%s error=%s", machine.session.call_id, event.get("error")
        )
    elif etype == "session.updated":
        # 回显即服务端确认:把实际生效的断句参数与音色记进日志,便于核对配置是否被接受
        session_cfg = event.get("session")
        session_cfg = cast("dict[str, Any]", session_cfg) if isinstance(session_cfg, dict) else {}
        audio_cfg = session_cfg.get("audio")
        audio_cfg = cast("dict[str, Any]", audio_cfg) if isinstance(audio_cfg, dict) else {}
        output_cfg = audio_cfg.get("output")
        output_cfg = cast("dict[str, Any]", output_cfg) if isinstance(output_cfg, dict) else {}
        log.info(
            "语音会话配置已确认 call_id=%s turn_detection=%s voice=%s",
            machine.session.call_id,
            session_cfg.get("turn_detection"),
            output_cfg.get("voice") or session_cfg.get("voice"),
        )
        log.info("语音模型事件 call_id=%s type=%s", machine.session.call_id, etype)
    elif etype in (
        "session.created",
        "input_audio_buffer.speech_started",
        "input_audio_buffer.speech_stopped",
        "response.created",
        "response.done",
        "response.output_item.done",
        "response.function_call_arguments.done",
    ):
        log.info("语音模型事件 call_id=%s type=%s", machine.session.call_id, etype)
    for command in machine.on_model_event(event):
        if isinstance(command, BrowserSend) and command.payload.get("type") == "turn.request":
            log.info(
                "语音触发问答 call_id=%s round=%s question_len=%s",
                machine.session.call_id,
                machine.session.rounds,
                len(str(command.payload.get("question") or "")),
            )
            await websocket.send_json(command.payload)
            _spawn_terminal_waiter(machine, websocket, command.payload["token"])
            continue
        if isinstance(command, TurnCancel):
            # 顶替旧轮时的取消异步执行,不阻塞新一轮 turn.request 下发与通道收发
            _spawn_turn_cancel(machine.session.user_id, command.trace_id)
            continue
        await _dispatch(machine, websocket, registry, command)
    return await _expire_if_due(machine, websocket, registry, svc)


async def _expire_if_due(
    machine: CallStateMachine,
    websocket: Any,
    registry: Any,
    svc: VoiceService,
) -> bool:
    """墙钟到期收口:取消在途轮 + 结束通知并关闭通道(4429);审计按达时长上限。"""
    if not machine.tick(time.monotonic()):
        return False
    for command in machine.on_expiration():
        # 结束通知尽力送达;发送失败不改变已到期的终态
        with contextlib.suppress(Exception):
            await _dispatch(machine, websocket, registry, command)
    # 通道断开路径也会调 end_call,但本次收口原因必须先落定,否则审计会记成通道断开
    await svc.end_call(machine.session, end_reason="max_duration")
    with contextlib.suppress(Exception):
        await websocket.close(code=4429)
    return True


async def _cancel_turn(user_id: str, trace_id: str) -> None:
    """调用注入的问答取消端口;失败只记日志不向上传播。"""
    canceler = get_turn_canceler()
    if canceler is None:
        return
    try:
        # pylint: disable-next=not-callable  # 运行期由 lifespan 经 install_turn_canceler 注入
        await canceler(user_id, trace_id)
    except Exception:
        log.exception("在途轮取消失败 trace_id=%s", trace_id)


def _spawn_turn_cancel(user_id: str, trace_id: str) -> None:
    """后台取消被顶替的旧轮(通道收口仍同步等待取消完成)。"""
    task = asyncio.create_task(_cancel_turn(user_id, trace_id))

    def _on_done(t: "asyncio.Task[None]") -> None:
        if t.exception() is not None:
            log.warning("在途轮后台取消异常", exc_info=t.exception())

    task.add_done_callback(_on_done)


def _spawn_terminal_waiter(
    machine: CallStateMachine,
    websocket: "WebSocket",
    token: str,
) -> None:
    """等问答侧绑定与终态,回填命令执行(后台任务;通道断开时静默收场)。"""

    async def _wait() -> None:
        registry = get_voice_registry()
        try:
            trace_id = await registry.wait_bound(token=token, timeout_sec=_TURN_BIND_WAIT_SEC)
            if trace_id is None:
                # 绑定超时/未发起:立即清理占位并按失败回填,避免永等未触发的终态事件
                registry.drop(token=token)
                outcome: object | None = {"verdict": "FAILED"}
            else:
                if machine.pending_token == token:
                    machine.attach_inflight(trace_id=trace_id)
                outcome = await registry.wait_terminal(token=token)
                if outcome is None:
                    outcome = {"verdict": "FAILED"}
            for command in machine.on_turn_terminal_for(
                token,
                cast("dict[str, Any]", outcome),
                original_call_invalid=machine.original_call_invalid,
            ):
                # 通道断开:回填失败不影响已落库结果
                with contextlib.suppress(Exception):
                    await _dispatch(machine, websocket, registry, command)
        finally:
            # 终态已回填或通道已断,关联字段不再需要;及时释放结果行内存
            registry.drop(token=token)

    task = asyncio.create_task(_wait())

    def _on_wait_done(t: "asyncio.Task[None]") -> None:
        if t.exception() is not None:
            log.warning("终态等待任务异常", exc_info=t.exception())

    task.add_done_callback(_on_wait_done)


async def _dispatch(
    machine: CallStateMachine,
    websocket: Any,  # 仅要求 send_json(真通道或断开后的空端点)
    registry: Any,
    command: Command,
) -> None:
    """执行状态机指令:发浏览器、发模型(搬运)、取消在途轮。"""
    if isinstance(command, ModelSend):
        await websocket.send_json({"type": "model.send", "payload": command.payload})
    elif isinstance(command, BrowserSend):
        await websocket.send_json(command.payload)
    else:
        await _cancel_turn(machine.session.user_id, command.trace_id)
