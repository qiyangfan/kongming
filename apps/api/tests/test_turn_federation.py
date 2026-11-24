"""在途轮跨实例协作测试(share-turn-registry 控制面一侧)。

覆盖:登记/注销/定位的端口语义(含同名轮顶替与陈旧键),取消落在非持有实例时的
转发链路(200/非 200/传输失败/定位故障),归属校验跨实例不放松,以及内部面端点。
"""
# pyright: reportPrivateUsage=false

import asyncio
import json
from typing import Any, cast

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from kongming.chat import dependencies, turn_federation
from kongming.chat.domain_errors import TurnNotFound
from kongming.chat.inflight import TurnSettlement
from kongming.chat.turn_federation import RedisTurnFederation
from kongming.config import Settings
from kongming.redis_client import get_redis
from tests.test_chat_query import _pass_reply  # 测试助手按名字取用
from tests.test_chat_turn_cancel import (  # 测试助手按名字取用
    TID,
    _auth,
    _HangAgent,
    _stream_user,
)

SELF = "http://instance-self"
PEER = "http://instance-peer"
INTERNAL_PATH = "/internal/chat/turn-cancel"


class _FakeRedis:
    """最小 Redis 替身:get/set(带 ex)/eval(复刻「值匹配才删」的注销 Lua)。"""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.fail = False

    async def get(self, key: str) -> str | None:
        if self.fail:
            raise ConnectionError("redis down")
        return self.data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        if self.fail:
            raise ConnectionError("redis down")
        self.data[key] = value

    async def eval(self, _script: str, _numkeys: int, key: str, token: str) -> int:
        """复刻注销 Lua:解析值内 nonce,匹配才删;值损坏按不匹配。"""
        if self.fail:
            raise ConnectionError("redis down")
        raw = self.data.get(key)
        if raw:
            try:
                data = cast("dict[str, object]", json.loads(raw))
            except ValueError:
                data = {}
            if data.get("n") == token:
                del self.data[key]
                return 1
        return 0


class _Resp:
    def __init__(self, payload: object, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def json(self) -> object:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _FakeClient:
    """转发客户端替身:按脚本应答,并记录调用(路径/体)与构造参数(地址/令牌/超时)。"""

    def __init__(self, script: list[_Resp | Exception] | None = None) -> None:
        self.script = script or [_Resp({"outcome": "CANCELLED"})]
        self.calls: list[dict[str, Any]] = []
        self.init_kw: dict[str, Any] = {}

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *_a: object) -> None:
        return None

    async def post(self, path: str, **kw: Any) -> _Resp:
        self.calls.append({"path": path, **kw})
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def hang_agent() -> Any:
    """流式替身注入(与 test_chat_turn_cancel 同构;夹具不跨模块共享,本地重建)。"""
    from kongming.chat.dependencies import get_agent_client
    from kongming.main import app

    holder: dict[str, _HangAgent] = {}

    def _factory() -> _HangAgent:
        return holder["agent"]

    app.dependency_overrides[get_agent_client] = _factory
    yield holder
    app.dependency_overrides.pop(get_agent_client, None)


@pytest.fixture(autouse=True)
def _restore_httpx() -> Any:
    original = turn_federation.httpx.AsyncClient
    yield
    turn_federation.httpx.AsyncClient = original


def _settings(self_url: str) -> Settings:
    return Settings(
        api_self_base_url=self_url,
        turn_registry_ttl_sec=900,
        internal_token="test-internal-token",
    )


def _fed(redis: _FakeRedis, client: _FakeClient, *, self_url: str = SELF) -> RedisTurnFederation:
    def _factory(**kw: object) -> _FakeClient:
        client.init_kw = dict(kw)
        return client

    turn_federation.httpx.AsyncClient = _factory  # pyright: ignore[reportAttributeAccessIssue]
    return RedisTurnFederation(redis, _settings(self_url))  # pyright: ignore[reportArgumentType]


# ── 登记/注销/定位 ─────────────────────────────────────────────


async def test_claim_locate_release_roundtrip() -> None:
    """登记后可按 trace 定位到归属与持有地址;按凭据注销后不再可见。"""
    redis = _FakeRedis()
    fed = _fed(redis, _FakeClient())
    token = await fed.claim(trace_id=TID, user_id="u1")
    assert token
    located = await fed.locate(trace_id=TID)
    assert located is not None and located.user_id == "u1"
    assert located.base_url == SELF
    await fed.release(trace_id=TID, token=token)
    assert await fed.locate(trace_id=TID) is None


async def test_release_with_stale_token_keeps_newer_registration() -> None:
    """同名轮(客户端自带 trace_id 撞号)顶替后,旧凭据注销不删新登记。"""
    redis = _FakeRedis()
    fed = _fed(redis, _FakeClient())
    first = await fed.claim(trace_id=TID, user_id="u1")
    second = await fed.claim(trace_id=TID, user_id="u1")
    assert first is not None and second is not None and first != second
    await fed.release(trace_id=TID, token=first)
    assert await fed.locate(trace_id=TID) is not None
    await fed.release(trace_id=TID, token=second)
    assert await fed.locate(trace_id=TID) is None


async def test_locate_unknown_trace_is_none() -> None:
    redis = _FakeRedis()
    assert await _fed(redis, _FakeClient()).locate(trace_id="no-such-turn") is None


async def test_claim_failure_degrades_to_local_only() -> None:
    """登记不可用(Redis 故障)返回 None:该轮退化为仅本实例可见,不更糟。"""
    redis = _FakeRedis()
    redis.fail = True
    assert await _fed(redis, _FakeClient()).claim(trace_id=TID, user_id="u1") is None


# ── 跨实例取消转发 ─────────────────────────────────────────────


def _seeded_peer(redis: _FakeRedis, *, user: str = "u1", url: str = PEER) -> None:
    redis.data["chat:turn:" + TID] = json.dumps({"u": user, "url": url, "n": "peer"})


async def test_cancel_remote_forwards_and_returns_settlement() -> None:
    """定位到别实例:经内部面转发,200 原样回传;请求带内部令牌与归属用户。"""
    redis = _FakeRedis()
    client = _FakeClient()
    fed = _fed(redis, client)
    _seeded_peer(redis)
    settled = await fed.cancel_remote(trace_id=TID, user_id="u1")
    assert settled == TurnSettlement("CANCELLED", None)
    call = client.calls[0]
    assert call["path"] == INTERNAL_PATH
    assert call["json"] == {"trace_id": TID, "user_id": "u1"}
    assert client.init_kw["base_url"] == PEER
    assert client.init_kw["headers"]["X-Internal-Token"]


async def test_cancel_remote_foreign_user_is_turn_not_found() -> None:
    """定位到别处但归属他人:抛 TurnNotFound(端点映射 404,与不存在同形)。"""
    redis = _FakeRedis()
    client = _FakeClient()
    fed = _fed(redis, client)
    _seeded_peer(redis, user="owner")
    with pytest.raises(TurnNotFound):
        await fed.cancel_remote(trace_id=TID, user_id="intruder")
    assert client.calls == []


async def test_cancel_remote_self_url_is_not_forwarded() -> None:
    """登记指向本实例而本实例无该轮(陈旧键):不自我转发,走落库兜底(防自环)。"""
    redis = _FakeRedis()
    client = _FakeClient()
    fed = _fed(redis, client)
    _seeded_peer(redis, url=SELF)
    assert await fed.cancel_remote(trace_id=TID, user_id="u1") is None
    assert client.calls == []


async def test_cancel_remote_not_registered_anywhere() -> None:
    redis = _FakeRedis()
    client = _FakeClient()
    fed = _fed(redis, client)
    assert await fed.cancel_remote(trace_id="gone", user_id="u1") is None
    assert client.calls == []


@pytest.mark.parametrize("resp", [_Resp({"detail": "x"}, status=404), _Resp({}, status=500)])
async def test_cancel_remote_peer_failure_falls_back(resp: _Resp) -> None:
    """持有侧非 200(该轮在它那里也已不在途/内部故障):返回 None,落库兜底。"""
    redis = _FakeRedis()
    client = _FakeClient([resp])
    fed = _fed(redis, client)
    _seeded_peer(redis)
    assert await fed.cancel_remote(trace_id=TID, user_id="u1") is None


async def test_cancel_remote_transport_error_falls_back() -> None:
    redis = _FakeRedis()
    client = _FakeClient([httpx.ConnectError("peer down")])
    fed = _fed(redis, client)
    _seeded_peer(redis)
    assert await fed.cancel_remote(trace_id=TID, user_id="u1") is None


async def test_cancel_remote_registry_failure_falls_back() -> None:
    """定位读取失败(Redis 故障):返回 None,落库兜底,不伪造终态。"""
    redis = _FakeRedis()
    redis.fail = True
    client = _FakeClient()
    fed = _fed(redis, client)
    assert await fed.cancel_remote(trace_id=TID, user_id="u1") is None
    assert client.calls == []


# ── 集成:两实例 + 内部面端点 ─────────────────────────────────────


async def test_internal_endpoint_requires_internal_token(client: AsyncClient) -> None:
    """内部面无令牌/错令牌 403(中间件闸,与既有内部面同构)。"""
    missing = await client.post(INTERNAL_PATH, json={"trace_id": TID, "user_id": "u"})
    wrong = await client.post(
        INTERNAL_PATH,
        json={"trace_id": TID, "user_id": "u"},
        headers={"X-Internal-Token": "bad"},
    )
    assert missing.status_code == 403 and wrong.status_code == 403


async def test_cancel_lands_on_other_instance_reaches_owner(
    client: AsyncClient,
    session: Any,
    make_user: Any,
    hang_agent: dict[str, _HangAgent],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """两实例共享同一 federation:A 持轮在途,B 收到取消→经内部面转到 A→CANCELLED。

    A=应用默认组合根(monkeypatch 换成实例 A 的身份:本地表 turns_a + 自报地址 a);
    B=手工装配的第二个 service(另一张本地登记表 + 同一 Redis、自报地址 b)。
    """
    from sqlalchemy import text

    from kongming.chat.inflight import InflightTurns
    from kongming.chat.repository import ChatRepository
    from kongming.chat.service import ChatService
    from kongming.datasource.repository import SqlDatasourceRepo
    from kongming.db import get_sessionmaker
    from kongming.main import app

    uid, token = await _stream_user(session, make_user, "fed-a")
    agent = _HangAgent()
    hang_agent["agent"] = agent

    turns_a = InflightTurns()
    fed_a = RedisTurnFederation(get_redis(), _settings("http://instance-a"))
    fed_b = RedisTurnFederation(get_redis(), _settings("http://instance-b"))
    monkeypatch.setattr(dependencies, "_TURNS", turns_a)
    monkeypatch.setattr(dependencies, "_FEDERATION", fed_a)

    real_client = httpx.AsyncClient  # 先取真身:补丁是模块级的,厂内再取会取到自己

    def _asgi_factory(**kw: object) -> httpx.AsyncClient:
        # ASGI 传输不看主机,但 httpx 要绝对基址才解析相对路径:占位基址 + ASGI 传输
        kw["base_url"] = "http://asgi.invalid"
        kw["transport"] = ASGITransport(app=app)
        return real_client(**kw)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(turn_federation.httpx, "AsyncClient", _asgi_factory)

    stream = asyncio.create_task(
        client.post(
            "/chat/query/stream",
            json={"question": "慢查询"},
            headers={**_auth(token), "X-Trace-Id": TID},
        )
    )
    await asyncio.wait_for(agent.started.wait(), 5)
    # A 的本地登记与 Redis 登记都已发生;B 的本地表查不到这一轮
    assert [t.trace_id for t in turns_a.find_all(user_id=uid, trace_id=TID)] == [TID]
    assert await fed_b.locate(trace_id=TID) is not None

    async with get_sessionmaker()() as s2:
        svc_b = ChatService(
            ChatRepository(s2),
            agent,  # pyright: ignore[reportArgumentType]
            SqlDatasourceRepo(s2),
            turns=InflightTurns(),
            federation=fed_b,
            stream_store_factory=_stream_store(agent),
        )
        settled = await asyncio.wait_for(svc_b.cancel_turn(user_id=uid, trace_id=TID), 30)

    assert settled.outcome == "CANCELLED" and settled.message_id
    agent.finish.set()
    frames = _parse((await asyncio.wait_for(stream, 10)).text)
    assert [e for e, _ in frames] == ["stage", "result"]
    assert frames[-1][1]["verdict"] == "CANCELLED"
    rows = await session.execute(
        text(
            "SELECT m.role, m.status, m.trace_id FROM public.chat_message m"
            " JOIN public.chat_session s ON s.id = m.session_id"
            " WHERE s.user_id = :u ORDER BY m.created_at, m.id"
        ),
        {"u": uid},
    )
    assert [(str(r[0]), str(r[1]), r[2]) for r in rows.all()] == [
        ("USER", "SUCCESS", None),
        ("ASSISTANT", "CANCELLED", TID),
    ]


async def test_cancel_foreign_trace_from_other_instance_is_404(
    client: AsyncClient, session: Any, make_user: Any, hang_agent: dict[str, _HangAgent]
) -> None:
    """他人轮次经任何实例取消都 404 同形,且不影响该轮执行。"""
    from sqlalchemy import text

    uid, token = await _stream_user(session, make_user, "fed-b-a")
    _other, other_token = await _stream_user(session, make_user, "fed-b-b")
    agent = _HangAgent(tail=[("result", json.dumps(_pass_reply().__dict__))])
    hang_agent["agent"] = agent
    stream = asyncio.create_task(
        client.post(
            "/chat/query/stream",
            json={"question": "慢查询"},
            headers={**_auth(token), "X-Trace-Id": TID},
        )
    )
    await asyncio.wait_for(agent.started.wait(), 5)
    ghost = await client.post("/chat/query/stream/no-such/cancel", headers=_auth(other_token))
    other = await client.post(f"/chat/query/stream/{TID}/cancel", headers=_auth(other_token))
    assert other.status_code == 404 and ghost.status_code == 404
    assert other.json() == ghost.json()
    agent.release.set()
    agent.finish.set()
    frames = _parse((await asyncio.wait_for(stream, 10)).text)
    assert frames[-1][1]["success"] is True
    rows = await session.execute(
        text(
            "SELECT m.role, m.status FROM public.chat_message m"
            " JOIN public.chat_session s ON s.id = m.session_id"
            " WHERE s.user_id = :u ORDER BY m.created_at, m.id"
        ),
        {"u": uid},
    )
    assert [(str(r[0]), str(r[1])) for r in rows.all()] == [
        ("USER", "SUCCESS"),
        ("ASSISTANT", "SUCCESS"),
    ]


# ── 助手 ───────────────────────────────────────────────────────


def _stream_store(agent: _HangAgent) -> Any:
    from kongming.chat.dependencies import _stream_store_factory

    return _stream_store_factory(agent)  # pyright: ignore[reportArgumentType]


def _parse(text: str) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    event = ""
    for line in text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: ") :]
        elif line.startswith("data: ") and event:
            out.append((event, json.loads(line[len("data: ") :])))
    return out
