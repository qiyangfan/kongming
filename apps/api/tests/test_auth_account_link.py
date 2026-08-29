"""联邦账号关联与自动建号(auth spec「联邦账号关联与自动建号」)。"""

from collections.abc import Callable
from typing import Any, cast
from urllib.parse import parse_qs, urlparse

import httpx
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.models import NormalizedProfile
from kongming.auth.seed import seed_all
from kongming.config import get_settings
from tests.conftest import MakeUser


def _profile(**kw: object) -> NormalizedProfile:
    # 混合值域(含 None/bool)推断过宽,显式 Any 走 pydantic 强转
    base: dict[str, Any] = {
        "external_id": "42",
        "username": "octocat",
        "display_name": "The Octocat",
        "email": None,
        "email_verified": False,
        "avatar_url": None,
    }
    base.update(kw)
    return NormalizedProfile(**base)


async def _callback(client: AsyncClient, provider: str) -> httpx.Response:
    r = await client.get(f"/auth/{provider}/start")
    # parse_qs 对 Any 入参成员解析不出,cast 定型(运行时不变)
    authorize_url = cast("str", r.json()["authorize_url"])
    state = parse_qs(urlparse(authorize_url).query)["state"][0]
    return await client.post(f"/auth/{provider}/callback", json={"code": "c0de", "state": state})


async def _user_count(session: AsyncSession) -> int:
    return (await session.execute(text("SELECT count(*) FROM public.sys_user"))).scalar_one()


async def test_identity_hit_logs_in_directly(
    client: AsyncClient,
    fake_provider: Callable[[str, NormalizedProfile], None],
    make_user: MakeUser,
    session: AsyncSession,
) -> None:
    uid = await make_user("octocat", None)
    await session.execute(
        text(
            "INSERT INTO public.user_identities (id, user_id, provider, external_id)"
            " VALUES (gen_random_uuid(), :uid, 'GITHUB', '42')"
        ),
        {"uid": uid},
    )
    await session.commit()
    fake_provider("github", _profile())
    before = await _user_count(session)
    r = await _callback(client, "github")
    assert r.status_code == 200
    me = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}
    )
    assert me.json()["user_id"] == uid
    assert await _user_count(session) == before  # 未重复建号


async def test_verified_email_links_existing_account(
    client: AsyncClient,
    fake_provider: Callable[[str, NormalizedProfile], None],
    make_user: MakeUser,
    session: AsyncSession,
) -> None:
    uid = await make_user("localdev", "Passw0rd!", email="a@b.c")
    fake_provider("github", _profile(email="a@b.c", email_verified=True))
    r = await _callback(client, "github")
    assert r.status_code == 200
    me = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}
    )
    assert me.json()["user_id"] == uid  # 关联到既有账号
    assert await _user_count(session) == 1  # 未新建
    bound = (
        await session.execute(
            text(
                "SELECT count(*) FROM public.user_identities"
                " WHERE user_id=:uid AND provider='GITHUB'"
            ),
            {"uid": uid},
        )
    ).scalar_one()
    assert bound == 1


async def test_unverified_email_never_links(
    client: AsyncClient,
    fake_provider: Callable[[str, NormalizedProfile], None],
    make_user: MakeUser,
    session: AsyncSession,
) -> None:
    await seed_all(session, get_settings())
    uid = await make_user("localdev", "Passw0rd!", email="a@b.c")
    fake_provider("github", _profile(email="a@b.c", email_verified=False))
    r = await _callback(client, "github")
    assert r.status_code == 200
    me = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}
    )
    assert me.json()["user_id"] != uid  # 不关联,走自动建号(防邮箱伪造劫持)
    assert await _user_count(session) == 3  # admin + localdev + 新建


async def test_auto_provision_binds_default_role_tenant_and_logs(
    client: AsyncClient,
    fake_provider: Callable[[str, NormalizedProfile], None],
    session: AsyncSession,
) -> None:
    await seed_all(session, get_settings())
    fake_provider("github", _profile())
    r = await _callback(client, "github")
    assert r.status_code == 200
    me_resp = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}
    )
    me = me_resp.json()
    assert "DEVELOPER" in me["role_keys"]
    assert any(t["tenant_key"] == "default" for t in me["tenants"])
    logs = (
        (
            await session.execute(
                text("SELECT action FROM public.sys_oper_log WHERE user_id=:uid"),
                {"uid": me["user_id"]},
            )
        )
        .scalars()
        .all()
    )
    assert any("register" in a for a in logs)


async def test_username_conflict_gets_distinct_suffix(
    client: AsyncClient,
    fake_provider: Callable[[str, NormalizedProfile], None],
    make_user: MakeUser,
    session: AsyncSession,
) -> None:
    await seed_all(session, get_settings())
    await make_user("octocat", "Passw0rd!")
    fake_provider("github", _profile())
    r = await _callback(client, "github")
    assert r.status_code == 200
    names = (
        (
            await session.execute(
                text("SELECT username FROM public.sys_user WHERE username LIKE 'octocat%'")
            )
        )
        .scalars()
        .all()
    )
    assert len(names) == 2 and len(set(names)) == 2  # 冲突用户名已脱冲突


async def test_auto_provision_username_always_carries_provider_suffix(
    client: AsyncClient,
    fake_provider: Callable[[str, NormalizedProfile], None],
    session: AsyncSession,
) -> None:
    """联邦建号一律 {base}-{provider}:无冲突也不占裸名(来源可溯,003 e2e 后拍板)。"""
    await seed_all(session, get_settings())
    fake_provider("github", _profile())
    r = await _callback(client, "github")
    assert r.status_code == 200
    names = (
        (
            await session.execute(
                text("SELECT username FROM public.sys_user WHERE username LIKE 'octocat%'")
            )
        )
        .scalars()
        .all()
    )
    assert names == ["octocat-github"]


async def test_auto_provision_suffix_conflict_falls_back_to_numbered(
    client: AsyncClient,
    fake_provider: Callable[[str, NormalizedProfile], None],
    make_user: MakeUser,
    session: AsyncSession,
) -> None:
    """后缀名也被占时追加 -N(bounded 链不丢)。"""
    await seed_all(session, get_settings())
    await make_user("octocat-github", "Passw0rd!")
    fake_provider("github", _profile())
    r = await _callback(client, "github")
    assert r.status_code == 200
    names = (
        (
            await session.execute(
                text("SELECT username FROM public.sys_user WHERE username LIKE 'octocat%'")
            )
        )
        .scalars()
        .all()
    )
    assert names == ["octocat-github", "octocat-github-2"]


async def test_auto_provision_long_base_reserves_suffix_room(
    client: AsyncClient,
    fake_provider: Callable[[str, NormalizedProfile], None],
    session: AsyncSession,
) -> None:
    """超长 base(如 Google email 本地部分可达 64 字符)先预留后缀位再截断:
    脱冲突名 ≤64 且 provider 后缀完整(防截断后多候选同串误报失败)。"""
    await seed_all(session, get_settings())
    fake_provider("github", _profile(username="a" * 70))
    r = await _callback(client, "github")
    assert r.status_code == 200
    name = (
        await session.execute(
            text("SELECT username FROM public.sys_user WHERE username LIKE 'aaa%'")
        )
    ).scalar_one()
    assert len(name) <= 64 and name.endswith("-github")


async def test_multi_identity_binding(
    client: AsyncClient,
    fake_provider: Callable[[str, NormalizedProfile], None],
    make_user: MakeUser,
    session: AsyncSession,
) -> None:
    uid = await make_user("localdev", "Passw0rd!", email="a@b.c")
    fake_provider("github", _profile(external_id="gh1", email="a@b.c", email_verified=True))
    fake_provider(
        "google",
        _profile(external_id="gg1", username="octocat.gg", email="a@b.c", email_verified=True),
    )
    r1 = await _callback(client, "github")
    r2 = await _callback(client, "google")
    assert r1.status_code == 200 and r2.status_code == 200
    providers = (
        (
            await session.execute(
                text("SELECT provider FROM public.user_identities WHERE user_id=:uid ORDER BY 1"),
                {"uid": uid},
            )
        )
        .scalars()
        .all()
    )
    assert providers == ["GITHUB", "GOOGLE"]  # 两路联邦绑到同一账号


async def test_disabled_federated_account_forbidden(
    client: AsyncClient,
    fake_provider: Callable[[str, NormalizedProfile], None],
    make_user: MakeUser,
    session: AsyncSession,
) -> None:
    uid = await make_user("octocat", None, status="DISABLED")
    await session.execute(
        text(
            "INSERT INTO public.user_identities (id, user_id, provider, external_id)"
            " VALUES (gen_random_uuid(), :uid, 'GITHUB', '42')"
        ),
        {"uid": uid},
    )
    await session.commit()
    fake_provider("github", _profile())
    r = await _callback(client, "github")
    assert r.status_code == 403
