# pyright: reportPrivateUsage=false
"""-chat-cost-control 测试:速率闸/配额闸/公开嵌入限流/失败口径。

单元层使用假时钟和假端口验证 fail-open/fail-closed;
集成层(HTTP 端到端):monkeypatch 组合根 _build_cost_gate 注入假端口,
429/503 与 detail 码、X-Quota-Warning、agent 零调用(未烧 token)断言。
"""

import datetime as dt
from collections.abc import Iterator

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.chat.costgate import (
    ChatCostGate,
    ChatQuotaExceeded,
    ChatQuotaUnavailable,
    ChatRateLimited,
    day_window,
)
from kongming.chat.dependencies import get_agent_client
from kongming.main import app
from tests.conftest import MakeUser

_TZ = dt.timezone(dt.timedelta(hours=8))


class _Limiter:
    """可编排限流器:fail=恒拒绝;boom=恒抛(存储异常)。"""

    def __init__(self, *, fail: bool = False, boom: bool = False) -> None:
        self.fail = fail
        self.boom = boom
        self.keys: list[str] = []

    async def allow(self, key: str, *, limit: int, window_sec: int) -> bool:
        self.keys.append(key)
        if self.boom:
            raise ConnectionError("redis down")
        return not self.fail

    async def allow_sliding(self, key: str, *, limit: int, window_sec: int) -> bool:
        return await self.allow(key, limit=limit, window_sec=window_sec)


class _CountingLimiter:
    """按 key 计数的窗口语义替身:同 key 第 limit+1 次起拒绝。"""

    def __init__(self, *, limit: int = 1) -> None:
        self.limit = limit
        self.seen: dict[str, int] = {}
        self.keys: list[str] = []

    async def allow(self, key: str, *, limit: int, window_sec: int) -> bool:
        self.keys.append(key)
        n = self.seen.get(key, 0) + 1
        self.seen[key] = n
        return n <= self.limit

    async def allow_sliding(self, key: str, *, limit: int, window_sec: int) -> bool:
        return await self.allow(key, limit=limit, window_sec=window_sec)


class _Counter:
    def __init__(
        self,
        day: int = 0,
        month: int = 0,
        *,
        boom: bool = False,
    ) -> None:
        self.day = day
        self.month = month
        self.boom = boom
        self.calls: list[tuple[str, str, str]] = []

    async def count_calls(
        self, *, tenant_id: str, day_start: object, month_start: object
    ) -> tuple[int, int]:
        self.calls.append((tenant_id, str(day_start), str(month_start)))
        if self.boom:
            raise RuntimeError("db down")
        return self.day, self.month


class _Oper:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict[str, object]]] = []

    async def log(self, *, action: str, target: str, detail: dict[str, object]) -> None:
        self.records.append((action, target, detail))


def _gate(
    limiter: _Limiter,
    counter: _Counter,
    oper: _Oper | None = None,
    *,
    rate: int = 20,
    daily: int = 500,
    monthly: int = 10000,
    now: dt.datetime | None = None,
) -> ChatCostGate:
    return ChatCostGate(
        limiter=limiter,
        counter=counter,
        oper_logger=oper,
        rate_per_minute=rate,
        quota_daily_calls=daily,
        quota_monthly_calls=monthly,
        now=now,
    )


# ── 1. 速率闸 ─────────────────────────────────────────────────────


async def test_rate_limit_blocks_with_429() -> None:
    limiter, counter = _Limiter(fail=True), _Counter()
    with pytest.raises(ChatRateLimited):
        await _gate(limiter, counter).check_login(tenant_id="t1", user_id="u1")
    assert limiter.keys == ["chat:t1:u1"]  # (租户, 用户) 维度键


async def test_rate_limit_fail_open_on_store_error() -> None:
    """限流存储异常放行(速率闸 fail-open;限流是保护层非安全闸)。"""
    limiter, counter = _Limiter(boom=True), _Counter()
    warn = await _gate(limiter, counter).check_login(tenant_id="t1", user_id="u1")
    assert warn == ""  # 放行,不抛


async def test_rate_zero_disables_gate() -> None:
    """0=不限:闸完全不响应(部署可关)。"""
    limiter, counter = _Limiter(fail=True), _Counter()
    await _gate(limiter, counter, rate=0).check_login(tenant_id="t1", user_id="u1")
    assert limiter.keys == []


# ── 2. 配额闸 ─────────────────────────────────────────────────────


async def test_daily_quota_exceeded_429_with_audit() -> None:
    limiter, counter, oper = _Limiter(), _Counter(day=500), _Oper()
    with pytest.raises(ChatQuotaExceeded):
        await _gate(limiter, counter, oper, daily=500).check_login(tenant_id="t1", user_id="u1")
    actions = [a for a, _, _ in oper.records]
    assert "chat.quota_reject" in actions  # 拒绝落审计(同口径)


async def test_monthly_quota_exceeded() -> None:
    limiter, counter = _Limiter(), _Counter(month=10_000)
    with pytest.raises(ChatQuotaExceeded):
        await _gate(limiter, counter, monthly=10_000).check_login(tenant_id="t1", user_id="u1")


async def test_quota_warn_header_under_limit() -> None:
    """达预警线(80%)不拒绝,返回 X-Quota-Warning 值。"""
    limiter, counter = _Limiter(), _Counter(day=450, month=100)
    warn = await _gate(limiter, counter, daily=500, monthly=10_000).check_login(
        tenant_id="t1", user_id="u1"
    )
    assert warn == "CHAT_DAILY_CALLS:450/500"


async def test_quota_read_failure_fails_closed_with_audit() -> None:
    """配额读数异常拒绝并落审计(配额闸 fail-closed)。"""
    limiter, counter, oper = _Limiter(), _Counter(boom=True), _Oper()
    with pytest.raises(ChatQuotaUnavailable):
        await _gate(limiter, counter, oper).check_login(tenant_id="t1", user_id="u1")
    actions = [a for a, _, _ in oper.records]
    assert "chat.quota_read_failed" in actions


async def test_quota_zero_disables_dimension() -> None:
    limiter, counter = _Limiter(), _Counter(day=999_999, month=999_999)
    warn = await _gate(limiter, counter, daily=0, monthly=0).check_login(
        tenant_id="t1", user_id="u1"
    )
    assert warn == ""


def test_day_window_asia_shanghai() -> None:
    """窗口起点按 Asia/Shanghai 日历日/月(与 billing 同口径)。"""
    day, month = day_window(dt.datetime(2026, 9, 27, 23, 30, tzinfo=dt.UTC))
    assert (day.year, day.month, day.day, day.hour) == (2026, 9, 28, 0)  # UTC+8 已跨日
    assert (month.year, month.month, month.day) == (2026, 9, 1)


# ── 3. 公开嵌入面(bot_key, ip)维度 ────────────────────────────────


async def test_bot_gate_dimension_keys() -> None:
    """(助手标识, 客户端 IP) 维度;换 IP/换 bot 互不影响。"""
    limiter, counter = _Limiter(), _Counter()
    gate = _gate(limiter, counter, rate=20)
    await gate.check_bot(bot_key="kb", client_ip="1.1.1.1")
    await gate.check_bot(bot_key="kb", client_ip="2.2.2.2")
    await gate.check_bot(bot_key="kb2", client_ip="1.1.1.1")
    assert limiter.keys == [
        "chatbot:kb:1.1.1.1",
        "chatbot:kb:2.2.2.2",
        "chatbot:kb2:1.1.1.1",
    ]

    blocked = _gate(_Limiter(fail=True), counter, rate=20)
    with pytest.raises(ChatRateLimited):
        await blocked.check_bot(bot_key="kb", client_ip="1.1.1.1")


async def test_bot_gate_ip_never_from_request_header() -> None:
    """限流键只使用 ClientIpMiddleware 注入的 IP,不读取请求自带的 X-Forwarded-For。"""
    limiter, counter = _Limiter(), _Counter()
    gate = _gate(limiter, counter, rate=20)
    await gate.check_bot(bot_key="kb", client_ip=None)  # 非 HTTP 上下文(None)
    assert limiter.keys == ["chatbot:kb:noip"]


# ── 4. HTTP 集成(依赖替身 + 组合根 monkeypatch) ───────────────────


@pytest.fixture
def gate_holder() -> Iterator[dict[str, object]]:
    """在组合根 seam 注入可编排闸(limiter/counter/oper 假端口)。"""
    import kongming.chat.dependencies as chat_deps

    state: dict[str, object] = {
        "limiter": _CountingLimiter(limit=1),
        "counter": _Counter(),
        "oper": _Oper(),
    }

    def _fake_build() -> ChatCostGate:
        return ChatCostGate(
            limiter=state["limiter"],  # type: ignore[arg-type]
            counter=state["counter"],  # type: ignore[arg-type]
            oper_logger=state["oper"],  # type: ignore[arg-type]
            rate_per_minute=1,  # 首问放行,次问触限
            quota_daily_calls=500,
            quota_monthly_calls=10_000,
        )

    orig = chat_deps._build_cost_gate
    chat_deps._build_cost_gate = _fake_build  # type: ignore[assignment]
    yield state
    chat_deps._build_cost_gate = orig  # type: ignore[assignment]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


from tests.test_chat_query import (  # noqa: E402  复用登录/权限脚手架(单源)
    FakeAgent,
    _install,
    _pass_reply,
    _user_with_chat_perm,
)


@pytest.fixture
def fake_agent() -> Iterator[dict[str, FakeAgent]]:
    """agent 替身注入(与 test_chat_query 的 fixture 同构;fixture 不过模块共享)。"""
    holder: dict[str, FakeAgent] = {}

    def _factory() -> FakeAgent:
        return holder["agent"]

    app.dependency_overrides[get_agent_client] = _factory
    yield holder
    app.dependency_overrides.pop(get_agent_client, None)


async def test_chat_query_rate_limited_skips_agent(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
    gate_holder: dict[str, object],
) -> None:
    """分钟限额后 429 CHAT_RATE_LIMITED,且 agent 零调用(未烧 token)。"""
    _, token = await _user_with_chat_perm(session, make_user, "cost1")
    agent = FakeAgent(_pass_reply())
    _install(fake_agent, agent)
    r1 = await client.post("/chat/query", json={"question": "有多少商品"}, headers=_auth(token))
    assert r1.status_code == 200
    r2 = await client.post("/chat/query", json={"question": "再来一问"}, headers=_auth(token))
    assert r2.status_code == 429
    assert r2.json()["detail"] == "CHAT_RATE_LIMITED"
    assert len(agent.calls) == 1  # 第二次根本没进 agent(零 token 消耗)


async def test_chat_query_quota_exceeded_429(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
    gate_holder: dict[str, object],
) -> None:
    """配额超限:429 detail=CHAT_QUOTA_EXCEEDED:…,X-Quota-Warning 回落。"""
    gate_holder["counter"] = _Counter(day=500)  # 日配额打满
    _, token = await _user_with_chat_perm(session, make_user, "cost2")
    _install(fake_agent, FakeAgent(_pass_reply()))
    r = await client.post("/chat/query", json={"question": "有多少商品"}, headers=_auth(token))
    assert r.status_code == 429
    assert r.json()["detail"].startswith("CHAT_QUOTA_EXCEEDED")
    assert r.headers.get("x-quota-warning")


async def test_chat_query_quota_unavailable_503(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
    gate_holder: dict[str, object],
) -> None:
    """配额读数失败:503 CHAT_QUOTA_UNAVAILABLE(fail-closed),agent 零调用。"""
    gate_holder["counter"] = _Counter(boom=True)
    _, token = await _user_with_chat_perm(session, make_user, "cost3")
    agent = FakeAgent(_pass_reply())
    _install(fake_agent, agent)
    r = await client.post("/chat/query", json={"question": "有多少商品"}, headers=_auth(token))
    assert r.status_code == 503
    assert r.json()["detail"] == "CHAT_QUOTA_UNAVAILABLE"
    assert agent.calls == []


async def test_chat_query_warn_header_on_success(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
    gate_holder: dict[str, object],
) -> None:
    """达预警线不拒绝:200 + X-Quota-Warning(450/500)。"""
    gate_holder["counter"] = _Counter(day=450)
    _, token = await _user_with_chat_perm(session, make_user, "cost4")
    _install(fake_agent, FakeAgent(_pass_reply()))
    r = await client.post("/chat/query", json={"question": "有多少商品"}, headers=_auth(token))
    assert r.status_code == 200
    assert r.headers.get("x-quota-warning") == "CHAT_DAILY_CALLS:450/500"


async def test_chat_stream_rate_limited_regular_json(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
    gate_holder: dict[str, object],
) -> None:
    """流式端点同款闸;前段失败以常规 JSON 429(非 SSE,防线同构)。"""
    _, token = await _user_with_chat_perm(session, make_user, "cost5")
    _install(fake_agent, FakeAgent(_pass_reply()))
    r1 = await client.post(
        "/chat/query/stream", json={"question": "有多少商品"}, headers=_auth(token)
    )
    assert r1.status_code == 200  # 首问放行
    r2 = await client.post(
        "/chat/query/stream", json={"question": "再来一问"}, headers=_auth(token)
    )
    assert r2.status_code == 429
    assert r2.json()["detail"] == "CHAT_RATE_LIMITED"
