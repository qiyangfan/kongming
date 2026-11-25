"""voice 用例层:通话准入、牵线与通话状态机。

领域层零框架依赖:持久化/外存经 ports 端口,配置经只读视图(ADR-002 信任边界:
模型服务密钥只存在于基础设施实现,不进入领域对象)。
"""

import logging
import secrets
import time
import uuid
from dataclasses import dataclass

from kongming.voice.errors import (
    AdmissionUnavailable,
    ConcurrencyFull,
    OfferInvalid,
    SignalingFailed,
    UserCallConflict,
    VoiceNotEnabled,
)
from kongming.voice.models import CallAudit, CallSession, Ticket
from kongming.voice.ports import AdmissionGate, OperLogSink, SignalingGateway, TicketStore

log = logging.getLogger(__name__)

# 牵线连接请求/应答的大小上限(D10):防恶意超大报文,应答另防上游异常回包
OFFER_MAX_BYTES = 128 * 1024
ANSWER_MAX_BYTES = 256 * 1024
TICKET_TTL_SEC = 60


@dataclass(frozen=True)
class VoiceSettingsView:
    """service 依赖的只读配置视图(测试替身可按字段构造)。"""

    voice_api_key: str | None
    voice_realtime_base_url: str | None
    voice_model: str | None
    voice_max_call_seconds: int


class VoiceService:
    def __init__(
        self,
        gate: AdmissionGate,
        gateway: SignalingGateway,
        tickets: TicketStore,
        *,
        operlog: OperLogSink | None = None,
        settings: VoiceSettingsView,
    ) -> None:
        self._gate = gate
        self._gateway = gateway
        self._tickets = tickets
        self._operlog = operlog
        self._s = settings
        self.calls: dict[str, CallSession] = {}

    @property
    def max_call_seconds(self) -> int:
        """部署级单次通话时长上限(状态机定时器据此收口)。"""
        return self._s.voice_max_call_seconds

    @property
    def model(self) -> str:
        """部署配置的实时语音模型名(供会话配置下发使用)。"""
        return self._s.voice_model or ""

    def enabled(self) -> bool:
        """三项语音配置齐备才启用(缺任一项入口不出现、端点 503)。"""
        return bool(
            self._s.voice_api_key and self._s.voice_realtime_base_url and self._s.voice_model
        )

    async def establish(
        self, *, user_id: str, tenant_key: str = "", offer: bytes
    ) -> tuple[bytes, Ticket]:
        """建交通话:准入 → 牵线 → 签票据;返回 (应答, 票据)。

        任一环节失败即回滚已取得的准入占位,不留幽灵占用;上游错误一律收敛为
        SignalingFailed(原文不透传,日志不含请求与应答正文)。
        """
        if not self.enabled():
            raise VoiceNotEnabled("语音通话未启用")
        offer = self._check_offer(offer)
        try:
            user_ok = await self._gate.try_acquire_user(user_id)
        except Exception as e:
            # 准入后端故障 fail-closed(与限流器 fail-open 相反):拒绝建立
            log.warning("语音准入不可用 user=%s: %s", user_id, type(e).__name__)
            raise AdmissionUnavailable("语音服务暂不可用,请稍后再试") from e
        if not user_ok:
            raise UserCallConflict("已有一通在途通话")
        try:
            slot_ok = await self._gate.try_acquire_slot()
        except Exception as e:
            log.warning("语音准入不可用 user=%s: %s", user_id, type(e).__name__)
            await self._gate.release_user(user_id)
            raise AdmissionUnavailable("语音服务暂不可用,请稍后再试") from e
        if not slot_ok:
            await self._gate.release_user(user_id)
            raise ConcurrencyFull("当前繁忙,请稍后再试")
        try:
            try:
                answer = await self._gateway.exchange(offer)
            except SignalingFailed:
                # 上游错误不透传原文(防御:实现泄漏原文也被这里吞掉重抛统一文案)
                log.warning("语音牵线被上游拒绝 user=%s", user_id)
                raise SignalingFailed("语音服务暂时不可用") from None
            # 牵线成功后的本地步骤失败同样要回滚准入:应答非法、票据签发失败都
            # 不能留下用户或槽位占位
            answer = self._check_answer(answer)
            call_id = uuid.uuid4().hex
            ticket = await self._tickets.issue(user_id, call_id, TICKET_TTL_SEC)
            self.calls[call_id] = CallSession(
                call_id=call_id,
                user_id=user_id,
                tenant_key=tenant_key,
                started_at=time.monotonic(),
            )
        except BaseException:
            await self._release_admission(user_id)
            raise
        else:
            return answer, ticket

    async def _release_admission(self, user_id: str) -> None:
        """释放已取得的准入占位(用户 + 槽位);两端各自独立,避免一侧失败漏掉另一侧。"""
        try:
            await self._gate.release_user(user_id)
        finally:
            await self._gate.release_slot()

    def _check_offer(self, offer: bytes) -> bytes:
        """连接请求约束(D10):≤128KB 且以 v=0 开头;不合法属客户端错误(400)。"""
        if len(offer) > OFFER_MAX_BYTES or not offer.startswith(b"v=0"):
            raise OfferInvalid("连接请求不合法")
        return offer

    def _check_answer(self, answer: bytes) -> bytes:
        """连接应答约束(D10):≤256KB、以 v=0 开头且为 UTF-8;畸形即判失败。"""
        if len(answer) > ANSWER_MAX_BYTES or not answer.startswith(b"v=0"):
            raise SignalingFailed("语音服务应答不合法")
        try:
            answer.decode("utf-8")
        except UnicodeDecodeError:
            raise SignalingFailed("语音服务应答不合法") from None
        return answer

    async def end_call(self, session: CallSession, *, end_reason: str) -> None:
        """结束通话:落一条操作审计并释放准入占位;不建表,审计即通话记录。
        同一通话只收口一次(挂断与通道断开竞态):后到者直接返回,不双释放准入
        也不双写审计。"""
        if self.calls.pop(session.call_id, None) is None:
            return
        await self._gate.release_user(session.user_id)
        await self._gate.release_slot()
        if self._operlog is None:
            return
        audit = CallAudit(
            user_id=session.user_id,
            tenant_key=session.tenant_key,
            call_id=session.call_id,
            duration_sec=max(0, int(time.monotonic() - session.started_at)),
            end_reason=end_reason,
            rounds=session.rounds,
            interrupts=session.interrupts,
            audio_tokens_in=session.audio_tokens_in,
            audio_tokens_out=session.audio_tokens_out,
            model=self._s.voice_model or "",
        )
        try:
            await self._operlog.add(
                session.user_id,
                "chat.voice.call_end",
                audit.call_id,
                {
                    "tenant_key": audit.tenant_key,
                    "duration_sec": audit.duration_sec,
                    "end_reason": audit.end_reason,
                    "rounds": audit.rounds,
                    "interrupts": audit.interrupts,
                    "audio_tokens_in": audit.audio_tokens_in,
                    "audio_tokens_out": audit.audio_tokens_out,
                    "model": audit.model,
                },
            )
        except Exception:
            # 审计尽力而为:通话结束的准入释放不受审计写失败影响
            log.exception("通话审计写入失败 call_id=%s", session.call_id)

    def new_ticket_secret(self) -> str:
        """票据随机源(secrets.urlsafe,与 OAuth state 同款做法)。"""
        return secrets.token_urlsafe(32)
