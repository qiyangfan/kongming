"""覆盖问答查询的租户隔离、权限和响应持久化。"""

from collections.abc import Iterator

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.chat.agent_client import AgentReply, AgentUnavailable
from kongming.chat.router import get_agent_client
from kongming.config import get_settings
from kongming.main import app
from tests.conftest import MakeUser


class FakeAgent:
    """scripted 替身:记录调用,按构造参数应答或抛 AgentUnavailable。"""

    def __init__(self, reply: AgentReply | None = None, *, boom: bool = False) -> None:
        self.reply = reply or AgentReply(success=True, verdict="PASS")
        self.boom = boom
        self.calls: list[dict[str, object]] = []
        # 标题改写:缺省 None=降级(保持截断标题);赋值字符串即模拟改写成功
        self.title_reply: str | None = None
        self.title_calls: list[str] = []

    async def query(self, **kw: object) -> AgentReply:
        self.calls.append(kw)
        if self.boom:
            raise AgentUnavailable("connect failed")
        return self.reply

    async def rewrite_title(self, *, question: str) -> str | None:
        self.title_calls.append(question)
        return self.title_reply


@pytest.fixture
def fake_agent() -> Iterator[dict[str, FakeAgent]]:
    holder: dict[str, FakeAgent] = {}

    def _factory() -> FakeAgent:
        return holder["agent"]

    app.dependency_overrides[get_agent_client] = _factory
    yield holder
    app.dependency_overrides.pop(get_agent_client, None)


def _install(holder: dict[str, FakeAgent], agent: FakeAgent) -> None:
    holder["agent"] = agent


async def _login(client: AsyncClient, username: str, password: str = "Passw0rd!") -> str:
    r = await client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200
    return str(r.json()["access_token"])


async def _mk_role_with_perms(s: AsyncSession, role_key: str, perm_codes: list[str]) -> str:
    rid = (
        await s.execute(
            text(
                "INSERT INTO public.sys_role (id, role_key, role_name, data_scope)"
                " VALUES (gen_random_uuid(), :k, :k, 'SELF') RETURNING id"
            ),
            {"k": role_key},
        )
    ).scalar_one()
    for code in perm_codes:
        await s.execute(
            text(
                "INSERT INTO public.sys_permission (id, perm_code, perm_name)"
                " VALUES (gen_random_uuid(), :c, :c) ON CONFLICT DO NOTHING"
            ),
            {"c": code},
        )
        await s.execute(
            text(
                "INSERT INTO public.sys_role_permission (role_id, permission_id)"
                " SELECT :r, p.id FROM public.sys_permission p WHERE p.perm_code = :c"
                " ON CONFLICT DO NOTHING"
            ),
            {"r": rid, "c": code},
        )
    await s.commit()
    return str(rid)


async def _bind(s: AsyncSession, table: str, user_id: str, target_id: str) -> None:
    col = "role_id" if table == "sys_user_role" else "tenant_id"
    await s.execute(
        text(
            f"INSERT INTO public.{table} (user_id, {col}) VALUES (:u, :t)"  # noqa: S608  # 测试常量
        ),
        {"u": user_id, "t": target_id},
    )
    await s.commit()


async def _default_tenant_id(s: AsyncSession) -> str:
    return str(
        (
            await s.execute(text("SELECT id FROM public.sys_tenant WHERE tenant_key='default'"))
        ).scalar_one()
    )


async def _user_with_chat_perm(
    client: AsyncClient, s: AsyncSession, make_user: MakeUser, username: str = "qa"
) -> tuple[str, str]:
    """造一个有 chat:execute + default 租户的用户,返回 (user_id, token)。"""
    await seed_all(s, get_settings())
    uid = await make_user(username)
    rid = (
        await s.execute(text("SELECT id FROM public.sys_role WHERE role_key='DEVELOPER'"))
    ).scalar_one()
    await _bind(s, "sys_user_role", uid, str(rid))
    await _bind(s, "sys_user_tenant", uid, await _default_tenant_id(s))
    return uid, await _login(client, username)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _pass_reply() -> AgentReply:
    return AgentReply(
        success=True,
        verdict="PASS",
        summary="共 2 种商品",
        insights=[{"type": "max", "text": "连弩最多"}],
        stats={"rows": 2, "qty_max": 30},
        columns=["name", "qty"],
        rows=[["连弩", 30], ["木牛流马", 12]],
        sql="SELECT name, qty FROM goods",
        retry_count=0,
    )


async def test_unauthenticated_401(client: AsyncClient) -> None:
    r = await client.post("/chat/query", json={"question": "有多少商品"})
    assert r.status_code == 401


async def test_no_perm_403(client: AsyncClient, session: AsyncSession, make_user: MakeUser) -> None:
    await seed_all(session, get_settings())
    uid = await make_user("noperm")
    rid = await _mk_role_with_perms(session, "NO_CHAT", [])  # 角色无任何权限点
    await _bind(session, "sys_user_role", uid, rid)
    await _bind(session, "sys_user_tenant", uid, await _default_tenant_id(session))
    token = await _login(client, "noperm")
    r = await client.post("/chat/query", json={"question": "有多少商品"}, headers=_auth(token))
    assert r.status_code == 403
    # 不新建会话、不落消息
    assert (
        await session.execute(text("SELECT count(*) FROM public.chat_session"))
    ).scalar_one() == 0


async def test_perm_revoke_effective_immediately(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    _uid, token = await _user_with_chat_perm(client, session, make_user, "revoked")
    await session.execute(
        text(
            "DELETE FROM public.sys_role_permission rp"
            " USING public.sys_permission p, public.sys_role r"
            " WHERE rp.permission_id=p.id AND rp.role_id=r.id"
            " AND p.perm_code='chat:execute' AND r.role_key='DEVELOPER'"
        )
    )
    await session.commit()
    r = await client.post("/chat/query", json={"question": "有多少商品"}, headers=_auth(token))
    assert r.status_code == 403  # 实时查库,撤销即生效(rbac spec)


async def test_tenant_not_bound_403(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    _, token = await _user_with_chat_perm(client, session, make_user, "tenantx")
    await session.execute(
        text(
            "INSERT INTO public.sys_tenant (id, tenant_key, tenant_name)"
            " VALUES (gen_random_uuid(), 'ghost', 'ghost')"
        )
    )
    await session.commit()
    r = await client.post(
        "/chat/query",
        json={"question": "有多少商品"},
        headers={**_auth(token), "X-Tenant-Id": "ghost"},
    )
    assert r.status_code == 403


# ============ 会话与消息 ============


async def test_happy_path_auto_session_and_messages(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    _, token = await _user_with_chat_perm(client, session, make_user)
    _install(fake_agent, FakeAgent(_pass_reply()))
    question = "各商品库存分别是多少,帮我看看哪个最多"
    r = await client.post("/chat/query", json={"question": question}, headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True and body["verdict"] == "PASS"
    assert body["summary"] == "共 2 种商品"
    assert body["columns"] == ["name", "qty"] and body["rows"][0] == ["连弩", 30]
    # 低基数分类+数值 → 首候选 bar;qty 裸列不可加(非 COUNT/SUM)pie 被闸
    assert body["chart"]["type"] == "bar"
    assert [c["type"] for c in body["chart_candidates"]] == ["bar"]
    # 统计键经 humanize(rows→行数;qty_max→物理名回退形态「qty 最高」)
    assert body["stats"] == {"行数": 2, "qty 最高": 30}
    assert body["insights"] == [{"type": "max", "text": "连弩最多"}]

    sid = body["session_id"]
    sess = (
        await session.execute(text("SELECT title FROM public.chat_session WHERE id=:i"), {"i": sid})
    ).scalar_one()
    assert sess == question[:30]  # 标题=首问前 30 字符
    msgs = (
        await session.execute(
            text(
                "SELECT role, status, sql_text, summary FROM public.chat_message"
                " WHERE session_id=:i ORDER BY created_at"
            ),
            {"i": sid},
        )
    ).all()
    assert [(m.role, m.status) for m in msgs] == [("USER", "SUCCESS"), ("ASSISTANT", "SUCCESS")]
    assert msgs[1].sql_text == "SELECT name, qty FROM goods"


async def test_explicit_history_wins(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    _, token = await _user_with_chat_perm(client, session, make_user)
    agent = FakeAgent(_pass_reply())
    _install(fake_agent, agent)
    r = await client.post(
        "/chat/query",
        json={"question": "那他呢", "history": ["问1", "问2"]},
        headers=_auth(token),
    )
    assert r.status_code == 200
    assert agent.calls[0]["history"] == ["问1", "问2"]  # 显式 history 原样透传
    # 生成的 trace_id 同时传给 agent 和执行审计。
    assert isinstance(agent.calls[0]["trace_id"], str)
    assert len(str(agent.calls[0]["trace_id"])) == 32  # uuid4 hex


async def test_super_admin_not_exempt_from_tenant_whitelist(
    client: AsyncClient, session: AsyncSession, fake_agent: dict[str, FakeAgent]
) -> None:
    """SUPER_ADMIN 仍受租户白名单约束。"""
    await seed_all(session, get_settings())
    _install(fake_agent, FakeAgent(_pass_reply()))
    token = await _login(client, "admin", "admin123")
    await session.execute(
        text(
            "INSERT INTO public.sys_tenant (id, tenant_key, tenant_name)"
            " VALUES (gen_random_uuid(), 'notbound', 'notbound')"
        )
    )
    await session.commit()
    r = await client.post(
        "/chat/query",
        json={"question": "q"},
        headers={**_auth(token), "X-Tenant-Id": "notbound"},
    )
    assert r.status_code == 403


async def test_rejected_copy_datascope_variant(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    """BLOCKED 文案分态:DATA_SCOPE_UNSUPPORTED 不说「非只读」。"""
    _, token = await _user_with_chat_perm(client, session, make_user)
    reply = AgentReply(success=False, verdict="BLOCKED", error="DATA_SCOPE_UNSUPPORTED: 多主表")
    _install(fake_agent, FakeAgent(reply))
    r = await client.post("/chat/query", json={"question": "q"}, headers=_auth(token))
    assert "数据范围" in r.json()["error"]


async def test_backend_history_fill(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    _, token = await _user_with_chat_perm(client, session, make_user)
    agent = FakeAgent(_pass_reply())
    _install(fake_agent, agent)
    # 先造 7 轮(第 7 轮发问时, 6 条 user 问句超 5 条补全上限)
    sid: str | None = None
    for i in range(7):
        payload: dict[str, object] = {"question": f"第{i}问"}
        if sid:
            payload["session_id"] = sid
        r = await client.post("/chat/query", json=payload, headers=_auth(token))
        assert r.status_code == 200
        sid = r.json()["session_id"]
    # 末次请求时消息表已有第0..5问,补全取最近 5 条(不含当前问句)
    assert agent.calls[-1]["history"] == ["第1问", "第2问", "第3问", "第4问", "第5问"]


async def test_persist_false_no_rows(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    _, token = await _user_with_chat_perm(client, session, make_user)
    _install(fake_agent, FakeAgent(_pass_reply()))
    r = await client.post(
        "/chat/query",
        json={"question": "有多少商品", "persist": False},
        headers=_auth(token),
    )
    assert r.status_code == 200
    assert r.json()["session_id"] is None
    assert (
        await session.execute(text("SELECT count(*) FROM public.chat_session"))
    ).scalar_one() == 0
    assert (
        await session.execute(text("SELECT count(*) FROM public.chat_message"))
    ).scalar_one() == 0


async def test_session_of_other_user_404(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    _, token_a = await _user_with_chat_perm(client, session, make_user, "usera")
    _install(fake_agent, FakeAgent(_pass_reply()))
    r = await client.post("/chat/query", json={"question": "q"}, headers=_auth(token_a))
    sid_a = r.json()["session_id"]
    _, token_b = await _user_with_chat_perm(client, session, make_user, "userb")
    r = await client.post(
        "/chat/query",
        json={"question": "q", "session_id": sid_a},
        headers=_auth(token_b),
    )
    assert r.status_code == 404  # 不存在与越权同形,不泄露信息


# ============ 失败映射 ============


async def test_agent_unavailable_friendly_error(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    _, token = await _user_with_chat_perm(client, session, make_user)
    _install(fake_agent, FakeAgent(boom=True))
    r = await client.post("/chat/query", json={"question": "有多少商品"}, headers=_auth(token))
    assert r.status_code == 200  # 不裸 500
    body = r.json()
    assert body["success"] is False
    assert "暂不可用" in body["error"] and "connect failed" not in body["error"]
    # 即使 agent 不可达,响应仍保留数据源快照。
    assert body["datasource_key"] == "default"
    status = (
        await session.execute(
            text("SELECT status FROM public.chat_message WHERE role='ASSISTANT' AND session_id=:i"),
            {"i": body["session_id"]},
        )
    ).scalar_one()
    assert status == "FAILED"


async def test_blocked_maps_rejected(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    _, token = await _user_with_chat_perm(client, session, make_user)
    reply = AgentReply(
        success=False, verdict="BLOCKED", sql="DELETE FROM goods", error="BLOCKED_ROOT: Delete"
    )
    _install(fake_agent, FakeAgent(reply))
    r = await client.post("/chat/query", json={"question": "删掉商品"}, headers=_auth(token))
    body = r.json()
    assert body["success"] is False and body["verdict"] == "BLOCKED"
    assert "非只读" in body["error"]
    status = (
        await session.execute(
            text("SELECT status FROM public.chat_message WHERE role='ASSISTANT' AND session_id=:i"),
            {"i": body["session_id"]},
        )
    ).scalar_one()
    assert status == "REJECTED"


async def test_error_maps_failed(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    _, token = await _user_with_chat_perm(client, session, make_user)
    reply = AgentReply(
        success=False, verdict="ERROR", error="column qtyy does not exist", retry_count=2
    )
    _install(fake_agent, FakeAgent(reply))
    r = await client.post("/chat/query", json={"question": "q"}, headers=_auth(token))
    body = r.json()
    assert body["success"] is False and body["verdict"] == "ERROR"
    assert body["retry_count"] == 2
    status = (
        await session.execute(
            text("SELECT status FROM public.chat_message WHERE role='ASSISTANT' AND session_id=:i"),
            {"i": body["session_id"]},
        )
    ).scalar_one()
    assert status == "FAILED"


async def test_no_data_maps_neutral_status(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    """verdict=NO_DATA → 消息第四态;summary 承载中性文案,error 空无产物卡。"""
    _, token = await _user_with_chat_perm(client, session, make_user)
    reply = AgentReply(
        success=False,
        verdict="NO_DATA",
        error="当前数据源未找到回答该问题所需的数据,可切换数据源或调整问法后重试",
    )
    _install(fake_agent, FakeAgent(reply))
    r = await client.post(
        "/chat/query",
        json={"question": "统计每个员工处理的租赁总数"},
        headers=_auth(token),
    )
    body = r.json()
    assert body["success"] is False and body["verdict"] == "NO_DATA"
    # 中性形态:不走「查询失败:」错误包装,说明文案经 summary 传达
    assert body["error"] is None
    assert "未找到回答该问题所需的数据" in body["summary"]
    row = (
        (
            await session.execute(
                text(
                    "SELECT status, summary, CAST(result_json AS jsonb)->>'error' AS err,"
                    " CAST(result_json AS jsonb)->'artifacts' AS artifacts"
                    " FROM public.chat_message WHERE role='ASSISTANT' AND session_id=:i"
                ),
                {"i": body["session_id"]},
            )
        )
        .mappings()
        .one()
    )
    assert row["status"] == "NO_DATA"
    assert "未找到回答该问题所需的数据" in (row["summary"] or "")
    assert not row["err"]  # error 不落档(不进错误横幅)
    assert row["artifacts"] == []  # 如实说明不派生产物卡(asyncpg 已反序列化 jsonb)


async def test_failed_friendly_error_with_detail(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    """FAILED 文案用户语言,原始技术错误迁移 error_detail(响应+落档)。"""
    _, token = await _user_with_chat_perm(client, session, make_user)
    raw = 'missing FROM-clause entry for table "s"'
    reply = AgentReply(success=False, verdict="ERROR", error=raw, retry_count=2)
    _install(fake_agent, FakeAgent(reply))
    r = await client.post(
        "/chat/query",
        json={"question": "查询每个门店库存中各影片分类的库存数量", "persist": False},
        headers=_auth(token),
    )
    body = r.json()
    assert body["verdict"] == "ERROR"
    assert body["error"] == "查询执行失败,请调整问法后重试"  # 用户语言
    assert body["error_detail"] == raw  # 原始错误原文(技术详情)


async def test_rejected_reserved_table_copy_neutral(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    """保留表拦截文案中性化——不责备用户(模型越界是常见成因)。"""
    from kongming.chat.service import blocked_friendly

    assert blocked_friendly("RESERVED_TABLE:user_memory") == (
        "该问题涉及的数据不允许查询,请调整问法或咨询管理员"
    )
    # 其余分态语义不动
    assert blocked_friendly("DATA_SCOPE:x") == "该查询超出您的数据范围,暂不支持"
    assert blocked_friendly("GUARDRAIL:x") == "内容未通过安全检查,请调整后重试"


def test_chart_candidates_rules() -> None:
    """图表候选:列角色画像→形状→有序候选(chat spec「图表可视化」)。"""
    import datetime as dt

    from kongming.chat.models import suggest_charts

    # 首列日期 → line 唯一候选
    c = suggest_charts(
        ["day", "n"],
        [[dt.date(2026, 8, 1), 1], [dt.date(2026, 8, 2), 2]],
        "SELECT day, COUNT(*) AS n FROM x GROUP BY day",
    )
    assert [(i.type, i.x, i.y, i.series) for i in c] == [("line", "day", ("n",), None)]
    # 单维低基数 + COUNT 别名(可加)→ bar 主、pie 辅
    c = suggest_charts(
        ["name", "cnt"],
        [["a", 1], ["b", 2]],
        "SELECT name, COUNT(*) AS cnt FROM x GROUP BY name",
    )
    assert [i.type for i in c] == ["bar", "pie"]
    # 无 sql → 可加性 fail-closed,pie 排除
    assert [i.type for i in suggest_charts(["name", "cnt"], [["a", 1], ["b", 2]])] == ["bar"]
    # AVG 不可加 → 仅 bar
    c = suggest_charts(
        ["name", "v"],
        [["a", 4.5], ["b", 3.2]],
        "SELECT name, AVG(len) AS v FROM x GROUP BY name",
    )
    assert [i.type for i in c] == ["bar"]
    # 负值 → pie 排除
    c = suggest_charts(
        ["name", "cnt"],
        [["a", -1], ["b", 2]],
        "SELECT name, COUNT(*) AS cnt FROM x GROUP BY name",
    )
    assert [i.type for i in c] == ["bar"]
    # 期间维(年份)非构成整体 → pie 排除
    c = suggest_charts(
        ["year", "cnt"],
        [[2005, 10], [2006, 12]],
        "SELECT year, COUNT(*) AS cnt FROM x GROUP BY year",
    )
    assert [i.type for i in c] == ["bar"]
    # 基数 9~50 → 仅 bar
    c = suggest_charts(
        ["name", "cnt"],
        [[f"n{i}", i] for i in range(20)],
        "SELECT name, COUNT(*) AS cnt FROM x GROUP BY name",
    )
    assert [i.type for i in c] == ["bar"]
    # 高基数(明细推定)→ 零候选
    assert (
        suggest_charts(["name", "cnt"], [[f"n{i}", i] for i in range(60)], "SELECT name FROM x")
        == []
    )
    # 无度量列/单列 → 零候选
    assert suggest_charts(["a", "b"], [["x", "y"]], "SELECT a, b FROM t") == []
    assert suggest_charts(["n"], [[1], [2]], "SELECT n FROM t") == []
    # bool 不算度量
    assert suggest_charts(["name", "flag"], [["a", True]], "SELECT name, flag FROM t") == []


def test_chart_candidates_identifier_dims() -> None:
    """标识列/期间列恒为维度(碎刻度根除),两维分组→分组柱状图。"""
    from kongming.chat.models import suggest_charts

    sql = (
        "SELECT i.store_id, c.name, COUNT(*) AS count FROM inventory i"
        " JOIN category c ON 1=1 GROUP BY 1, 2"
    )
    rows: list[list[object]] = [
        [1, "Action", 64],
        [1, "Animation", 66],
        [2, "Action", 43],
        [2, "Family", 58],
    ]
    c = suggest_charts(["store_id", "name", "count"], rows, sql)
    # 门店×分类:分组柱状图(系列=低基数维 store_id),不再出现 x=标识列的 scatter
    assert [(i.type, i.x, i.y, i.series) for i in c] == [("bar", "name", ("count",), "store_id")]
    # 小表明细(每行唯一 x 标签)分组柱退化 → 零候选(标签重复闸)
    c = suggest_charts(
        ["id", "title", "length"],
        [[1, "A", 120], [2, "B", 86]],
        "SELECT id, title, length FROM film",
    )
    assert c == []
    # 标识列单维+可加度量 → bar 主、pie 辅(门店库存构成是合法 part-of-whole)
    c = suggest_charts(
        ["store_id", "cnt"],
        [[1, 64], [2, 58]],
        "SELECT store_id, COUNT(*) AS cnt FROM x GROUP BY store_id",
    )
    assert [i.type for i in c] == ["bar", "pie"]
    # 稠密序列兜底:仅当降维后仍剩度量才生效(唯一小计数列不被降维)
    c = suggest_charts(
        ["name", "cnt"],
        [[f"n{i}", i] for i in range(1, 7)],
        "SELECT name, COUNT(*) AS cnt FROM x GROUP BY name",
    )
    assert [i.type for i in c] == ["bar", "pie"]
    # 时序+低基数分类维 → 多系列 line(系列=分类维)
    c = suggest_charts(
        ["day", "store", "cnt"],
        [["2025-07-01", 1, 10], ["2025-07-01", 2, 11]],
        "SELECT day, store, COUNT(*) AS cnt FROM x GROUP BY 1, 2",
    )
    assert [(i.type, i.x, i.y, i.series) for i in c] == [("line", "day", ("cnt",), "store")]


def test_chart_candidates_multi_measure_scatter() -> None:
    """双度量散点与多度量系列(y 列表);列序解耦语义保留。"""
    from decimal import Decimal

    from kongming.chat.models import suggest_charts

    # 双度量无维 → scatter(x/y=前两度量)
    c = suggest_charts(["price", "rental"], [[9.9, 2], [4.5, 3]], "SELECT price, rental FROM t")
    assert [(i.type, i.x, i.y) for i in c] == [("scatter", "price", ("rental",))]
    # Decimal 双度量 → scatter
    c = suggest_charts(["a", "b"], [[Decimal("1.5"), 2]], "SELECT a, b FROM t")
    assert [i.type for i in c] == ["scatter"]
    # 首列含 None 行的双度量 → scatter(行级容错,any 语义)
    c = suggest_charts(["a", "b"], [[None, 1], [2.5, 3]], "SELECT a, b FROM t")
    assert [(i.type, i.x, i.y) for i in c] == [("scatter", "a", ("b",))]
    # 多度量单维 → bar 多 y 系列 + pie(首度量可加)
    c = suggest_charts(
        ["name", "a", "b"],
        [["x", 1, 2], ["y", 3, 4]],
        "SELECT name, SUM(x) AS a, SUM(y) AS b FROM t GROUP BY name",
    )
    assert [(i.type, i.x, i.y) for i in c] == [("bar", "name", ("a", "b")), ("pie", "name", ("a",))]
    # 双度量无维 y 截前二;多度量单维 y 截前 3
    c = suggest_charts(["m1", "m2", "m3", "m4"], [[1, 2, 3, 4]], "SELECT m1, m2, m3, m4 FROM t")
    assert len(c) == 1 and c[0].type == "scatter" and c[0].y == ("m2",)
    assert c[0].x == "m1"


def test_humanize_stats() -> None:
    """统计键用户可读化(标签单源;歧义 fail-open;幂等)。"""
    from kongming.chat.models import humanize_stats

    stats: dict[str, object] = {
        "rows": 3,
        "inventory_count_max": 181,
        "inventory_count_min": 110,
        "inventory_count_avg": 143.16,
        "store_id_max": 2,
    }
    # 列标签单源:标签缺失回退物理列名;标识列统计已在 agent 侧过滤,此处仅验映射
    out = humanize_stats(stats, ["inventory_count", "store_id"], ["库存数量", "store_id"])
    assert out["行数"] == 3
    assert out["库存数量 最高"] == 181 and out["库存数量 最低"] == 110
    assert out["库存数量 均值"] == 143.16
    assert "inventory_count_max" not in out
    assert out["store_id 最高"] == 2  # 标签缺失回退物理列名
    # 列未命中(stats 键在结果列中无对应列)→ 保留原键 fail-open
    out = humanize_stats({"legacy_key_max": 1}, ["qty"], [])
    assert out["legacy_key_max"] == 1
    # 幂等:已中文化键二次应用原样保留(存量档读档归一路径)
    once = humanize_stats({"rows": 2, "qty_max": 30}, ["qty"], [])
    twice = humanize_stats(once, ["qty"], [])
    assert twice == {"行数": 2, "qty 最高": 30}


def test_chart_candidates_dim_priority() -> None:
    """类目维在场时比较类图型(bar)优先于散点;散点仅服务双度量无维形态
    (列序解耦后的收紧:标识/名称列在任意位置都不进度量池)。"""
    from kongming.chat.models import suggest_charts

    # [名称, 片长, 租金] → bar 多度量系列(类目比较优先),散点不再截胡
    c = suggest_charts(
        ["title", "length", "rental_rate"],
        [["a", 86, 0.99], ["b", 48, 4.99]],
        "SELECT title, length, rental_rate FROM film",
    )
    assert [(i.type, i.x, i.y) for i in c] == [("bar", "title", ("length", "rental_rate"))]
    # 单度量+分类维(无 sql 可加面)→ 仅 bar
    c = suggest_charts(
        ["title", "length"], [["a", 86], ["b", 48]], "SELECT title, length FROM film"
    )
    assert [i.type for i in c] == ["bar"]
    # 高基数类目(20)→ 仍 bar
    c = suggest_charts(
        ["title", "length"],
        [[f"t{i}", i] for i in range(20)],
        "SELECT title, length FROM film",
    )
    assert [(i.type, i.x, i.y) for i in c] == [("bar", "title", ("length",))]
    # 纯双度量(无类目维)→ scatter,度量列序无关
    c = suggest_charts(
        ["length", "rental_rate"],
        [[86, 0.99], [48, 4.99]],
        "SELECT length, rental_rate FROM film",
    )
    assert [(i.type, i.x, i.y) for i in c] == [("scatter", "length", ("rental_rate",))]
    # 聚合形态 [分类, count, avg]:bar 多度量为主,cnt 可加给 pie(首度量),avg 不升散点
    c = suggest_charts(
        ["rating", "cnt", "avg"],
        [["PG", 194, 2.1], ["R", 195, 2.3], ["G", 178, 2.0], ["PG-13", 223, 2.2]],
        "SELECT rating, COUNT(*) AS cnt, AVG(length) AS avg FROM film GROUP BY rating",
    )
    assert [(i.type, i.x, i.y) for i in c] == [
        ("bar", "rating", ("cnt", "avg")),
        ("pie", "rating", ("cnt",)),
    ]
