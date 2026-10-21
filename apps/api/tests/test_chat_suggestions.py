"""覆盖建议问题的租户隔离、数据源校验、缓存和生成失败处理。"""
# pyright: reportPrivateUsage=false

import asyncio
import json
from collections.abc import Iterator
from typing import Any, cast

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.chat.router import get_schema_overview, get_suggestor
from kongming.config import get_settings
from kongming.datasource.cipher import get_cipher
from kongming.main import app
from tests.conftest import MakeUser
from tests.test_chat_query import (
    _auth,
    _bind,
    _default_tenant_id,
    _login,
)
from tests.test_system_admin import _admin

SUG = "/chat/suggestions"
DS = "/system/datasource"


class FakeSuggestor:
    """scripted 生成替身:记录摘要入参,按脚本应答;delay 模拟 LLM 耗时。"""

    def __init__(self, questions: list[object] | None = None, *, delay: float = 0.0) -> None:
        self.questions: list[object] = (
            ["库存里有多少种商品?", "销量最高的前五类商品是哪些?", "最近一个月的订单趋势如何?"]
            if questions is None
            else questions
        )
        self.delay = delay
        self.digests: list[str] = []

    async def suggest_questions(self, digest: str) -> list[str]:
        self.digests.append(digest)
        if self.delay:
            await asyncio.sleep(self.delay)
        return cast("list[str]", list(self.questions))


class FakeOverview:
    """schema 摘要替身:记录调用;digest=None 模拟内省不可得。"""

    _DEFAULT = "demo.goods —— 商品表\n  - id uuid\n  - name text"

    def __init__(self, digest: str | None = _DEFAULT) -> None:
        self.digest = digest
        self.calls: list[dict[str, str]] = []

    async def overview(self, *, tenant_key: str, datasource_key: str) -> str | None:
        self.calls.append({"tenant_key": tenant_key, "datasource_key": datasource_key})
        return self.digest


@pytest.fixture
def suggestor() -> Iterator[FakeSuggestor]:
    fake = FakeSuggestor()
    app.dependency_overrides[get_suggestor] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_suggestor, None)


@pytest.fixture
def overview() -> Iterator[FakeOverview]:
    fake = FakeOverview()
    app.dependency_overrides[get_schema_overview] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_schema_overview, None)


async def _mk_tenant(s: AsyncSession, key: str) -> str:
    tid = (
        await s.execute(
            text(
                "INSERT INTO public.sys_tenant (id, tenant_key, tenant_name)"
                " VALUES (gen_random_uuid(), :k, :k) RETURNING id"
            ),
            {"k": key},
        )
    ).scalar_one()
    await s.commit()
    return str(tid)


async def _mk_ds(s: AsyncSession, key: str, tenant_id: str, *, status: str = "ACTIVE") -> None:
    enc = get_cipher(get_settings().datasource_encrypt_key).encrypt("kongming")
    await s.execute(
        text(
            "INSERT INTO public.sys_datasource"
            " (id, datasource_key, name, tenant_id, host, port, database, username,"
            "  password_enc, default_schema, status)"
            " VALUES (gen_random_uuid(), :k, :k, :t, '127.0.0.1', 5432, 'kongming_test',"
            "         'kongming', :pe, 'public', :st)"
        ),
        {"k": key, "t": tenant_id, "pe": enc, "st": status},
    )
    await s.commit()


async def _user_in_tenant(
    client: AsyncClient,
    s: AsyncSession,
    make_user: MakeUser,
    username: str,
    tenant_id: str | None = None,
) -> tuple[str, str]:
    """造一个有 chat:execute 的用户并绑定指定租户(缺省=default 租户),
    返回 (user_id, token)。seed_all 先行——default 租户由种子保证存在。"""
    await seed_all(s, get_settings())
    if tenant_id is None:
        tenant_id = await _default_tenant_id(s)
    uid = await make_user(username)
    rid = (
        await s.execute(text("SELECT id FROM public.sys_role WHERE role_key='DEVELOPER'"))
    ).scalar_one()
    await _bind(s, "sys_user_role", uid, str(rid))
    await _bind(s, "sys_user_tenant", uid, tenant_id)
    return uid, await _login(client, username)


async def _mk_cache(s: AsyncSession, tenant_id: str, key: str, questions: list[str]) -> None:
    await s.execute(
        text(
            "INSERT INTO public.chat_suggestion (id, tenant_id, datasource_key, questions)"
            " VALUES (gen_random_uuid(), :t, :k, CAST(:q AS jsonb))"
        ),
        {"t": tenant_id, "k": key, "q": json.dumps(questions, ensure_ascii=False)},
    )
    await s.commit()


async def _cache_rows(s: AsyncSession, key: str | None = None) -> list[dict[str, object]]:
    sql = "SELECT tenant_id, datasource_key, questions FROM public.chat_suggestion"
    params: dict[str, str] = {}
    if key is not None:
        sql += " WHERE datasource_key=:k"
        params["k"] = key
    rows = (await s.execute(text(sql), params)).mappings().all()
    return [dict(r) for r in rows]


async def test_first_visit_generates_then_cached(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    suggestor: FakeSuggestor,
    overview: FakeOverview,
) -> None:
    """首访懒生成落库;二访命中缓存不再生成。"""
    _, token = await _user_in_tenant(client, session, make_user, "sugqa")
    r1 = await client.get(SUG, headers=_auth(token))
    assert r1.status_code == 200, r1.text
    items = r1.json()["items"]
    assert len(items) == 3
    assert suggestor.digests and "goods" in suggestor.digests[0]  # 摘要进了生成
    assert overview.calls[0]["datasource_key"] == "default"
    assert len(await _cache_rows(session, "default")) == 1

    r2 = await client.get(SUG, headers=_auth(token))
    assert r2.json()["items"] == items
    assert len(suggestor.digests) == 1  # 缓存命中,未再生成


async def test_unusable_same_shape_no_generation(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    suggestor: FakeSuggestor,
    overview: FakeOverview,
) -> None:
    """伪造/禁用/越属数据源:同形空清单,不内省不生成(spec 防探测口径)。"""
    _, token = await _user_in_tenant(client, session, make_user, "sugqa2")
    await seed_all(session, get_settings())
    tid = await _default_tenant_id(session)
    other_tid = await _mk_tenant(session, "sug-other")
    await _mk_ds(session, "sugoff", tid, status="DISABLED")
    await _mk_ds(session, "sugother", other_tid)

    bodies: list[list[str]] = []
    for key in ("sugghost", "sugoff", "sugother"):
        r = await client.get(f"{SUG}?datasource_key={key}", headers=_auth(token))
        assert r.status_code == 200, key
        bodies.append(r.json()["items"])
    assert bodies == [[], [], []]  # 同形空清单
    assert suggestor.digests == [] and overview.calls == []
    assert await _cache_rows(session) == []


async def test_generation_failure_no_cache_then_retries(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    suggestor: FakeSuggestor,
    overview: FakeOverview,
) -> None:
    """生成失败收敛空清单且不落缓存;再次访问自然重试(自愈)。"""
    _, token = await _user_in_tenant(client, session, make_user, "sugqa3")
    suggestor.questions = []
    r = await client.get(SUG, headers=_auth(token))
    assert r.status_code == 200, (r.status_code, r.text)
    assert await _cache_rows(session) == []

    suggestor.questions = ["恢复后的建议"]
    r2 = await client.get(SUG, headers=_auth(token))
    assert r2.json()["items"] == ["恢复后的建议"]
    assert len(suggestor.digests) == 2  # 第二次真实重试
    assert len(await _cache_rows(session, "default")) == 1


async def test_api_side_validation_drops_bad_items(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    suggestor: FakeSuggestor,
    overview: FakeOverview,
) -> None:
    """产出防线:多行折叠保留/超长丢弃/去重/非字符串丢弃/全畸形降级不落缓存。"""
    _, token = await _user_in_tenant(client, session, make_user, "sugqa4")
    suggestor.questions = ["  多  行   问句 ", "x" * 150, "同一句", "同一句", 123, "   "]
    r = await client.get(SUG, headers=_auth(token))
    assert r.json()["items"] == ["多 行 问句", "同一句"]
    assert [row["questions"] for row in await _cache_rows(session, "default")] == [
        ["多 行 问句", "同一句"]
    ]

    suggestor.questions = ["", "   ", "y" * 200]
    r2 = await client.get(f"{SUG}?datasource_key=sugbad", headers=_auth(token))
    assert r2.json()["items"] == []
    assert [row for row in await _cache_rows(session) if row["datasource_key"] == "sugbad"] == []


async def test_overview_unavailable_degrades(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    suggestor: FakeSuggestor,
    overview: FakeOverview,
) -> None:
    """摘要不可得:空清单、不生成、不落缓存。"""
    _, token = await _user_in_tenant(client, session, make_user, "sugqa5")
    overview.digest = None
    r = await client.get(SUG, headers=_auth(token))
    assert r.status_code == 200 and r.json()["items"] == []
    assert suggestor.digests == []
    assert await _cache_rows(session) == []


async def test_cache_isolated_per_tenant(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    suggestor: FakeSuggestor,
    overview: FakeOverview,
) -> None:
    """缓存按租户分档:乙租户不读甲租户缓存;越绑租户 403。"""
    await seed_all(session, get_settings())
    tid_a = await _default_tenant_id(session)
    _, token_a = await _user_in_tenant(client, session, make_user, "suga", tid_a)
    r = await client.get(SUG, headers=_auth(token_a))
    assert len(r.json()["items"]) == 3

    tid_b = await _mk_tenant(session, "sug-tb")
    await _mk_cache(session, tid_b, "default", ["乙租户专属建议"])
    _, token_b = await _user_in_tenant(client, session, make_user, "sugb", tid_b)
    r2 = await client.get(SUG, headers=_auth(token_b))
    assert r2.json()["items"] == ["乙租户专属建议"]  # 乙读乙档,甲缓存不串
    assert len(suggestor.digests) == 1

    r3 = await client.get(SUG, headers={**_auth(token_a), "X-Tenant-Id": "sug-tb"})
    assert r3.status_code == 403  # 甲用户未绑定乙租户


async def test_malformed_key_422_and_unauthenticated_401(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    suggestor: FakeSuggestor,
) -> None:
    """畸形 key 传输层 422;未登录 401(与 chat 面同口径)。"""
    _, token = await _user_in_tenant(client, session, make_user, "sugqa6")
    r = await client.get(f"{SUG}?datasource_key=BAD-KEY", headers=_auth(token))
    assert r.status_code == 422
    r2 = await client.get(SUG)
    assert r2.status_code == 401


async def test_concurrent_first_visit_generates_once(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    overview: FakeOverview,
) -> None:
    """并发首访:advisory lock 串行化,至多一次生成,双方获得一致清单。"""
    slow = FakeSuggestor(delay=0.3)
    app.dependency_overrides[get_suggestor] = lambda: slow
    try:
        _, token = await _user_in_tenant(client, session, make_user, "sugqa7")
        r1, r2 = await asyncio.gather(
            client.get(SUG, headers=_auth(token)),
            client.get(SUG, headers=_auth(token)),
        )
        assert r1.status_code == r2.status_code == 200
        assert r1.json()["items"] == r2.json()["items"]
        assert len(r1.json()["items"]) == 3
        assert len(slow.digests) == 1  # 只生成一次(后到者取锁后命中)
        assert len(await _cache_rows(session, "default")) == 1
    finally:
        app.dependency_overrides.pop(get_suggestor, None)


# ---- datasource 管理变更级联失效(缓存生命周期) ----


def _update_body(row: dict[str, object], tenant_id: str, **over: object) -> dict[str, Any]:
    body: dict[str, object] = {
        "datasource_key": row["datasource_key"],
        "tenant_id": tenant_id,
        "name": row["name"],
        "host": row["host"],
        "port": row["port"],
        "database": row["database"],
        "username": row["username"],
        "password": None,
        "default_schema": row["default_schema"],
        "readonly": row["readonly"],
    }
    body.update(over)
    return body


async def _ds_id(session: AsyncSession, key: str) -> str:
    return str(
        (
            await session.execute(
                text("SELECT id FROM public.sys_datasource WHERE datasource_key=:k"),
                {"k": key},
            )
        ).scalar_one()
    )


async def test_connection_change_evicts_cache(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    suggestor: FakeSuggestor,
    overview: FakeOverview,
) -> None:
    """连接信息变更 → 该源各租户缓存失效;仅改 name 不失效。"""
    _, admin_token = await _admin(client, session, make_user, "sugboss")
    tid = await _default_tenant_id(session)
    await _mk_ds(session, "sugev", tid)
    _, token = await _user_in_tenant(client, session, make_user, "sugevu", tid)

    r = await client.get(f"{SUG}?datasource_key=sugev", headers=_auth(token))
    assert r.json()["items"]
    assert len(await _cache_rows(session, "sugev")) == 1

    row = (
        (
            await session.execute(
                text("SELECT * FROM public.sys_datasource WHERE datasource_key='sugev'")
            )
        )
        .mappings()
        .one()
    )
    dsid = await _ds_id(session, "sugev")

    # 仅改 name:缓存保留
    r2 = await client.put(
        f"{DS}/{dsid}",
        json=_update_body(dict(row), tid, name="改名不改连接"),
        headers=_auth(admin_token),
    )
    assert r2.status_code == 204, r2.text
    assert len(await _cache_rows(session, "sugev")) == 1

    # 改连接信息(host):缓存失效
    r3 = await client.put(
        f"{DS}/{dsid}",
        json=_update_body(dict(row), tid, host="10.0.0.9"),
        headers=_auth(admin_token),
    )
    assert r3.status_code == 204
    assert await _cache_rows(session, "sugev") == []


async def test_delete_datasource_evicts_cache(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    suggestor: FakeSuggestor,
    overview: FakeOverview,
) -> None:
    """删除数据源 → 缓存级联失效,不留孤儿行。"""
    _, admin_token = await _admin(client, session, make_user, "sugboss2")
    tid = await _default_tenant_id(session)
    await _mk_ds(session, "sugdel", tid)
    _, token = await _user_in_tenant(client, session, make_user, "sugdelu", tid)

    r = await client.get(f"{SUG}?datasource_key=sugdel", headers=_auth(token))
    assert r.json()["items"]
    assert len(await _cache_rows(session, "sugdel")) == 1

    dsid = await _ds_id(session, "sugdel")
    r2 = await client.delete(f"{DS}/{dsid}", headers=_auth(admin_token))
    assert r2.status_code == 204
    assert await _cache_rows(session, "sugdel") == []


# ---- Suggestor 降级契约单元锁定(真实 httpx 报文形态) ----


def test_agent_client_degrades_on_bad_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    """200 但报文非 JSON / 畸形结构 / generated=False → 一律空清单不抛异常。"""
    import httpx

    from kongming.chat.agent_client import AgentClient
    from kongming.config import get_settings

    class _Resp:
        def __init__(self, payload: object, status: int = 200) -> None:
            self._payload = payload
            self.status_code = status

        def json(self) -> object:
            if isinstance(self._payload, Exception):
                raise self._payload
            return self._payload

    class _Client:
        resp: _Resp

        def __init__(self, **_kw: object) -> None:
            pass

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *_a: object) -> None:
            return None

        async def post(self, _path: str, **_kw: object) -> _Resp:
            return self.resp

    captured: dict[str, _Resp] = {}

    def _factory(**kw: object) -> _Client:
        c = _Client(**kw)
        c.resp = captured["resp"]
        return c

    monkeypatch.setattr("kongming.chat.agent_client.httpx.AsyncClient", _factory)
    client = AgentClient(get_settings())

    # 200 但 HTML(中间层注入形态):json 抛 ValueError
    captured["resp"] = _Resp(ValueError("Expecting value"))
    assert client.suggest_questions.__name__ == "suggest_questions"
    import asyncio

    assert asyncio.run(client.suggest_questions("d")) == []

    # 200 但 generated=False(旧版 agent / 降级应答)
    captured["resp"] = _Resp({"generated": False, "questions": []})
    assert asyncio.run(client.suggest_questions("d")) == []

    # 200 但 questions 非 list
    captured["resp"] = _Resp({"generated": True, "questions": "oops"})
    assert asyncio.run(client.suggest_questions("d")) == []

    # 非 200
    captured["resp"] = _Resp({"detail": "x"}, status=403)
    assert asyncio.run(client.suggest_questions("d")) == []

    # 正常路径透传
    captured["resp"] = _Resp({"generated": True, "questions": ["a", "b"]})
    assert asyncio.run(client.suggest_questions("d")) == ["a", "b"]

    # httpx 未被误伤(引用保持)
    assert httpx.AsyncClient is not None
