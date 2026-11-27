# pyright: reportPrivateUsage=false
"""语音通话准入、牵线与票据(voice-call「通话入口与可选启用」等)。

准入是保护服务器的闸:同一用户至多一通、部署级并发上限、对 Redis 故障
fail-closed;票据一次性、短时效、绑定本人;牵线端口对请求/应答设形状与
大小约束,上游错误不透传原文,日志不含正文。
"""

from typing import Any, cast

import pytest

from kongming.voice.errors import (
    ConcurrencyFull,
    DeploymentDailyLimitExceeded,
    OfferInvalid,
    RateLimited,
    SignalingFailed,
    UserCallConflict,
    UserDailyLimitExceeded,
    VoiceNotEnabled,
)
from kongming.voice.models import Ticket
from kongming.voice.service import VoiceService


def _settings(**over: Any) -> Any:
    """启用的最小配置;over 覆盖单字段。"""
    base: dict[str, Any] = {
        "voice_api_key": "k-test",
        "voice_realtime_base_url": "https://rt.example/api/v1/webrtc/realtime",
        "voice_model": "qwen3.8-omni-flash-realtime",
        "voice_max_concurrent": 2,
        "voice_max_call_seconds": 600,
        "voice_establish_rate_per_minute": 6,
        "voice_daily_calls_per_user": 20,
        "voice_daily_calls_per_deployment": 100,
    }
    base.update(over)
    return cast("Any", type("S", (), base)())


_OFFER = b"v=0\r\no=- 1 2 IN IP4 127.0.0.1\r\n"


class FakeGate:
    """替身准入:记录获释;Redis 故障用 boom 模拟。"""

    def __init__(self, *, user_ok: bool = True, slot_ok: bool = True, boom: bool = False) -> None:
        self.user_ok = user_ok
        self.slot_ok = slot_ok
        self.boom = boom
        self.acquired_user = 0
        self.released_user = 0
        self.acquired_slot = 0
        self.released_slot = 0

    async def try_acquire_user(self, user_id: str) -> bool:
        if self.boom:
            raise RuntimeError("redis down")
        if self.user_ok:
            self.acquired_user += 1
            return True
        return False

    async def release_user(self, user_id: str) -> None:
        self.released_user += 1

    async def try_acquire_slot(self) -> bool:
        if self.boom:
            raise RuntimeError("redis down")
        if self.slot_ok:
            self.acquired_slot += 1
            return True
        return False

    async def release_slot(self) -> None:
        self.released_slot += 1


class FakeGateway:
    """替身牵线:应答可脚本化;记录收到的请求字节。"""

    def __init__(
        self, answer: bytes | None = None, *, raise_error: Exception | None = None
    ) -> None:
        self.answer = answer if answer is not None else b"v=0\r\no=- 9 9 IN IP4 127.0.0.1\r\n"
        self.raise_error = raise_error
        self.received: list[bytes] = []

    async def exchange(self, offer: bytes) -> bytes:
        self.received.append(offer)
        if self.raise_error:
            raise self.raise_error
        return self.answer


class FakeTickets:
    def __init__(self) -> None:
        self.issued: list[Ticket] = []

    async def issue(self, user_id: str, call_id: str, ttl_sec: int) -> Ticket:
        t = Ticket(token=f"tk-{len(self.issued)}", user_id=user_id, call_id=call_id)
        self.issued.append(t)
        return t

    async def consume(self, token: str) -> Ticket | None:
        for t in self.issued:
            if t.token == token:
                self.issued.remove(t)
                return t
        return None


class FakeGuard:
    """替身建连闸:记录频次/预算的占用与释放;boom 模拟 Redis 故障。"""

    def __init__(
        self,
        *,
        rate_ok: bool = True,
        user_ok: bool = True,
        deploy_ok: bool = True,
        boom: bool = False,
    ) -> None:
        self.rate_ok = rate_ok
        self.user_ok = user_ok
        self.deploy_ok = deploy_ok
        self.boom = boom
        self.rate_attempts = 0
        self.user_reserves = 0
        self.deploy_reserves = 0
        self.user_releases = 0
        self.deploy_releases = 0

    async def allow_establish(self, user_id: str) -> bool:
        if self.boom:
            raise RuntimeError("redis down")
        self.rate_attempts += 1
        return self.rate_ok

    async def reserve_user_day(self, user_id: str) -> bool:
        if self.boom:
            raise RuntimeError("redis down")
        if self.user_ok:
            self.user_reserves += 1
        return self.user_ok

    async def reserve_deployment_day(self) -> bool:
        if self.boom:
            raise RuntimeError("redis down")
        if self.deploy_ok:
            self.deploy_reserves += 1
        return self.deploy_ok

    async def release_user_day(self, user_id: str) -> None:
        self.user_releases += 1

    async def release_deployment_day(self) -> None:
        self.deploy_releases += 1


def _svc(
    gate: FakeGate,
    gateway: FakeGateway,
    tickets: FakeTickets,
    settings: Any | None = None,
    guard: FakeGuard | None = None,
) -> VoiceService:
    return VoiceService(
        gate,
        gateway,
        tickets,
        guard=guard or FakeGuard(),
        settings=settings or _settings(),
    )  # type: ignore[arg-type]


UID = "2d1a6d35-7d79-40a6-948c-642b0e3f8943"


# ---- 启用形态 ----


async def test_not_enabled_when_settings_missing() -> None:
    for over in (
        {"voice_api_key": None},
        {"voice_realtime_base_url": None},
        {"voice_model": None},
    ):
        svc = _svc(FakeGate(), FakeGateway(), FakeTickets(), _settings(**over))
        with pytest.raises(VoiceNotEnabled):
            await svc.establish(user_id=UID, offer=_OFFER)


# ---- 准入 ----


async def test_admission_user_conflict_denied() -> None:
    """同一用户已有在途通话:拒绝并区分原因(409 已有一通);未到牵线。"""
    gate = FakeGate(user_ok=False)
    gateway = FakeGateway()
    with pytest.raises(UserCallConflict):
        await _svc(gate, gateway, FakeTickets()).establish(user_id=UID, offer=_OFFER)
    assert gateway.received == []


async def test_admission_slot_full_denied_and_user_released() -> None:
    """部署级并发已满:拒绝并区分原因(409 当前繁忙);先取得的用户占位须回滚。"""
    gate = FakeGate(slot_ok=False)
    with pytest.raises(ConcurrencyFull):
        await _svc(gate, FakeGateway(), FakeTickets()).establish(user_id=UID, offer=_OFFER)
    assert gate.released_user == 1


async def test_admission_redis_down_fail_closed() -> None:
    """Redis 不可用:拒绝建立(准入 fail-closed,与限流器 fail-open 相反是有意的)。"""
    from kongming.voice.errors import AdmissionUnavailable

    gate = FakeGate(boom=True)
    with pytest.raises(AdmissionUnavailable):
        await _svc(gate, FakeGateway(), FakeTickets()).establish(user_id=UID, offer=_OFFER)


# ---- 牵线 ----


async def test_offer_constraints() -> None:
    """连接请求不合法是客户端错误(OfferInvalid→400),不是上游故障。"""
    svc = _svc(FakeGate(), FakeGateway(), FakeTickets())
    with pytest.raises(OfferInvalid):
        await svc.establish(user_id=UID, offer=b"not-sdp")  # 不以 v=0 开头
    with pytest.raises(OfferInvalid):
        await svc.establish(user_id=UID, offer=b"v=0" + b"x" * (128 * 1024 + 1))  # 超 128KB


async def test_answer_constraints_and_upstream_error_opaque() -> None:
    """应答超限或畸形判失败;上游错误只回统一失败,不透传原文。"""
    svc = _svc(FakeGate(), FakeGateway(b"v=0" + b"x" * (256 * 1024 + 1)), FakeTickets())
    with pytest.raises(SignalingFailed):
        await svc.establish(user_id=UID, offer=_OFFER)
    svc2 = _svc(FakeGate(), FakeGateway(b"garbage-answer"), FakeTickets())
    with pytest.raises(SignalingFailed):
        await svc2.establish(user_id=UID, offer=_OFFER)
    svc3 = _svc(
        FakeGate(),
        FakeGateway(raise_error=SignalingFailed("upstream said: secret-blob")),
        FakeTickets(),
    )
    with pytest.raises(SignalingFailed) as ei:
        await svc3.establish(user_id=UID, offer=_OFFER)
    assert "secret-blob" not in str(ei.value)  # 上游原文不透传
    svc4 = _svc(FakeGate(), FakeGateway(b"v=0\xff"), FakeTickets())
    with pytest.raises(SignalingFailed):
        await svc4.establish(user_id=UID, offer=_OFFER)


async def test_invalid_answer_releases_admission() -> None:
    """牵线成功但应答非法:用户+槽位占位必须回滚,不能留下幽灵占用。"""
    gate = FakeGate()
    with pytest.raises(SignalingFailed):
        await _svc(gate, FakeGateway(b"garbage-answer"), FakeTickets()).establish(
            user_id=UID, offer=_OFFER
        )
    assert gate.released_user == 1 and gate.released_slot == 1


async def test_ticket_issue_failure_releases_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    """票据签发失败同属建立失败:准入占位回滚,不留到 TTL 的幽灵通话。"""
    gate = FakeGate()
    tickets = FakeTickets()

    async def _boom(*_a: object, **_kw: object) -> Ticket:
        raise RuntimeError("redis down")

    monkeypatch.setattr(tickets, "issue", _boom)
    with pytest.raises(RuntimeError):
        await _svc(gate, FakeGateway(), tickets).establish(user_id=UID, offer=_OFFER)
    assert gate.released_user == 1 and gate.released_slot == 1


async def test_signaling_failure_releases_admission() -> None:
    """牵线失败:准入占位(用户+槽位)须回滚,不留幽灵占用。"""
    gate = FakeGate()
    gateway = FakeGateway(raise_error=SignalingFailed("boom"))
    with pytest.raises(SignalingFailed):
        await _svc(gate, gateway, FakeTickets()).establish(user_id=UID, offer=_OFFER)
    assert gate.released_user == 1 and gate.released_slot == 1


async def test_establish_success_returns_answer_and_ticket() -> None:
    gate = FakeGate()
    gateway = FakeGateway()
    tickets = FakeTickets()
    out = await _svc(gate, gateway, tickets).establish(user_id=UID, offer=_OFFER)
    assert out is not None
    answer, ticket = out
    assert answer.startswith(b"v=0")
    assert ticket.user_id == UID
    assert tickets.issued[0].ttl_sec == 60
    assert gate.released_user == 0 and gate.released_slot == 0  # 成功不回滚


# ---- 建连频次与 24 小时预算 ----


async def test_rate_limit_denied_before_budget_and_gate() -> None:
    """频次超限:最先拒绝,不占预算、不占准入、不发起上游交换。"""
    gate = FakeGate()
    gateway = FakeGateway()
    guard = FakeGuard(rate_ok=False)
    with pytest.raises(RateLimited):
        await _svc(gate, gateway, FakeTickets(), guard=guard).establish(user_id=UID, offer=_OFFER)
    assert guard.rate_attempts == 1
    assert guard.user_reserves == 0 and guard.deploy_reserves == 0
    assert gate.acquired_user == 0 and gateway.received == []


async def test_user_budget_denied_before_gate() -> None:
    """用户预算用尽:不占部署预算、不占准入、不发起上游交换。"""
    gate = FakeGate()
    gateway = FakeGateway()
    guard = FakeGuard(user_ok=False)
    with pytest.raises(UserDailyLimitExceeded):
        await _svc(gate, gateway, FakeTickets(), guard=guard).establish(user_id=UID, offer=_OFFER)
    assert guard.deploy_reserves == 0
    assert gate.acquired_user == 0 and gateway.received == []


async def test_deploy_budget_denied_releases_user_budget() -> None:
    """部署预算用尽:回滚已占的用户预算,不占准入、不发起上游交换。"""
    gate = FakeGate()
    gateway = FakeGateway()
    guard = FakeGuard(deploy_ok=False)
    with pytest.raises(DeploymentDailyLimitExceeded):
        await _svc(gate, gateway, FakeTickets(), guard=guard).establish(user_id=UID, offer=_OFFER)
    assert guard.user_reserves == 1 and guard.user_releases == 1
    assert guard.deploy_reserves == 0
    assert gate.acquired_user == 0 and gateway.received == []


async def test_establish_failure_releases_budgets() -> None:
    """牵线失败:用户与部署预算都回滚,不留下幽灵额度。"""
    guard = FakeGuard()
    with pytest.raises(SignalingFailed):
        await _svc(
            FakeGate(),
            FakeGateway(raise_error=SignalingFailed("boom")),
            FakeTickets(),
            guard=guard,
        ).establish(user_id=UID, offer=_OFFER)
    assert guard.user_reserves == 1 and guard.deploy_reserves == 1
    assert guard.user_releases == 1 and guard.deploy_releases == 1


async def test_success_keeps_budgets() -> None:
    """成功建立:预算保留,释放次数为 0。"""
    guard = FakeGuard()
    await _svc(FakeGate(), FakeGateway(), FakeTickets(), guard=guard).establish(
        user_id=UID, offer=_OFFER
    )
    assert guard.user_releases == 0 and guard.deploy_releases == 0


async def test_user_budget_rejection_is_audited() -> None:
    """日预算拒绝落 chat.voice.call_rejected 审计;频次拒绝不落。"""
    sink = FakeOperLog()
    svc = VoiceService(
        FakeGate(),
        FakeGateway(),
        FakeTickets(),
        guard=FakeGuard(user_ok=False),
        operlog=sink,
        settings=_settings(),
    )
    with pytest.raises(UserDailyLimitExceeded):
        await svc.establish(user_id=UID, tenant_key="t-a", offer=_OFFER)
    assert sink.rows == [
        (
            UID,
            "chat.voice.call_rejected",
            None,
            {"tenant_key": "t-a", "reason": "user_daily", "limit": 20},
        )
    ]


# ---- 票据 ----


async def test_ticket_single_use() -> None:
    tickets = FakeTickets()
    t = await tickets.issue(UID, "call-1", 60)
    got = await tickets.consume(t.token)
    assert got is not None and got.call_id == "call-1" and got.user_id == UID
    assert await tickets.consume(t.token) is None  # 一次性:复用拒绝
    other = await tickets.issue("someone-else", "call-2", 60)
    got2 = await tickets.consume(other.token)
    assert got2 is not None and got2.user_id == "someone-else"  # 票据即凭据,归属随票据


async def test_ticket_ttl_is_short() -> None:
    tickets = FakeTickets()
    await tickets.issue(UID, "call-3", 60)
    assert tickets.issued[0].ttl_sec <= 60  # 短时效:泄漏窗口以秒计


# ---- 日志卫生 ----


async def test_no_payload_in_logs(caplog: pytest.LogCaptureFixture) -> None:
    """失败路径的日志不含请求与应答正文。"""
    import logging

    secret_offer = _OFFER + b"ice-password: hush"
    gateway = FakeGateway(raise_error=SignalingFailed("deadline"))
    svc = _svc(FakeGate(), gateway, FakeTickets())
    with (
        caplog.at_level(logging.DEBUG, logger="kongming.voice"),
        pytest.raises(SignalingFailed),
    ):
        await svc.establish(user_id=UID, offer=secret_offer)
    blob = caplog.text
    assert "hush" not in blob
    assert "ice-password" not in blob


# ============ 集成:路由鉴权与未启用形态 ============


async def _user_with_chat_perm(s: Any, make_user: Any, username: str) -> str:
    """造一个有 chat:execute 并绑定 default 租户的用户,返回 access 令牌。"""
    from sqlalchemy import text

    from kongming.auth import tokens
    from kongming.auth.seed import seed_all
    from kongming.config import get_settings

    await seed_all(s, get_settings())
    uid = await make_user(username)
    rid = (
        await s.execute(text("SELECT id FROM public.sys_role WHERE role_key='DEVELOPER'"))
    ).scalar_one()
    await s.execute(
        text("INSERT INTO public.sys_user_role (user_id, role_id) VALUES (:u, :r)"),
        {"u": uid, "r": str(rid)},
    )
    tid = (
        await s.execute(text("SELECT id FROM public.sys_tenant WHERE tenant_key='default'"))
    ).scalar_one()
    await s.execute(
        text("INSERT INTO public.sys_user_tenant (user_id, tenant_id) VALUES (:u, :t)"),
        {"u": uid, "t": str(tid)},
    )
    await s.commit()
    return tokens.issue(uid, "access")


async def test_establish_endpoint_not_enabled_is_503(
    client: Any, session: Any, make_user: Any
) -> None:
    """未配置实时语音模型:端点 503 并明示未启用。"""
    from kongming.main import app
    from tests.voice_dependencies_for_test import install_voice_service

    token = await _user_with_chat_perm(session, make_user, "voice-ne")
    with install_voice_service(app, enabled=False):
        r = await client.post(
            "/voice/calls",
            content=b"v=0\r\n",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/sdp"},
        )
    assert r.status_code == 503
    assert r.json()["detail"] == "VOICE_NOT_ENABLED"


async def test_establish_endpoint_requires_perm(client: Any, session: Any, make_user: Any) -> None:
    """无 chat:execute:403(启用与否不影响鉴权先决)。"""
    from kongming.auth import tokens
    from kongming.main import app
    from tests.voice_dependencies_for_test import install_voice_service

    await session.commit()
    uid = await make_user("voice-noperm")
    token = tokens.issue(uid, "access")
    with install_voice_service(app, enabled=True):
        r = await client.post(
            "/voice/calls",
            content=b"v=0\r\n",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/sdp"},
        )
    assert r.status_code == 403


async def test_capability_endpoint_reports_enabled_state(
    client: Any, session: Any, make_user: Any
) -> None:
    """能力探测:未配置返回未启用,配置齐备返回启用;页面据此决定入口是否出现。"""
    from kongming.main import app
    from tests.voice_dependencies_for_test import install_voice_service

    token = await _user_with_chat_perm(session, make_user, "voice-cap")
    with install_voice_service(app, enabled=False):
        r = await client.get(
            "/voice/calls/capability", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 200
        assert r.json() == {"enabled": False}
    with install_voice_service(app, enabled=True):
        r = await client.get(
            "/voice/calls/capability", headers={"Authorization": f"Bearer {token}"}
        )
    assert r.status_code == 200
    assert r.json() == {"enabled": True}


async def test_capability_endpoint_requires_perm(client: Any, session: Any, make_user: Any) -> None:
    """无 chat:execute:403(探测同样不越权限点)。"""
    from kongming.auth import tokens
    from kongming.main import app
    from tests.voice_dependencies_for_test import install_voice_service

    await session.commit()
    uid = await make_user("voice-cap-noperm")
    token = tokens.issue(uid, "access")
    with install_voice_service(app, enabled=True):
        r = await client.get(
            "/voice/calls/capability", headers={"Authorization": f"Bearer {token}"}
        )
    assert r.status_code == 403


async def test_establish_endpoint_success(client: Any, session: Any, make_user: Any) -> None:
    """启用形态下牵线成功:返回连接应答、票据与通话标识。"""
    from kongming.main import app
    from tests.voice_dependencies_for_test import install_voice_service

    token = await _user_with_chat_perm(session, make_user, "voice-ok")
    with install_voice_service(
        app, enabled=True, answer=b"v=0\r\no=- 9 9 IN IP4 127.0.0.1\r\n"
    ) as svc:
        r = await client.post(
            "/voice/calls",
            content=b"v=0\r\no=- 1 2 IN IP4 127.0.0.1\r\n",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/sdp",
                "X-Tenant-Id": "default",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["answer"].startswith("v=0")
        assert body["ticket"] and body["call_id"]
        # 挂断:仅本人可挂;他人 404 同形(不泄露通话是否存在)
        other = tokens_issue_other()
        r2 = await client.delete(
            f"/voice/calls/{body['call_id']}", headers={"Authorization": f"Bearer {other}"}
        )
        assert r2.status_code == 404
        r3 = await client.delete(
            f"/voice/calls/{body['call_id']}", headers={"Authorization": f"Bearer {token}"}
        )
        assert r3.status_code == 200
        assert svc.calls == {}  # 挂断后在途表清空


def tokens_issue_other() -> str:
    from kongming.auth import tokens

    return tokens.issue("00000000-0000-7000-8000-000000000001", "access")


async def test_establish_rate_limited_is_429(client: Any, session: Any, make_user: Any) -> None:
    """建联过频:429 专属码,且不建立通话。"""
    from kongming.main import app
    from tests.voice_dependencies_for_test import install_voice_service

    token = await _user_with_chat_perm(session, make_user, "voice-rate")
    with install_voice_service(app, enabled=True, guard=FakeGuard(rate_ok=False)) as svc:
        r = await client.post(
            "/voice/calls",
            content=b"v=0\r\n",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/sdp"},
        )
        assert r.status_code == 429
        assert r.json()["detail"] == "VOICE_RATE_LIMITED"
        assert svc.calls == {}


async def test_establish_user_budget_429(client: Any, session: Any, make_user: Any) -> None:
    """用户 24 小时预算用尽:429 专属码。"""
    from kongming.main import app
    from tests.voice_dependencies_for_test import install_voice_service

    token = await _user_with_chat_perm(session, make_user, "voice-user-budget")
    with install_voice_service(app, enabled=True, guard=FakeGuard(user_ok=False)) as svc:
        r = await client.post(
            "/voice/calls",
            content=b"v=0\r\n",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/sdp"},
        )
        assert r.status_code == 429
        assert r.json()["detail"] == "VOICE_DAILY_LIMIT"
        assert svc.calls == {}


async def test_establish_deploy_budget_429_busy(client: Any, session: Any, make_user: Any) -> None:
    """部署预算用尽:429 但语义是当前繁忙(复用 VOICE_BUSY 文案)。"""
    from kongming.main import app
    from tests.voice_dependencies_for_test import install_voice_service

    token = await _user_with_chat_perm(session, make_user, "voice-deploy-budget")
    with install_voice_service(app, enabled=True, guard=FakeGuard(deploy_ok=False)) as svc:
        r = await client.post(
            "/voice/calls",
            content=b"v=0\r\n",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/sdp"},
        )
        assert r.status_code == 429
        assert r.json()["detail"] == "VOICE_BUSY"
        assert svc.calls == {}


async def test_establish_invalid_tenant_is_403(client: Any, session: Any, make_user: Any) -> None:
    """声明的租户未绑定:403;不发起上游交换。"""
    from kongming.main import app
    from tests.voice_dependencies_for_test import install_voice_service

    token = await _user_with_chat_perm(session, make_user, "voice-tenant-bad")
    with install_voice_service(app, enabled=True) as svc:
        r = await client.post(
            "/voice/calls",
            content=b"v=0\r\n",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/sdp",
                "X-Tenant-Id": "not-bound",
            },
        )
        assert r.status_code == 403
        assert r.json()["detail"] == "TENANT_FORBIDDEN"
        assert svc.calls == {}


async def test_establish_audits_validated_tenant(client: Any, session: Any, make_user: Any) -> None:
    """审计记录校验后的租户;未声明时取默认租户。"""
    from kongming.main import app
    from tests.voice_dependencies_for_test import install_voice_service

    token = await _user_with_chat_perm(session, make_user, "voice-tenant-ok")
    sink = FakeOperLog()
    with install_voice_service(app, enabled=True, operlog=sink) as svc:
        r = await client.post(
            "/voice/calls",
            content=b"v=0\r\n",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/sdp",
                "X-Tenant-Id": "default",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert svc.calls[body["call_id"]].tenant_key == "default"
        await client.delete(
            f"/voice/calls/{body['call_id']}", headers={"Authorization": f"Bearer {token}"}
        )
    assert sink.rows[-1][3]["tenant_key"] == "default"

    # 未声明租户:取默认租户(default 优先)
    with install_voice_service(app, enabled=True) as svc2:
        r2 = await client.post(
            "/voice/calls",
            content=b"v=0\r\n",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/sdp"},
        )
        assert r2.status_code == 200
        assert svc2.calls[r2.json()["call_id"]].tenant_key == "default"


# ============ WebSocket 通道鉴权(3.3):票据缺失/复用/通话不在途一律直接关闭 ============


@pytest.fixture
def isolated_voice_redis(monkeypatch: pytest.MonkeyPatch) -> Any:
    """TestClient 起独立事件循环:组合根的 Redis 票据单例须换新客户端,
    否则沿用旧循环的连接池会跨循环复用 Future(connected-to-different-loop)。
    返回新客户端的票据存储,测试的签发也用它(同一循环语义由使用方保证)。"""
    from redis.asyncio import from_url

    import kongming.chat.dependencies as deps
    from kongming.redis_client import get_settings
    from kongming.voice.repository import RedisTicketStore

    store = RedisTicketStore(from_url(get_settings().redis_url, decode_responses=True))
    monkeypatch.setattr(deps, "_VOICE_TICKETS", store)
    return store


def _sync_establish(svc: Any, *, user_id: str) -> str:
    """同步驱动 establish(独立事件循环;Redis 连接在该循环内新建)。"""
    import anyio

    async def _go() -> str:
        _, ticket = await svc.establish(user_id=user_id, offer=b"v=0\r\n")
        return ticket.token

    return anyio.run(_go)


def test_channel_unknown_ticket_closed_4401(isolated_voice_redis: Any) -> None:
    """不存在的票据:accept 后立即被关闭(4401),不泄露通话是否存在。"""
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from kongming.main import app

    with (
        pytest.raises(WebSocketDisconnect) as ei,
        TestClient(app).websocket_connect("/voice/calls/channel?ticket=missing-token") as ws,
    ):
        ws.receive_json()
    assert ei.value.code == 4401


def test_channel_ticket_without_active_call_closed_4404(isolated_voice_redis: Any) -> None:
    """真实签发的票据,但通话已不在途:关闭(4404),与不存在同形不泄露。"""
    import anyio
    from starlette.testclient import TestClient

    from kongming.main import app
    from kongming.voice.repository import RedisTicketStore

    store = cast("RedisTicketStore", isolated_voice_redis)

    async def _issue() -> str:
        t = await store.issue(UID, "call-gone", 60)
        return t.token

    token = anyio.run(_issue)
    with (
        pytest.raises(Exception) as ei,
        TestClient(app).websocket_connect(f"/voice/calls/channel?ticket={token}"),
    ):
        pass  # 通话不在途,连接不应建立
    assert "4404" in str(ei.value) or "closed" in str(ei.value).lower()


def test_channel_ticket_single_use_second_connect_rejected(isolated_voice_redis: Any) -> None:
    """建立通话后首连成功;同一票据复连被拒(一次性)。"""
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from kongming.main import app
    from tests.voice_dependencies_for_test import install_voice_service

    with install_voice_service(
        app, enabled=True, answer=b"v=0\r\nok\r\n", tickets=isolated_voice_redis
    ) as svc:
        token = _sync_establish(svc, user_id=UID)
        # 首连通过鉴权(没有 4401/4404 的拒绝关闭);通道随即收口,读侧收到关闭
        with (
            pytest.raises((WebSocketDisconnect, Exception)) as first,
            TestClient(app).websocket_connect(f"/voice/calls/channel?ticket={token}") as ws,
        ):
            ws.receive_json()
        code = getattr(first.value, "code", None)
        assert code not in (4401, 4404)
        # 复用同一票据:第二次连接被鉴权关闭(一次性)
        with (
            pytest.raises(WebSocketDisconnect) as ei,
            TestClient(app).websocket_connect(f"/voice/calls/channel?ticket={token}") as ws,
        ):
            ws.receive_json()
        assert ei.value.code == 4401


def test_channel_ticket_via_subprotocol(monkeypatch: pytest.MonkeyPatch) -> None:
    """票据走子协议:handshake 回选该协议,URL 不含票据。"""
    from starlette.testclient import TestClient

    import kongming.chat.dependencies as deps
    from kongming.main import app
    from tests.voice_dependencies_for_test import install_voice_service

    tickets = FakeTickets()
    monkeypatch.setattr(deps, "_VOICE_TICKETS", tickets)
    with install_voice_service(app, enabled=True, tickets=tickets) as svc:
        token = _sync_establish(svc, user_id=UID)
        with TestClient(app).websocket_connect("/voice/calls/channel", subprotocols=[token]) as ws:
            assert ws.accepted_subprotocol == token
            cfg = ws.receive_json()
            assert cfg["type"] == "model.send"


def test_channel_oversized_event_closes_1009(monkeypatch: pytest.MonkeyPatch) -> None:
    """单帧超过 64KB:关闭 1009 并正常收口(通话从在途表移除)。"""
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    import kongming.chat.dependencies as deps
    from kongming.main import app
    from tests.voice_dependencies_for_test import install_voice_service

    tickets = FakeTickets()
    monkeypatch.setattr(deps, "_VOICE_TICKETS", tickets)
    with install_voice_service(app, enabled=True, tickets=tickets) as svc:
        token = _sync_establish(svc, user_id=UID)
        with (
            pytest.raises(WebSocketDisconnect) as ei,
            TestClient(app).websocket_connect(f"/voice/calls/channel?ticket={token}") as ws,
        ):
            ws.receive_json()
            ws.send_text("x" * (64 * 1024 + 1))
            ws.receive_json()
        assert ei.value.code == 1009
        assert svc.calls == {}


def test_client_ip_middleware_covers_websocket_scope() -> None:
    """来源 IP 中间件覆盖 WebSocket 范围:通话审计可取到 client host。"""
    from kongming.audit.context import ClientIpMiddleware, client_ip_var

    seen: dict[str, str | None] = {}

    async def _inner(scope: Any, receive: Any, send: Any) -> None:
        seen["ip"] = client_ip_var.get()

    import anyio

    async def _go() -> None:
        mw = ClientIpMiddleware(_inner)
        await mw({"type": "websocket", "client": ("10.1.2.3", 5000)}, None, None)  # type: ignore[arg-type]
        await mw({"type": "http", "client": ("10.9.9.9", 80)}, None, None)  # type: ignore[arg-type]

    anyio.run(_go)
    assert seen["ip"] == "10.9.9.9"  # 两个 scope 类型都写入(websocket 先写,http 后覆写)


# ============ 评审修复回归:通道提问签发/静默到期/垃圾帧/收口幂等 ============


class FakeOperLog:
    """替身审计:记录调用(次数即通话审计条数)。"""

    def __init__(self) -> None:
        self.rows: list[tuple[Any, str, Any, dict[str, object]]] = []

    async def add(
        self, user_id: str | None, action: str, target: str | None, detail: dict[str, object]
    ) -> None:
        self.rows.append((user_id, action, target, detail))


def test_end_call_idempotent_single_release_and_audit() -> None:
    """同一通话两次收口(挂断与通道断开竞态):准入只释放一次,审计只落一条。"""
    import anyio

    from tests.test_voice_calls import FakeGateway, FakeTickets

    gate = FakeGate()
    sink = FakeOperLog()
    svc = VoiceService(
        gate,
        FakeGateway(b"v=0\r\nok"),
        FakeTickets(),
        guard=FakeGuard(),
        operlog=sink,
        settings=_settings(),
    )

    async def _run() -> None:
        _, ticket = await svc.establish(user_id="u-idem", offer=_OFFER)
        session = svc.calls[ticket.call_id]
        await svc.end_call(session, end_reason="hung_up")
        await svc.end_call(session, end_reason="channel_closed")

    anyio.run(_run)
    assert gate.released_user == 1
    assert gate.released_slot == 1
    assert len(sink.rows) == 1


async def test_hang_up_closes_channel_not_just_admission() -> None:
    """挂断端点须关闭仍开着的文字事件通道:只释放准入会让旧通道继续可用。"""
    from kongming.auth.models import AuthenticatedUser
    from kongming.voice import router as voice_router

    class _FakeSvc:
        def __init__(self) -> None:
            self.calls = {"c-close": type("S", (), {"user_id": UID})()}
            self.ended: list[str] = []

        async def end_call(self, session: object, *, end_reason: str) -> None:
            self.ended.append(end_reason)
            self.calls.pop("c-close", None)

    class _FakeChannel:
        def __init__(self) -> None:
            self.closed: int | None = None

        async def close(self, code: int) -> None:
            self.closed = code

    svc = _FakeSvc()
    channel = _FakeChannel()
    voice_router._CHANNELS["c-close"] = cast("Any", channel)
    await voice_router.hang_up("c-close", AuthenticatedUser(user_id=UID), cast("Any", svc))
    assert channel.closed == 1000
    assert "c-close" not in voice_router._CHANNELS
    assert svc.ended == ["hung_up"]


def test_channel_tool_call_issues_turn_request_with_real_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真实注册表接线:模型提问事件签发关联字段下发 turn.request;
    终态未被问答侧绑定时按失败回填,模型收到如实说明的补交指令。"""
    import json as json_mod

    from starlette.testclient import TestClient

    import kongming.chat.dependencies as deps
    import kongming.voice.router as voice_router
    from kongming.main import app
    from tests.voice_dependencies_for_test import install_voice_service

    monkeypatch.setattr(voice_router, "_TURN_BIND_WAIT_SEC", 0.05)
    tickets = FakeTickets()
    monkeypatch.setattr(deps, "_VOICE_TICKETS", tickets)
    with install_voice_service(app, enabled=True, tickets=tickets) as svc:
        token = _sync_establish(svc, user_id=UID)
        with TestClient(app).websocket_connect(f"/voice/calls/channel?ticket={token}") as ws:
            cfg = ws.receive_json()
            assert cfg["type"] == "model.send"
            assert cfg["payload"]["type"] == "session.update"
            assert cfg["payload"]["session"]["turn_detection"] == {
                "type": "semantic_vad",
                "threshold": 0.4,
                "silence_duration_ms": 2000,
            }
            assert cfg["payload"]["session"]["voice"] == "Serena"
            assert [t["name"] for t in cfg["payload"]["session"]["tools"]] == [
                "ask_kongming",
                "stop_current_query",
            ]
            ws.send_text(
                json_mod.dumps(
                    {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "function_call",
                            "name": "ask_kongming",
                            "call_id": "fc_1",
                            "arguments": '{"question": "上个月卖了多少钱"}',
                        },
                    }
                )
            )
            first = ws.receive_json()
            assert first["type"] == "turn.request"
            assert first["question"] == "上个月卖了多少钱"
            assert first["token"]
            # 关联字段未被问答侧绑定:终态等待方按失败回填,模型收到补交与再次应答指令
            backfill = ws.receive_json()
            assert backfill["type"] == "model.send"
            # 实时 API 形态:函数结果经 conversation.item.create 包裹提交
            assert backfill["payload"]["type"] == "conversation.item.create"
            assert backfill["payload"]["item"]["type"] == "function_call_output"
            assert "没有查到结果" in json_mod.dumps(backfill["payload"], ensure_ascii=False)
            allow = ws.receive_json()
            assert allow["type"] == "playback.allow"
            again = ws.receive_json()
            assert again["payload"]["type"] == "response.create"


def test_channel_stop_tool_only_confirms_without_turn_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """停止工具经通道到达:回工具结果并下发固定确认,不签发 turn.request。"""
    import json as json_mod

    from starlette.testclient import TestClient

    import kongming.chat.dependencies as deps
    from kongming.main import app
    from tests.voice_dependencies_for_test import install_voice_service

    tickets = FakeTickets()
    monkeypatch.setattr(deps, "_VOICE_TICKETS", tickets)
    with install_voice_service(app, enabled=True, tickets=tickets) as svc:
        token = _sync_establish(svc, user_id=UID)
        with TestClient(app).websocket_connect(f"/voice/calls/channel?ticket={token}") as ws:
            ws.receive_json()  # session.update
            ws.send_text(
                json_mod.dumps(
                    {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "function_call",
                            "name": "stop_current_query",
                            "call_id": "fc-stop",
                            "arguments": "{}",
                        },
                    }
                )
            )
            backfill = ws.receive_json()
            assert backfill["type"] == "model.send"
            assert backfill["payload"]["item"]["type"] == "function_call_output"
            stopped = ws.receive_json()
            assert stopped["type"] == "turn.stopped"
            # 停止只收口在途问答,通话本身仍在途(通道关闭时才清理)
            assert svc.calls
        assert svc.calls == {}


def test_channel_non_dict_model_event_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """非对象模型事件(数组等)跳帧不断通道:后续事件照常处理,关闭时正常收口。"""
    import json as json_mod

    from starlette.testclient import TestClient

    import kongming.chat.dependencies as deps
    from kongming.main import app
    from tests.voice_dependencies_for_test import install_voice_service

    tickets = FakeTickets()
    monkeypatch.setattr(deps, "_VOICE_TICKETS", tickets)
    with install_voice_service(app, enabled=True, tickets=tickets) as svc:
        token = _sync_establish(svc, user_id=UID)
        with TestClient(app).websocket_connect(f"/voice/calls/channel?ticket={token}") as ws:
            ws.send_text(json_mod.dumps(["not", "a", "dict"]))
            ws.send_text(json_mod.dumps({"type": "response.done", "response": {"usage": {}}}))
            ws.close()
        assert svc.calls == {}  # 通道关闭已收口,通话不再在途


def test_channel_silent_call_expires_at_max_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """静默通话(无模型事件)也受时长上限:墙钟到期收口(4429)并清理。"""
    import threading

    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    import kongming.chat.dependencies as deps
    import kongming.voice.router as voice_router
    from kongming.main import app
    from tests.voice_dependencies_for_test import install_voice_service

    monkeypatch.setattr(voice_router, "_CALL_TICK_SEC", 0.05)
    tickets = FakeTickets()
    monkeypatch.setattr(deps, "_VOICE_TICKETS", tickets)
    sink = FakeOperLog()
    with (
        install_voice_service(
            app, enabled=True, tickets=tickets, operlog=sink, max_call_seconds=1
        ) as svc,
        TestClient(app).websocket_connect(
            f"/voice/calls/channel?ticket={_sync_establish(svc, user_id=UID)}"
        ) as ws,
    ):
        result: dict[str, Any] = {}

        def _recv() -> None:
            try:
                # 建立即收到 session.update,到期收到结束通知并关闭(4429)
                result["config"] = ws.receive_json()
                result["frame"] = ws.receive_json()
                result["code"] = ws.receive_json()
            except WebSocketDisconnect as e:
                result["code"] = e.code

        watcher = threading.Thread(target=_recv, daemon=True)
        watcher.start()
        watcher.join(5)
        assert not watcher.is_alive(), "静默通话未按时长上限收口"
        assert result.get("code") == 4429
        assert svc.calls == {}
    assert len(sink.rows) == 1
    assert sink.rows[0][3]["end_reason"] == "max_duration"


# ============ 评审遗留清理:无效请求 400 / 审计真实计数 / 死常量随删随验 ============


async def test_establish_invalid_offer_is_client_error(
    client: Any, session: Any, make_user: Any
) -> None:
    """连接请求不合法(缺 v=0 头/超大)是客户端错误:400 专属码,不是 502 上游故障。"""
    from kongming.main import app
    from tests.voice_dependencies_for_test import install_voice_service

    token = await _user_with_chat_perm(session, make_user, "voice-offer")
    with install_voice_service(app, enabled=True):
        r = await client.post(
            "/voice/calls",
            content=b"not-an-sdp",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/sdp"},
        )
        assert r.status_code == 400
        assert r.json()["detail"] == "VOICE_OFFER_INVALID"
        # 超大 body 在读取阶段即拒绝,不进入服务层
        huge = await client.post(
            "/voice/calls",
            content=b"v=0" + b"x" * (128 * 1024 + 1),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/sdp"},
        )
        assert huge.status_code == 400
        assert huge.json()["detail"] == "VOICE_OFFER_INVALID"


def test_channel_audit_records_rounds_and_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """通话审计记真实轮数与用量(不再恒零):提问一轮 + 用量事件后收口,审计如实落。"""
    import json as json_mod

    from starlette.testclient import TestClient

    import kongming.chat.dependencies as deps
    from kongming.main import app
    from tests.voice_dependencies_for_test import install_voice_service

    tickets = FakeTickets()
    monkeypatch.setattr(deps, "_VOICE_TICKETS", tickets)
    sink = FakeOperLog()
    with install_voice_service(app, enabled=True, tickets=tickets, operlog=sink) as svc:
        token = _sync_establish(svc, user_id=UID)
        with TestClient(app).websocket_connect(f"/voice/calls/channel?ticket={token}") as ws:
            ws.receive_json()  # session.update
            ws.send_text(
                json_mod.dumps(
                    {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "function_call",
                            "name": "ask_kongming",
                            "call_id": "fc_1",
                            "arguments": '{"question": "上月合计"}',
                        },
                    }
                )
            )
            ws.receive_json()  # turn.request
            ws.send_text(
                json_mod.dumps(
                    {
                        "type": "response.done",
                        "response": {
                            "usage": {
                                "input_token_details": {"audio": 100},
                                "output_token_details": {"audio": 40},
                            }
                        },
                    }
                )
            )
            ws.close()
        assert len(sink.rows) == 1
        detail = sink.rows[0][3]
        assert detail["rounds"] == 1
        assert detail["audio_tokens_in"] == 100
        assert detail["audio_tokens_out"] == 40
        blob = json_mod.dumps(detail, ensure_ascii=False)
        assert "voice_api_key" not in blob and "密钥" not in blob


def test_voice_service_is_process_singleton() -> None:
    """语音服务进程内单例:在途通话表挂在实例上,建立/通道/挂断必须共用同一实例。"""
    from kongming.chat.dependencies import get_voice_service

    assert get_voice_service() is get_voice_service()


def test_superseding_tool_call_does_not_block_turn_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """新问顶掉旧轮时,旧轮异步取消不得阻塞新一轮 turn.request 下发。"""
    import asyncio as asyncio_mod
    import json as json_mod
    import time

    from starlette.testclient import TestClient

    import kongming.chat.dependencies as deps
    import kongming.voice.router as voice_router
    from kongming.main import app
    from tests.voice_dependencies_for_test import install_voice_service

    tickets = FakeTickets()
    monkeypatch.setattr(deps, "_VOICE_TICKETS", tickets)

    async def _slow_cancel(user_id: str, trace_id: str) -> None:
        await asyncio_mod.sleep(1.5)

    monkeypatch.setattr(voice_router, "get_turn_canceler", lambda: _slow_cancel)

    with install_voice_service(app, enabled=True, tickets=tickets) as svc:
        token = _sync_establish(svc, user_id=UID)
        with TestClient(app).websocket_connect(f"/voice/calls/channel?ticket={token}") as ws:
            ws.receive_json()  # session.update
            # 第一问并绑定在途 trace_id
            ws.send_text(
                json_mod.dumps(
                    {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "function_call",
                            "name": "ask_kongming",
                            "call_id": "fc_1",
                            "arguments": '{"question": "第一问"}',
                        },
                    }
                )
            )
            first = ws.receive_json()
            assert first["type"] == "turn.request"
            reg = deps.get_voice_registry()
            assert reg.validate(token=first["token"], user_id=UID)
            assert reg.bind(token=first["token"], trace_id="t-slow-1")
            # 等后台等待任务完成 bind
            time.sleep(0.05)

            # 第二问到达:不得等 1.5s 的取消才下发 turn.request
            t0 = time.monotonic()
            ws.send_text(
                json_mod.dumps(
                    {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "function_call",
                            "name": "ask_kongming",
                            "call_id": "fc_2",
                            "arguments": '{"question": "第二问"}',
                        },
                    }
                )
            )
            second = ws.receive_json()
            elapsed = time.monotonic() - t0
            assert second["type"] == "turn.request"
            assert second["question"] == "第二问"
            assert elapsed < 0.8
            ws.close()
