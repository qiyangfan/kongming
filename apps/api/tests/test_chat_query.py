"""问答端点全栈测试(chat spec「问答查询端点」,安全规则类)。

真实 PG/Redis;agent 面经 dependency_overrides 替身(scripted AgentReply)。
安全面:未认证 401、无权限 403、撤销立即生效、租户白名单、会话归属同形 404。
"""


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
        # 010 标题改写:缺省 None=降级(保持截断标题);赋值字符串即模拟改写成功
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


async def _mk_role_with_perms(
    s: AsyncSession, role_key: str, perm_codes: list[str]
) -> str:
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
            await s.execute(
                text("SELECT id FROM public.sys_tenant WHERE tenant_key='default'")
            )
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


# ============ 安全面(测试先行) ============


async def test_unauthenticated_401(client: AsyncClient) -> None:
    r = await client.post("/chat/query", json={"question": "有多少商品"})
    assert r.status_code == 401


async def test_no_perm_403(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
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
    client: AsyncClient, session: AsyncSession, make_user: MakeUser,
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
    assert body["chart"]["type"] == "pie"  # 低基数分类 + 数值
    assert body["insights"] == [{"type": "max", "text": "连弩最多"}]

    sid = body["session_id"]
    sess = (
        await session.execute(
            text("SELECT title FROM public.chat_session WHERE id=:i"), {"i": sid}
        )
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
    client: AsyncClient, session: AsyncSession, make_user: MakeUser,
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
    # trace_id 生成并贯穿传给 agent(与执行审计同源,nl2sql spec)
    assert isinstance(agent.calls[0]["trace_id"], str)
    assert len(str(agent.calls[0]["trace_id"])) == 32  # uuid4 hex


async def test_super_admin_not_exempt_from_tenant_whitelist(
    client: AsyncClient, session: AsyncSession, fake_agent: dict[str, FakeAgent]
) -> None:
    """SUPER_ADMIN 不豁免租户白名单(multitenant spec 明文,005 端点级钉死)。"""
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
    client: AsyncClient, session: AsyncSession, make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    """BLOCKED 文案分态:DATA_SCOPE_UNSUPPORTED 不说「非只读」。"""
    _, token = await _user_with_chat_perm(client, session, make_user)
    reply = AgentReply(
        success=False, verdict="BLOCKED", error="DATA_SCOPE_UNSUPPORTED: 多主表"
    )
    _install(fake_agent, FakeAgent(reply))
    r = await client.post("/chat/query", json={"question": "q"}, headers=_auth(token))
    assert "数据范围" in r.json()["error"]


async def test_backend_history_fill(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    _, token = await _user_with_chat_perm(client, session, make_user)
    agent = FakeAgent(_pass_reply())
    _install(fake_agent, agent)
    # 先造 7 轮(第 7 轮发问时,此前 6 条 user 问句超 5 条补全上限)
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
    client: AsyncClient, session: AsyncSession, make_user: MakeUser,
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
    client: AsyncClient, session: AsyncSession, make_user: MakeUser,
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
    client: AsyncClient, session: AsyncSession, make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    _, token = await _user_with_chat_perm(client, session, make_user)
    _install(fake_agent, FakeAgent(boom=True))
    r = await client.post("/chat/query", json={"question": "有多少商品"}, headers=_auth(token))
    assert r.status_code == 200  # 不裸 500
    body = r.json()
    assert body["success"] is False
    assert "暂不可用" in body["error"] and "connect failed" not in body["error"]
    # R2-B1:不可达分支的数据源快照字段与原同步形态一致(重构曾丢失落 default)
    assert body["datasource_key"] == "default"
    status = (
        await session.execute(
            text(
                "SELECT status FROM public.chat_message WHERE role='ASSISTANT'"
                " AND session_id=:i"
            ),
            {"i": body["session_id"]},
        )
    ).scalar_one()
    assert status == "FAILED"


async def test_blocked_maps_rejected(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser,
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
            text(
                "SELECT status FROM public.chat_message WHERE role='ASSISTANT'"
                " AND session_id=:i"
            ),
            {"i": body["session_id"]},
        )
    ).scalar_one()
    assert status == "REJECTED"


async def test_error_maps_failed(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser,
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
            text(
                "SELECT status FROM public.chat_message WHERE role='ASSISTANT'"
                " AND session_id=:i"
            ),
            {"i": body["session_id"]},
        )
    ).scalar_one()
    assert status == "FAILED"


async def test_no_data_maps_neutral_status(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    """044:verdict=NO_DATA → 消息第四态;summary 承载中性文案,error 空无产物卡。"""
    _, token = await _user_with_chat_perm(client, session, make_user)
    reply = AgentReply(
        success=False, verdict="NO_DATA",
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
        await session.execute(
            text(
                "SELECT status, summary, CAST(result_json AS jsonb)->>'error' AS err,"
                " CAST(result_json AS jsonb)->'artifacts' AS artifacts"
                " FROM public.chat_message WHERE role='ASSISTANT' AND session_id=:i"
            ),
            {"i": body["session_id"]},
        )
    ).mappings().one()
    assert row["status"] == "NO_DATA"
    assert "未找到回答该问题所需的数据" in (row["summary"] or "")
    assert not row["err"]  # error 不落档(不进错误横幅)
    assert row["artifacts"] == []  # 如实说明不派生产物卡(asyncpg 已反序列化 jsonb)


async def test_failed_friendly_error_with_detail(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    """047:FAILED 文案用户语言,原始技术错误迁移 error_detail(响应+落档)。"""
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
    client: AsyncClient, session: AsyncSession, make_user: MakeUser,
    fake_agent: dict[str, FakeAgent],
) -> None:
    """047:保留表拦截文案中性化——不责备用户(模型越界是常见成因)。"""
    from kongming.chat.service import blocked_friendly

    assert blocked_friendly("RESERVED_TABLE:user_memory") == (
        "该问题涉及的数据不允许查询,请调整问法或咨询管理员"
    )
    # 其余分态语义不动
    assert blocked_friendly("DATA_SCOPE:x") == "该查询超出您的数据范围,暂不支持"
    assert blocked_friendly("GUARDRAIL:x") == "内容未通过安全检查,请调整后重试"


def test_chart_suggestion_rules() -> None:
    import datetime as dt

    from kongming.chat.models import suggest_chart

    # 首列日期 → line
    c = suggest_chart(["day", "n"], [[dt.date(2026, 8, 1), 1], [dt.date(2026, 8, 2), 2]])
    assert (c.type, c.x, c.y) == ("line", "day", "n")
    # 低基数分类(≤8)+ 数值 → pie
    c = suggest_chart(["name", "n"], [["a", 1], ["b", 2]])
    assert c.type == "pie"
    # 基数 9~50 分类 + 数值 → bar
    c = suggest_chart(["name", "n"], [[f"n{i}", i] for i in range(20)])
    assert c.type == "bar"
    # 高基数(明细推定)→ none
    c = suggest_chart(["name", "n"], [[f"n{i}", i] for i in range(60)])
    assert c.type == "none"
    # 无数值列 → none
    assert suggest_chart(["a", "b"], [["x", "y"]]).type == "none"
    # 单列 → none
    assert suggest_chart(["n"], [[1], [2]]).type == "none"
    # bool 不算数值
    assert suggest_chart(["name", "flag"], [["a", True]]).type == "none"


def test_chart_suggestion_scatter() -> None:
    """011 图表增强:双数值列 → scatter(chat spec「图表可视化」推断补全)。"""
    import datetime as dt
    from decimal import Decimal

    from kongming.chat.models import suggest_chart

    # 双数值列 → scatter,x=首列,y=首个数值列
    c = suggest_chart(["price", "rental"], [[9.9, 2], [4.5, 3]])
    assert (c.type, c.x, c.y) == ("scatter", "price", "rental")
    # y 跳过非数值列取首个数值列
    c = suggest_chart(["x", "label", "y"], [[1, "t", 2], [3, "u", 4]])
    assert (c.type, c.x, c.y) == ("scatter", "x", "y")
    # Decimal 双数值 → scatter
    c = suggest_chart(["a", "b"], [[Decimal("1.5"), 2]])
    assert c.type == "scatter"
    # 首列含 None 行的双数值 → scatter(行级容错,any 语义;x/y 取法仍须锁定)
    c = suggest_chart(["a", "b"], [[None, 1], [2.5, 3]])
    assert (c.type, c.x, c.y) == ("scatter", "a", "b")
    # 首列数值但无第二数值列 → none 维持(单数值列不猜)
    assert suggest_chart(["n", "name"], [[1, "a"], [2, "b"]]).type == "none"
    # 首列 temporal 优先 line,不被 scatter 分支截胡
    c = suggest_chart(["day", "n"], [[dt.date(2026, 8, 1), 1], [dt.date(2026, 8, 2), 2]])
    assert c.type == "line"


def test_chart_suggestion_scatter_any_position() -> None:
    """011 吸收(可读性修缮):scatter 判定解耦列序——prompt 让 LLM 附带标识列
    (名称/标题)后,双数值列可能不在前两列,推断按「任意位置的前两个数值列」取。"""
    from kongming.chat.models import suggest_chart

    # 标识列在前,双数值在后 → scatter,x/y=两个数值列(标识列不入图)
    c = suggest_chart(["title", "length", "rental_rate"], [["a", 86, 0.99], ["b", 48, 4.99]])
    assert (c.type, c.x, c.y) == ("scatter", "length", "rental_rate")
    # 单数值列 + 前置标识列(低基数)→ 走分类路径(pie),不误判
    c = suggest_chart(["title", "length"], [["a", 86], ["b", 48]])
    assert (c.type, c.x, c.y) == ("pie", "title", "length")
    # 单数值列 + 高基数标识列 → bar
    c = suggest_chart(["title", "length"], [[f"t{i}", i] for i in range(20)])
    assert (c.type, c.x, c.y) == ("bar", "title", "length")
    # 数值列在前的既有形态零回归
    c = suggest_chart(["length", "rental_rate"], [[86, 0.99], [48, 4.99]])
    assert (c.type, c.x, c.y) == ("scatter", "length", "rental_rate")
    # 双度量聚合形态(R2-F1):[分类, count, avg] 双数值列 → scatter 优先于 pie/bar
    # (手动切换器可一键回 pie/bar;推断优先级已进 011 delta spec)
    c = suggest_chart(
        ["rating", "cnt", "avg"],
        [["PG", 194, 2.1], ["R", 195, 2.3], ["G", 178, 2.0], ["PG-13", 223, 2.2]],
    )
    assert (c.type, c.x, c.y) == ("scatter", "cnt", "avg")
