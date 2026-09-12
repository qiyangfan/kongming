"""026 conversation-artifacts 测试:derive 纯函数 + rerun 安全矩阵(测试先行)。"""
# pyright: reportPrivateUsage=false

from collections.abc import AsyncIterator
from typing import Any, cast

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.config import get_settings
from kongming.db import get_engine
from tests.conftest import MakeUser
from tests.test_chat_query import _bind, _login

SCHEMA = "tenant_art"


@pytest.fixture(scope="module", autouse=True)
async def _biz() -> AsyncIterator[None]:  # pyright: ignore[reportUnusedFunction]
    async with get_engine().begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))  # noqa: S608
        await conn.execute(
            text(
                f'CREATE TABLE IF NOT EXISTS "{SCHEMA}".goods ('  # noqa: S608
                " id uuid PRIMARY KEY, name varchar(64), create_by varchar(64))"
            )
        )
        await conn.execute(text(f'DELETE FROM "{SCHEMA}".goods'))  # noqa: S608
        await conn.execute(
            text(
                f'INSERT INTO "{SCHEMA}"."goods" (id, name, create_by)'  # noqa: S608
                " VALUES (gen_random_uuid(), '连弩', 'someone')"
            )
        )
    yield
    async with get_engine().begin() as conn:
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE'))  # noqa: S608


# ── derive_artifacts 纯函数 ──


def test_derive_artifacts_full() -> None:
    from kongming.chat.models import ChartSuggestion, derive_artifacts

    out = derive_artifacts(
        sql="SELECT 1",
        chart=ChartSuggestion(type="bar", x="name", y="cnt"),
        summary="共 2 种",
        datasource_key="default",
    )
    artifacts = cast("list[dict[str, Any]]", out)
    types = [a["type"] for a in artifacts]
    assert types == ["sql", "chart", "document"]
    assert artifacts[0]["content"] == "SELECT 1"
    assert artifacts[1]["metadata"]["chart"]["type"] == "bar"
    assert artifacts[2]["content"] == "共 2 种"


def test_derive_artifacts_empty() -> None:
    from kongming.chat.models import ChartSuggestion, derive_artifacts

    out = derive_artifacts(
        sql="", chart=ChartSuggestion("none"), summary="", datasource_key="default"
    )
    assert out == []


# ── rerun 端点安全矩阵 ──


async def _mk_session_with_sql(
    s: AsyncSession, user_id: str, tenant_key: str = "art"
) -> tuple[str, str]:
    tid = (
        await s.execute(
            text(
                "INSERT INTO public.sys_tenant (id, tenant_key, tenant_name)"
                " VALUES (gen_random_uuid(), :k, :k) ON CONFLICT (tenant_key) DO NOTHING"
                " RETURNING id"
            ),
            {"k": tenant_key},
        )
    ).scalar_one_or_none()
    if tid is None:
        tid = (
            await s.execute(
                text("SELECT id FROM public.sys_tenant WHERE tenant_key=:k"),
                {"k": tenant_key},
            )
        ).scalar_one()
    await _bind(s, "sys_user_tenant", user_id, str(tid))
    sid = (
        await s.execute(
            text(
                "INSERT INTO public.chat_session (id, user_id, tenant_id, title, datasource_key)"
                " VALUES (gen_random_uuid(), :u, :t, 't', 'default') RETURNING id"
            ),
            {"u": user_id, "t": str(tid)},
        )
    ).scalar_one()
    mid = (
        await s.execute(
            text(
                "INSERT INTO public.chat_message (id, session_id, role, sql_text, status)"
                " VALUES (gen_random_uuid(), :s, 'ASSISTANT', 'SELECT name FROM goods', 'SUCCESS')"
                " RETURNING id"
            ),
            {"s": str(sid)},
        )
    ).scalar_one()
    await s.commit()
    return str(sid), str(mid)


async def test_rerun_happy_path(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    await seed_all(session, get_settings())
    uid = await make_user("art_run")
    rid = await session.execute(text("SELECT id FROM public.sys_role WHERE role_key='DEVELOPER'"))
    await _bind(session, "sys_user_role", uid, str(rid.scalar_one()))
    _, mid = await _mk_session_with_sql(session, uid)
    # DEVELOPER=data_scope SELF(create_by=本人):行主改为本用户,
    # 既证改写生效又证归属行可见(不动共享角色,避免跨测试泄漏)
    await session.execute(
        text(f'UPDATE "{SCHEMA}".goods SET create_by=:u'),  # noqa: S608
        {"u": uid},
    )
    await session.commit()
    token = await _login(client, "art_run")
    r = await client.post(
        "/chat/artifacts/rerun",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": "art"},
        json={"message_id": mid, "sql": "SELECT name FROM goods"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["rows"] == [["连弩"]]
    # 新消息落库且不覆盖原消息
    n = (
        await session.execute(
            text(
                "SELECT count(*) FROM public.chat_message WHERE session_id="
                " (SELECT session_id FROM public.chat_message WHERE id=:m)"
            ),
            {"m": mid},
        )
    ).scalar_one()
    assert n == 2
    # artifacts 派生含 sql 卡
    assert any(a["type"] == "sql" for a in body["artifacts"])


async def test_rerun_write_blocked(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    await seed_all(session, get_settings())
    uid = await make_user("art_w")
    rid = (
        await session.execute(text("SELECT id FROM public.sys_role WHERE role_key='DEVELOPER'"))
    ).scalar_one()
    await _bind(session, "sys_user_role", uid, str(rid))
    _, mid = await _mk_session_with_sql(session, uid)
    token = await _login(client, "art_w")
    r = await client.post(
        "/chat/artifacts/rerun",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": "art"},
        json={"message_id": mid, "sql": "DELETE FROM goods"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert body["verdict"] == "BLOCKED"
    # 审查循环 F5:拦截「不执行,不落新结果消息」——REJECTED assistant 消息
    # 不得入库(其 sql_text 会再派生可执行的 sql 卡);消息数与重跑前一致
    n = (
        await session.execute(
            text(
                "SELECT count(*) FROM public.chat_message"
                " WHERE session_id=(SELECT session_id FROM public.chat_message WHERE id=:m)"
            ),
            {"m": mid},
        )
    ).scalar_one()
    assert n == 1  # 仅 USER 原消息


async def test_rerun_reserved_table_blocked(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    await seed_all(session, get_settings())
    uid = await make_user("art_r")
    rid = (
        await session.execute(text("SELECT id FROM public.sys_role WHERE role_key='DEVELOPER'"))
    ).scalar_one()
    await _bind(session, "sys_user_role", uid, str(rid))
    _, mid = await _mk_session_with_sql(session, uid)
    token = await _login(client, "art_r")
    r = await client.post(
        "/chat/artifacts/rerun",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": "art"},
        json={"message_id": mid, "sql": "SELECT id FROM public.sys_user"},
    )
    assert r.json()["verdict"] == "BLOCKED"


async def test_rerun_other_user_404(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    await seed_all(session, get_settings())
    uid_a = await make_user("art_a")
    rid = (
        await session.execute(text("SELECT id FROM public.sys_role WHERE role_key='DEVELOPER'"))
    ).scalar_one()
    await _bind(session, "sys_user_role", uid_a, str(rid))
    _, mid = await _mk_session_with_sql(session, uid_a)
    uid_b = await make_user("art_b")
    await _bind(session, "sys_user_role", uid_b, str(rid))
    # B 绑定同租户(过租户解析),走到消息归属校验 → 越权同形 404
    art_tid = (
        await session.execute(text("SELECT id FROM public.sys_tenant WHERE tenant_key='art'"))
    ).scalar_one()
    await _bind(session, "sys_user_tenant", uid_b, str(art_tid))
    await session.commit()
    token_b = await _login(client, "art_b")
    r = await client.post(
        "/chat/artifacts/rerun",
        headers={"Authorization": f"Bearer {token_b}", "X-Tenant-Id": "art"},
        json={"message_id": mid, "sql": "SELECT 1"},
    )
    assert r.status_code == 404


async def test_rerun_output_guardrail_applied(session: AsyncSession, make_user: MakeUser) -> None:
    """R1-F7/F5 直证:rerun 输出经 020 护栏(脱敏落到响应/落库/产物卡),
    且护栏审计 trace_id 非空(执行与护栏同一 trace)。service 级直调,
    runner/mock 护栏替代六层执行(过检面由 happy path 覆盖)。"""
    from kongming.chat.repository import ChatRepository
    from kongming.chat.service import ChatService
    from kongming.datasource.repository import SqlDatasourceRepo

    await seed_all(session, get_settings())
    uid = await make_user("art_g")
    rid = (
        await session.execute(text("SELECT id FROM public.sys_role WHERE role_key='DEVELOPER'"))
    ).scalar_one()
    await _bind(session, "sys_user_role", uid, str(rid))
    _, mid = await _mk_session_with_sql(session, uid)

    calls: list[dict[str, object]] = []

    class _MockGuardrail:
        async def check_input(
            self, *, trace_id: str, user_id: str, tenant_id: str, texts: list[str]
        ) -> tuple[bool, list[str]]:
            return False, texts

        async def filter_output(
            self, *, trace_id: str, user_id: str, tenant_id: str, texts: list[str]
        ) -> list[str]:
            calls.append({"trace_id": trace_id, "texts": list(texts)})
            return [t.replace("goods", "***") for t in texts]

    class _MockRunner:
        async def run(
            self,
            *,
            trace_id: str,
            tenant_key: str,
            datasource_key: str,
            sql_text: str,
            user_id: str,
        ) -> object:
            class _Outcome:
                verdict = "PASS"
                reason = None
                columns = ["name"]
                rows = [["连弩"]]

            return _Outcome()

    svc = ChatService(
        ChatRepository(session),
        cast("Any", None),  # rerun 路径不触 agent
        SqlDatasourceRepo(session),
        guardrail=_MockGuardrail(),
        runner=cast("Any", _MockRunner()),
    )
    out = await svc.rerun_artifact(
        user_id=uid, tenant_key="art", message_id=mid, sql="SELECT name FROM goods"
    )
    assert out.success is True
    # 护栏被调且 trace 贯通(F5)
    assert len(calls) == 1
    assert str(calls[0]["trace_id"]) != ""
    # 脱敏落到响应 sql 与产物卡(F7)
    assert out.sql == "SELECT name FROM ***"
    sql_card = next(a for a in out.artifacts if a["type"] == "sql")
    assert sql_card["content"] == "SELECT name FROM ***"
    # 落库消息同步脱敏(回显读档一致)
    stored = (
        await session.execute(
            text("SELECT sql_text FROM public.chat_message WHERE id=:m"),
            {"m": out.message_id},
        )
    ).scalar_one()
    assert stored == "SELECT name FROM ***"
