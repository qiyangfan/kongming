"""覆盖知识条目的团队归属、可见性和成员离队后的处理。"""
# pyright: reportPrivateUsage=false

import asyncio
import hashlib
import math
from collections.abc import AsyncIterator
from typing import Any, cast

import pytest
from alembic.config import Config
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from alembic import command
from kongming.auth.seed import seed_all
from kongming.config import get_settings
from kongming.rag.agent_callback import AgentRagClient
from kongming.rag.models import TableBlock
from tests.conftest import MakeUser, migrated_tmp_db
from tests.test_chat_query import _auth, _bind, _login
from tests.test_toolcall import _mk_role_with_perms

INTERNAL = {"X-Internal-Token": "test-internal-token"}
EMB_DIM = 2560


def _fake_vector(seed_text: str) -> list[float]:
    digest = hashlib.sha256(seed_text.encode()).digest()
    vals = [
        ((digest[i % len(digest)] << 8 | digest[(i + 1) % len(digest)]) % 1000) / 1000.0
        for i in range(EMB_DIM)
    ]
    norm = math.sqrt(sum(v * v for v in vals)) or 1.0
    return [v / norm for v in vals]


@pytest.fixture
def fake_embed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(self: AgentRagClient, texts: list[str]) -> list[list[float]]:
        return [_fake_vector(t) for t in texts]

    monkeypatch.setattr(AgentRagClient, "embed", fake)


@pytest.fixture(scope="module", autouse=True)
async def _biz() -> AsyncIterator[None]:  # pyright: ignore[reportUnusedFunction]
    """tenant_default 业务表(上传 schema 闸与 fetch 内省的活数据)。"""
    from kongming.db import get_engine

    async with get_engine().begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS tenant_default"))
        await conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS tenant_default.goods"
                " (id int primary key, name text, create_by uuid)"
            )
        )
    yield
    async with get_engine().begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS tenant_default.goods"))
        await conn.execute(text("DROP SCHEMA IF EXISTS tenant_default CASCADE"))


# ============ arrange 助手(直插身份/团队/知识,独立于被测实现) ============


async def _ensure_default_tenant(s: AsyncSession) -> str:
    """default 租户幂等播种(_clean 每用例清身份表,先于一切 arrange)。"""
    row = (
        await s.execute(text("SELECT id FROM public.sys_tenant WHERE tenant_key='default'"))
    ).scalar_one_or_none()
    if row is not None:
        return str(row)
    return str(
        (
            await s.execute(
                text(
                    "INSERT INTO public.sys_tenant (id, tenant_key, tenant_name)"
                    " VALUES (gen_random_uuid(), 'default', 'default') RETURNING id"
                )
            )
        ).scalar_one()
    )


async def _mk_team(
    s: AsyncSession,
    tenant_id: str,
    key: str,
    *,
    members: dict[str, str] | None = None,
) -> str:
    """直插团队+成员(角色字典 user_id→role)。"""
    team_id = str(
        (
            await s.execute(
                text(
                    "INSERT INTO public.sys_team"
                    " (id, tenant_id, team_key, name, created_by)"
                    " VALUES (gen_random_uuid(), CAST(:t AS uuid), :k, :k,"
                    "         gen_random_uuid()) RETURNING id"
                ),
                {"t": tenant_id, "k": key},
            )
        ).scalar_one()
    )
    for uid, role in (members or {}).items():
        await s.execute(
            text(
                "INSERT INTO public.sys_team_member (team_id, user_id, role)"
                " VALUES (CAST(:t AS uuid), CAST(:u AS uuid), :r)"
            ),
            {"t": team_id, "u": uid, "r": role},
        )
    await s.commit()
    return team_id


async def _seed_knowledge(
    s: AsyncSession,
    tenant_id: str,
    *,
    ds: str,
    table: str,
    comment: str,
    embed_text: str,
    team_id: str | None = None,
    source: str = "MANUAL_UPLOAD",
    # 知识种子表为虚构表:放在图管辖范围([tenant_default, public])之外,
    # 避免被「图外表=残留」的提示词对齐逻辑剔除(该逻辑只处理管辖 schema 内)
    schema_name: str = "fixture_knowledge",
) -> str:
    """直插知识条目(team_id 可空=公共;返回行 id)。"""
    vec = "[" + ",".join(repr(v) for v in _fake_vector(embed_text)) + "]"
    kid = str(
        (
            await s.execute(
                text(
                    "INSERT INTO rag.table_descriptions"
                    " (id, tenant_id, datasource_key, schema_name, table_name,"
                    "  table_comment, columns_comment, sample_queries, source_type,"
                    "  embedding, tsv, team_id)"
                    " VALUES (gen_random_uuid(), :t, :d, :sn, :n, :c, '{}', '[]', :st,"
                    "  CAST(:vec AS vector),"
                    "  to_tsvector('simple', :n_t || ' ' || :c_t),"
                    "  CAST(:team AS uuid)) RETURNING id"
                ),
                {
                    "t": tenant_id,
                    "d": ds,
                    "sn": schema_name,
                    "n": table,
                    "c": comment,
                    "st": source,
                    "vec": vec,
                    "n_t": table,
                    "c_t": comment or " ",
                    "team": team_id,
                },
            )
        ).scalar_one()
    )
    await s.commit()
    return kid


async def _rag_mgr_token(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser, name: str
) -> tuple[str, str]:
    """持 system:rag:manage 的租户用户(返回 (user_id, token))。"""
    await seed_all(session, get_settings())
    uid = await make_user(name)
    rid = await _mk_role_with_perms(session, f"role_{name}", ["system:rag:manage"])
    await _bind(session, "sys_user_role", uid, rid)
    await _bind(session, "sys_user_tenant", uid, await _ensure_default_tenant(session))
    return uid, _login(uid)


def _md(*tables: str) -> bytes:
    return "\n".join(f"## {t}\n说明:{t}说明\n字段:id 主键\n" for t in tables).encode()


async def _upload(
    client: AsyncClient, token: str, content: bytes, *, team_id: str | None = None
) -> object:
    data: dict[str, str] = {"datasource_key": "default", "schema_name": "tenant_default"}
    if team_id is not None:
        data["team_id"] = team_id
    return await client.post(
        "/system/rag/documents/upload",
        files={"file": ("tables.md", content, "text/markdown")},
        data=data,
        headers=_auth(token),
    )


# ============ 可见集合判定/写权限纯函数(四形态矩阵) ============


def test_entry_visible_four_forms() -> None:
    """公共/成员/非成员/孤儿四形态:公共恒可见;团队条目仅调用者团队集合内
    可见;孤儿(团队已删→不在任何调用者集合)不可见不泄露。"""
    from kongming.rag.models import entry_visible

    # 公共(team 归属空):任何调用者可见(含无团队者)——存量行为
    assert entry_visible(team_id=None, caller_team_ids=frozenset()) is True
    assert entry_visible(team_id=None, caller_team_ids=frozenset({"t1"})) is True
    # 成员:条目团队 ∈ 调用者团队集合
    assert entry_visible(team_id="t1", caller_team_ids=frozenset({"t1", "t2"})) is True
    # 非成员:条目团队 ∉ 调用者集合
    assert entry_visible(team_id="t1", caller_team_ids=frozenset({"t2"})) is False
    assert entry_visible(team_id="t1", caller_team_ids=frozenset()) is False
    # 孤儿:团队已删(成员行连带清空→不在任何集合)与非成员同形不可见
    assert entry_visible(team_id="ghost", caller_team_ids=frozenset({"t1"})) is False


def test_entry_write_access_matrix() -> None:
    """写权限三态:公共条目=既有权限口径(allowed);团队条目 OWNER/MEMBER
    allowed,VIEWER=viewer(403),非成员=not_member(404 同形)。"""
    from kongming.rag.models import entry_write_access

    assert entry_write_access(team_id=None, member_role=None) == "allowed"
    assert entry_write_access(team_id=None, member_role="VIEWER") == "allowed"
    assert entry_write_access(team_id="t1", member_role="OWNER") == "allowed"
    assert entry_write_access(team_id="t1", member_role="MEMBER") == "allowed"
    assert entry_write_access(team_id="t1", member_role="VIEWER") == "viewer"
    assert entry_write_access(team_id="t1", member_role=None) == "not_member"


def test_team_set_fingerprint_rules() -> None:
    """团队集合确定性指纹:空集=固定串;同集合异序同指纹;异集合异指纹。"""
    from kongming.rag.models import team_set_fingerprint

    assert team_set_fingerprint([]) == "public"
    assert team_set_fingerprint([]) == team_set_fingerprint(())
    a = team_set_fingerprint(["t1", "t2"])
    assert a == team_set_fingerprint(["t2", "t1"])  # 排序确定性
    assert a != team_set_fingerprint(["t1"])
    assert a != team_set_fingerprint(["t1", "t2", "t3"])
    assert len(a) == 16  # 短指纹(键段可读,碰撞面 2^64 足够)


# ============ 迁移 0048:升级/降级往返 + 存量行归属空 ============


async def _run_alembic(session: AsyncSession, cfg: Config, target: str) -> None:
    """执行一条 alembic 命令(前 rollback 清场,防 ACCESS SHARE 死等)。
    cfg 由 migrated_tmp_db 注入(一次性临时库,防 dropped 列占位累积)。"""
    await session.rollback()
    if target == "head":
        await asyncio.to_thread(command.upgrade, cfg, "head")
    else:
        await asyncio.to_thread(command.downgrade, cfg, target)


async def test_migration_0048_roundtrip_and_legacy_null() -> None:
    """down 到 0047 → 存量形态插行 → 升 head:列存在且存量行归属全空;
    再降/升往返列随之出现消失。"""
    async with migrated_tmp_db() as (session, cfg):
        await _run_alembic(session, cfg, "0047")
        tid = str(
            (
                await session.execute(
                    text(
                        "INSERT INTO public.sys_tenant (id, tenant_key, tenant_name)"
                        " VALUES (gen_random_uuid(), 'mig102', '迁移租户') RETURNING id"
                    )
                )
            ).scalar_one()
        )
        await session.execute(
            text(
                "INSERT INTO rag.table_descriptions"
                " (id, tenant_id, datasource_key, schema_name, table_name,"
                "  table_comment, columns_comment, sample_queries, source_type)"
                " VALUES (gen_random_uuid(), CAST(:t AS uuid), 'default', 's', 'legacy_t',"
                "  'c', '{}', '[]', 'MANUAL_UPLOAD')"
            ),
            {"t": tid},
        )
        await session.commit()
        await _run_alembic(session, cfg, "head")
        col = (
            await session.execute(
                text(
                    "SELECT count(*) FROM information_schema.columns"
                    " WHERE table_schema='rag' AND table_name='table_descriptions'"
                    " AND column_name='team_id'"
                )
            )
        ).scalar_one()
        assert col == 1
        # 存量行归属空断言(全 NULL=公共)
        legacy = (
            await session.execute(
                text("SELECT team_id FROM rag.table_descriptions WHERE table_name='legacy_t'")
            )
        ).scalar_one()
        assert legacy is None
        # 降级:列消失;再回 head:契约复现
        await _run_alembic(session, cfg, "0047")
        col = (
            await session.execute(
                text(
                    "SELECT count(*) FROM information_schema.columns"
                    " WHERE table_schema='rag' AND table_name='table_descriptions'"
                    " AND column_name='team_id'"
                )
            )
        ).scalar_one()
        assert col == 0
        await _run_alembic(session, cfg, "head")
        col = (
            await session.execute(
                text(
                    "SELECT count(*) FROM information_schema.columns"
                    " WHERE table_schema='rag' AND table_name='table_descriptions'"
                    " AND column_name='team_id'"
                )
            )
        ).scalar_one()
        assert col == 1


# ============ 召回过滤 + 缓存团队指纹(全程禁 flush) ============


async def _member_and_outsider(session: AsyncSession, make_user: MakeUser) -> tuple[str, str, str]:
    """(team_id, 成员 user_id, 非成员 user_id)——default 租户内团队。"""
    member_uid = await make_user("t_member")
    out_uid = await make_user("t_out")
    tid = await _ensure_default_tenant(session)
    team_id = await _mk_team(session, tid, "scope_t", members={member_uid: "MEMBER"})
    return team_id, member_uid, out_uid


async def test_team_entry_member_recall_nonmember_hidden(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser, fake_embed: None
) -> None:
    """团队条目:成员可召回,非成员与匿名(缺 user_id)不出现;公共条目不受影响。"""
    tid = await _ensure_default_tenant(session)
    team_id, member_uid, out_uid = await _member_and_outsider(session, make_user)
    await _seed_knowledge(
        session, tid, ds="default", table="pub_t", comment="公共表", embed_text="订单 orders"
    )
    await _seed_knowledge(
        session,
        tid,
        ds="default",
        table="team_t",
        comment="团队表",
        embed_text="订单 orders",
        team_id=team_id,
    )
    body = (
        await client.post(
            "/internal/rag/retrieve",
            json={"tenant_key": "default", "question": "订单", "user_id": member_uid},
            headers=INTERNAL,
        )
    ).json()
    assert "pub_t" in body["schema_hint"] and "team_t" in body["schema_hint"]
    body_out = (
        await client.post(
            "/internal/rag/retrieve",
            json={"tenant_key": "default", "question": "订单", "user_id": out_uid},
            headers=INTERNAL,
        )
    ).json()
    assert "pub_t" in body_out["schema_hint"]
    assert "team_t" not in body_out["schema_hint"]  # 非成员 MUST NOT 出现
    body_anon = (
        await client.post(
            "/internal/rag/retrieve",
            json={"tenant_key": "default", "question": "订单"},
            headers=INTERNAL,
        )
    ).json()
    assert "team_t" not in body_anon["schema_hint"]  # 缺身份=纯公共形态


async def test_cache_split_by_team_set_no_flush(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser, fake_embed: None
) -> None:
    """缓存不跨可见集合(全程零 flushdb):成员先召回(缓存含团队条目),
    非成员同问句不得命中成员缓存;双方各自缓存键并存。"""
    from kongming.redis_client import get_redis

    tid = await _ensure_default_tenant(session)
    team_id, member_uid, out_uid = await _member_and_outsider(session, make_user)
    # 公共条目保证两侧结果均非空(空结果不写缓存——既有纪律)
    await _seed_knowledge(
        session, tid, ds="default", table="pub_t", comment="公共库存", embed_text="库存 stock"
    )
    await _seed_knowledge(
        session,
        tid,
        ds="default",
        table="team_t",
        comment="团队表",
        embed_text="库存 stock",
        team_id=team_id,
    )
    q = {"tenant_key": "default", "question": "库存"}
    r_member = await client.post(
        "/internal/rag/retrieve", json={**q, "user_id": member_uid}, headers=INTERNAL
    )
    assert "team_t" in r_member.json()["schema_hint"]
    r_out = await client.post(
        "/internal/rag/retrieve", json={**q, "user_id": out_uid}, headers=INTERNAL
    )
    # 非成员若误命中成员缓存,team_t 会泄漏进其结果
    assert "team_t" not in r_out.json()["schema_hint"]
    keys = [k async for k in cast("Any", get_redis()).scan_iter(match="km:ragctx:*")]
    assert len(keys) == 2  # 两个可见集合各持一条(异指纹自然隔离)
    # 成员复问仍命中自己缓存(结果不变)
    r_again = await client.post(
        "/internal/rag/retrieve", json={**q, "user_id": member_uid}, headers=INTERNAL
    )
    assert r_again.json() == r_member.json()


async def test_membership_change_reflected_without_flush(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser, fake_embed: None
) -> None:
    """团队关系变化即时生效(零 flush):移出团队后指纹变,新召回不含团队条目。"""
    from kongming.redis_client import get_redis

    tid = await _ensure_default_tenant(session)
    team_id, member_uid, _ = await _member_and_outsider(session, make_user)
    # 公共条目保证移出后新集合结果非空(空结果不写缓存,键数断言才有意义)
    await _seed_knowledge(
        session, tid, ds="default", table="pub_t", comment="公共客户表", embed_text="客户"
    )
    await _seed_knowledge(
        session,
        tid,
        ds="default",
        table="team_t",
        comment="团队表",
        embed_text="客户",
        team_id=team_id,
    )
    q = {"tenant_key": "default", "question": "客户", "user_id": member_uid}
    r1 = await client.post("/internal/rag/retrieve", json=q, headers=INTERNAL)
    assert "team_t" in r1.json()["schema_hint"]
    await session.execute(
        text(
            "DELETE FROM public.sys_team_member"
            " WHERE team_id=CAST(:t AS uuid) AND user_id=CAST(:u AS uuid)"
        ),
        {"t": team_id, "u": member_uid},
    )
    await session.commit()
    r2 = await client.post("/internal/rag/retrieve", json=q, headers=INTERNAL)
    assert "team_t" not in r2.json()["schema_hint"]  # 新可见集合生效(键异不互命中)
    assert len([k async for k in cast("Any", get_redis()).scan_iter(match="km:ragctx:*")]) == 2


# ============ 治理面:上传归属/列表筛选/404 同形/VIEWER 403 ============


async def test_upload_with_team_and_list_scope_filters(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """上传携带归属(成员可写);列表默认=公共∪我所在团队,team_scope 筛选
    公共/指定团队;非成员管理员的列表不含团队条目。"""
    member_uid, member_token = await _rag_mgr_token(client, session, make_user, "mgr_m")
    _, out_token = await _rag_mgr_token(client, session, make_user, "mgr_o")
    tid = await _ensure_default_tenant(session)
    team_id = await _mk_team(session, tid, "gov_t", members={member_uid: "MEMBER"})
    await _seed_knowledge(session, tid, ds="default", table="pub_g", comment="公共", embed_text="x")
    r = await _upload(client, member_token, _md("team_g"), team_id=team_id)
    assert r.status_code == 200, r.text  # type: ignore[attr-defined]
    row = (
        await session.execute(
            text("SELECT team_id FROM rag.table_descriptions WHERE table_name='team_g'")
        )
    ).scalar_one()
    assert row is not None and str(row) == team_id

    # 成员列表:公共+团队;公共筛选;团队筛选
    names = [
        i["table_name"]
        for i in (await client.get("/system/rag/tables", headers=_auth(member_token))).json()[
            "items"
        ]
    ]
    assert set(names) == {"pub_g", "team_g"}
    pub_names = [
        i["table_name"]
        for i in (
            await client.get(
                "/system/rag/tables", params={"team_scope": "public"}, headers=_auth(member_token)
            )
        ).json()["items"]
    ]
    assert pub_names == ["pub_g"]
    team_names = [
        i["table_name"]
        for i in (
            await client.get(
                "/system/rag/tables",
                params={"team_scope": team_id},
                headers=_auth(member_token),
            )
        ).json()["items"]
    ]
    assert team_names == ["team_g"]
    # 归属列携带团队名
    item = (
        await client.get(
            "/system/rag/tables",
            params={"team_scope": team_id},
            headers=_auth(member_token),
        )
    ).json()["items"][0]
    assert item["team_id"] == team_id and item["team_name"] == "gov_t"

    # 非成员管理员:默认只见公共;指定团队筛选为空(不泄露存在性)
    out_names = [
        i["table_name"]
        for i in (await client.get("/system/rag/tables", headers=_auth(out_token))).json()["items"]
    ]
    assert out_names == ["pub_g"]
    out_team = await client.get(
        "/system/rag/tables", params={"team_scope": team_id}, headers=_auth(out_token)
    )
    assert [i["table_name"] for i in out_team.json()["items"]] == []


async def test_nonmember_write_404_same_shape(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """非成员(持知识管理权限)编辑/删除/归属变更团队条目:404 同形拒绝
    (与不存在条目同 detail,不暴露存在性),条目不变。"""
    _, out_token = await _rag_mgr_token(client, session, make_user, "wr_o")
    tid = await _ensure_default_tenant(session)
    team_id = await _mk_team(session, tid, "wr_t")
    kid = await _seed_knowledge(
        session,
        tid,
        ds="default",
        table="hidden_t",
        comment="c",
        embed_text="x",
        team_id=team_id,
    )
    r_edit = await client.put(
        f"/system/rag/tables/{kid}",
        json={"table_comment": "x", "alias": "y"},
        headers=_auth(out_token),
    )
    r_edit_missing = await client.put(
        f"/system/rag/tables/{'0' * 8}-0000-0000-0000-000000000000",
        json={"table_comment": "x", "alias": "y"},
        headers=_auth(out_token),
    )
    assert r_edit.status_code == 404
    assert r_edit.json()["detail"] == r_edit_missing.json()["detail"]  # 同形
    r_del = await client.delete(f"/system/rag/tables/{kid}", headers=_auth(out_token))
    assert r_del.status_code == 404
    r_own = await client.put(
        f"/system/rag/tables/{kid}/ownership", json={"team_id": None}, headers=_auth(out_token)
    )
    assert r_own.status_code == 404
    row = (
        (
            await session.execute(
                text(
                    "SELECT team_id, table_comment FROM rag.table_descriptions"
                    " WHERE id=CAST(:i AS uuid)"
                ),
                {"i": kid},
            )
        )
        .mappings()
        .one()
    )
    assert str(row["team_id"]) == team_id and row["table_comment"] == "c"  # 未被触碰


async def test_viewer_member_write_403(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """VIEWER 成员可读不可写:写操作 403(角色不足;可见集合内,非 404)。"""
    viewer_uid, viewer_token = await _rag_mgr_token(client, session, make_user, "wr_v")
    tid = await _ensure_default_tenant(session)
    team_id = await _mk_team(session, tid, "vw_t", members={viewer_uid: "VIEWER"})
    kid = await _seed_knowledge(
        session,
        tid,
        ds="default",
        table="vw_t_entry",
        comment="c",
        embed_text="x",
        team_id=team_id,
    )
    # 可见(列表含)但不可写
    names = [
        i["table_name"]
        for i in (await client.get("/system/rag/tables", headers=_auth(viewer_token))).json()[
            "items"
        ]
    ]
    assert "vw_t_entry" in names
    r = await client.put(
        f"/system/rag/tables/{kid}",
        json={"table_comment": "x", "alias": "y"},
        headers=_auth(viewer_token),
    )
    assert r.status_code == 403


async def test_upload_team_requires_writable_membership(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """上传归属闸:非成员指向团队 404(不暴露存在性);VIEWER 403;不落库。"""
    _, out_token = await _rag_mgr_token(client, session, make_user, "up_o")
    viewer_uid, viewer_token = await _rag_mgr_token(client, session, make_user, "up_v")
    tid = await _ensure_default_tenant(session)
    team_id = await _mk_team(session, tid, "up_t", members={viewer_uid: "VIEWER"})
    r1 = await _upload(client, out_token, _md("t_up"), team_id=team_id)
    assert r1.status_code == 404  # type: ignore[attr-defined]
    r2 = await _upload(client, viewer_token, _md("t_up"), team_id=team_id)
    assert r2.status_code == 403  # type: ignore[attr-defined]
    assert (
        await session.execute(text("SELECT count(*) FROM rag.table_descriptions"))
    ).scalar_one() == 0


async def test_upload_overwrite_team_entry_requires_entry_membership(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """既有条目写闸(spec「写操作含上传」):四元冲突命中他团队条目时,
    上传无论是否携带归属,非其成员一律 404 同形——防借覆盖夺取内容/归属;
    条目原样保留。"""
    y_uid, y_token = await _rag_mgr_token(client, session, make_user, "upw_y")
    _, out_token = await _rag_mgr_token(client, session, make_user, "upw_o")
    tid = await _ensure_default_tenant(session)
    x_team = await _mk_team(session, tid, "upw_x")
    y_team = await _mk_team(session, tid, "upw_y", members={y_uid: "MEMBER"})
    await _seed_knowledge(
        session,
        tid,
        ds="default",
        table="upw_t",
        comment="旧",
        embed_text="x",
        team_id=x_team,
        # 上传覆盖按四元(schema 含在内)命中既有条目:与上传 schema 保持一致
        schema_name="tenant_default",
    )
    # 非成员携归属上传(意图转移归属):404 同形
    r1 = await _upload(client, y_token, _md("upw_t"), team_id=y_team)
    assert r1.status_code == 404  # type: ignore[attr-defined]
    # 非成员缺省归属上传(意图覆盖内容):同形 404
    r2 = await _upload(client, out_token, _md("upw_t"))
    assert r2.status_code == 404  # type: ignore[attr-defined]
    row = (
        (
            await session.execute(
                text(
                    "SELECT table_comment, team_id FROM rag.table_descriptions"
                    " WHERE table_name='upw_t'"
                )
            )
        )
        .mappings()
        .one()
    )
    assert row["table_comment"] == "旧" and str(row["team_id"]) == x_team  # 未被触碰


async def test_ownership_change_audit_and_cache_evict(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser, fake_embed: None
) -> None:
    """归属变更(公共⇄团队):落审计+精确失效检索缓存;成员关系校验 OWNER/MEMBER。"""
    owner_uid, owner_token = await _rag_mgr_token(client, session, make_user, "ow_m")
    tid = await _ensure_default_tenant(session)
    team_id = await _mk_team(session, tid, "own_t", members={owner_uid: "MEMBER"})
    kid = await _seed_knowledge(
        session, tid, ds="default", table="own_t_entry", comment="客户", embed_text="客户"
    )
    # 预热检索缓存(成员身份召回)
    await client.post(
        "/internal/rag/retrieve",
        json={"tenant_key": "default", "question": "客户", "user_id": owner_uid},
        headers=INTERNAL,
    )
    r = await client.put(
        f"/system/rag/tables/{kid}/ownership",
        json={"team_id": team_id},
        headers=_auth(owner_token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["team_id"] == team_id
    audit = (
        await session.execute(
            text("SELECT count(*) FROM public.sys_oper_log WHERE action='rag.set_ownership'")
        )
    ).scalar_one()
    assert audit == 1
    from kongming.redis_client import get_redis

    keys = [k async for k in cast("Any", get_redis()).scan_iter(match="km:ragctx:*")]
    assert keys == []  # 归属变更精确失效检索缓存
    # 转回公共
    r2 = await client.put(
        f"/system/rag/tables/{kid}/ownership", json={"team_id": None}, headers=_auth(owner_token)
    )
    assert r2.status_code == 200 and r2.json()["team_id"] is None
    row = (
        await session.execute(
            text("SELECT team_id FROM rag.table_descriptions WHERE id=CAST(:i AS uuid)"),
            {"i": kid},
        )
    ).scalar_one()
    assert row is None


# ============ 团队删除钩子公共化 ============


async def test_team_delete_publicizes_entries_with_audit_and_evict(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser, fake_embed: None
) -> None:
    """团队删除:名下条目公共化(team_id 置空)+审计+检索缓存失效;
    删除后任意用户(原非成员)召回可见。"""
    from kongming.redis_client import get_redis

    mgr_uid, mgr_token = await _rag_mgr_token(client, session, make_user, "hook_m")
    rid = await _mk_role_with_perms(session, "role_hook_team", ["system:team:manage"])
    await _bind(session, "sys_user_role", mgr_uid, rid)
    tid = await _ensure_default_tenant(session)
    team_id = await _mk_team(session, tid, "hook_t")
    # 公共条目保证非成员预热缓存非空(空结果不写缓存——既有纪律)
    await _seed_knowledge(
        session, tid, ds="default", table="hook_pub", comment="公共订单", embed_text="订单"
    )
    await _seed_knowledge(
        session,
        tid,
        ds="default",
        table="hook_t_entry",
        comment="订单",
        embed_text="订单",
        team_id=team_id,
    )
    # 预热缓存(非成员身份——公共化后应可见,旧缓存不含)
    out_uid = await make_user("hook_out")
    q = {"tenant_key": "default", "question": "订单", "user_id": out_uid}
    await client.post("/internal/rag/retrieve", json=q, headers=INTERNAL)
    assert [k async for k in cast("Any", get_redis()).scan_iter(match="km:ragctx:*")]
    r = await client.delete(f"/system/teams/{team_id}", headers=_auth(mgr_token))
    assert r.status_code == 200, r.text
    row = (
        await session.execute(
            text("SELECT team_id FROM rag.table_descriptions WHERE table_name='hook_t_entry'")
        )
    ).scalar_one()
    assert row is None  # 公共化
    audit = (
        await session.execute(
            text("SELECT count(*) FROM public.sys_oper_log WHERE action='team.knowledge_publicize'")
        )
    ).scalar_one()
    assert audit == 1
    keys = [k async for k in cast("Any", get_redis()).scan_iter(match="km:ragctx:*")]
    assert keys == []  # 受影响范围缓存失效
    body = (await client.post("/internal/rag/retrieve", json=q, headers=INTERNAL)).json()
    assert "hook_t_entry" in body["schema_hint"]  # 公共化即时生效(原非成员可见)


async def test_team_delete_hook_failure_not_blocking(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """钩子失败不阻塞团队删除:告警可观测,团队照常删除,条目经 FK
    ON DELETE SET NULL 级联兜底公共化(不泄露不丢资产)。

    告警经替换模块 logger 捕获(alembic fileConfig 会 disable 既有 logger,
    caplog 在本套件不可靠),断言确定性不受日志基建影响。"""
    import kongming.team.service as team_service
    from kongming.rag.repository import RagEntryRepository

    warnings: list[str] = []

    class _CaptureLog:
        def warning(self, msg: str, *args: object, **kw: object) -> None:
            warnings.append(msg % args if args else msg)

    monkeypatch.setattr(team_service, "log", _CaptureLog())

    async def boom(self: RagEntryRepository, *, team_id: str) -> list[tuple[str, str]]:
        raise RuntimeError("公共化钩子故障")

    monkeypatch.setattr(RagEntryRepository, "publicize_team_entries", boom)
    mgr_uid, mgr_token = await _rag_mgr_token(client, session, make_user, "hookf_m")
    rid = await _mk_role_with_perms(session, "role_hookf_team", ["system:team:manage"])
    await _bind(session, "sys_user_role", mgr_uid, rid)
    tid = await _ensure_default_tenant(session)
    team_id = await _mk_team(session, tid, "hookf_t")
    await _seed_knowledge(
        session,
        tid,
        ds="default",
        table="hookf_entry",
        comment="c",
        embed_text="x",
        team_id=team_id,
    )
    r = await client.delete(f"/system/teams/{team_id}", headers=_auth(mgr_token))
    assert r.status_code == 200, r.text  # 删除未被阻塞
    assert any("公共化钩子" in w for w in warnings)  # 告警可观测
    team_gone = (
        await session.execute(
            text("SELECT count(*) FROM public.sys_team WHERE id=CAST(:i AS uuid)"),
            {"i": team_id},
        )
    ).scalar_one()
    assert team_gone == 0
    row = (
        await session.execute(
            text("SELECT team_id FROM rag.table_descriptions WHERE table_name='hookf_entry'")
        )
    ).scalar_one()
    assert row is None  # FK 级联兜底公共化(可见性按团队不存在=不可见→级联后公共)


# ============ fetch-metadata / reindex 归属语义(自动来源一律公共) ============


async def test_fetch_metadata_resets_team_auto_entry_to_public(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """扫描更新团队归属的自动条目:同口径回公共(机器产物不做归属推断)。"""
    from kongming.rag.agent_callback import AgentRagClient as _C

    async def fake_describe(self: _C, block: object, schema_name: str) -> tuple[bool, object]:
        return True, block

    monkey = pytest.MonkeyPatch()
    monkey.setattr(_C, "describe", fake_describe)
    try:
        token = (await _rag_mgr_token(client, session, make_user, "fm_m"))[1]
        tid = await _ensure_default_tenant(session)
        team_id = await _mk_team(session, tid, "fm_t")
        await _seed_knowledge(
            session,
            tid,
            ds="default",
            table="goods",
            comment="旧",
            embed_text="goods",
            team_id=team_id,
            source="AI_GENERATED",
        )
        r = await client.post(
            "/system/rag/fetch-metadata",
            json={"datasource_key": "default"},
            headers=_auth(token),
        )
        assert r.status_code == 200, r.text
        row = (
            (
                await session.execute(
                    text(
                        "SELECT team_id, source_type FROM rag.table_descriptions"
                        " WHERE table_name='goods'"
                    )
                )
            )
            .mappings()
            .one()
        )
        assert row["team_id"] is None  # 团队条目被扫描对账同口径处理回公共
        assert row["source_type"] == "AI_GENERATED"
    finally:
        monkey.undo()


async def test_reindex_rebuilds_team_entries_public(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """reindex 清空重建:团队条目一并清掉,重建产物全公共(明示语义)。"""
    from kongming.rag.agent_callback import AgentRagClient as _C

    async def fake_describe(self: _C, block: object, schema_name: str) -> tuple[bool, object]:
        return True, block

    monkey = pytest.MonkeyPatch()
    monkey.setattr(_C, "describe", fake_describe)
    try:
        token = (await _rag_mgr_token(client, session, make_user, "rx_m"))[1]
        tid = await _ensure_default_tenant(session)
        team_id = await _mk_team(session, tid, "rx_t")
        await _seed_knowledge(
            session,
            tid,
            ds="default",
            table="goods",
            comment="旧",
            embed_text="goods",
            team_id=team_id,
        )
        r = await client.post(
            "/system/rag/reindex", json={"datasource_key": "default"}, headers=_auth(token)
        )
        assert r.status_code == 200, r.text
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT team_id FROM rag.table_descriptions"
                        " WHERE tenant_id=CAST(:t AS uuid)"
                    ),
                    {"t": tid},
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) >= 1 and all(v is None for v in rows)  # 重建全公共
    finally:
        monkey.undo()


async def test_reconcile_deletes_stale_team_auto_entry_same_rule(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """对账(登记=现实)对团队条目同口径:扫描集外自动来源条目删除,与归属无关。"""
    from kongming.rag.agent_callback import AgentRagClient as _C

    async def fake_describe(self: _C, block: object, schema_name: str) -> tuple[bool, object]:
        return True, block

    monkey = pytest.MonkeyPatch()
    monkey.setattr(_C, "describe", fake_describe)
    try:
        token = (await _rag_mgr_token(client, session, make_user, "rc_m"))[1]
        tid = await _ensure_default_tenant(session)
        team_id = await _mk_team(session, tid, "rc_t")
        stale = await _seed_knowledge(
            session,
            tid,
            ds="default",
            table="stale_team_t",
            comment="c",
            embed_text="x",
            team_id=team_id,
            source="AI_GENERATED",
            schema_name="tenant_sakila",
        )
        r = await client.post(
            "/system/rag/fetch-metadata",
            json={"datasource_key": "default"},
            headers=_auth(token),
        )
        assert r.status_code == 200, r.text
        assert r.json()["removed"] >= 1
        gone = (
            await session.execute(
                text("SELECT count(*) FROM rag.table_descriptions WHERE id=CAST(:i AS uuid)"),
                {"i": stale},
            )
        ).scalar_one()
        assert gone == 0  # 团队条目对账同口径(不因归属豁免)
    finally:
        monkey.undo()


# ============ query_history 历史路样本可见性 ============


async def test_migration_0052_roundtrip_and_legacy_public() -> None:
    """down 到 0051 → 存量形态插行 → 升 head:visible_teams 列在且存量行为
    '{}' (=公共,来源不可追溯按既有公共语义);再降/升往返随列出现消失。"""
    async with migrated_tmp_db() as (session, cfg):
        await _run_alembic(session, cfg, "0051")
        tid = str(
            (
                await session.execute(
                    text(
                        "INSERT INTO public.sys_tenant (id, tenant_key, tenant_name)"
                        " VALUES (gen_random_uuid(), 'mig102h', '迁移租户') RETURNING id"
                    )
                )
            ).scalar_one()
        )
        await session.execute(
            text(
                "INSERT INTO rag.query_history"
                " (id, tenant_id, datasource_key, question, sql_text, status, source_type)"
                " VALUES (gen_random_uuid(), CAST(:t AS uuid), 'default', '旧问句',"
                "  'SELECT 1', 'SUCCESS', 'EXECUTION')"
            ),
            {"t": tid},
        )
        await session.commit()
        await _run_alembic(session, cfg, "head")
        col = (
            await session.execute(
                text(
                    "SELECT count(*) FROM information_schema.columns"
                    " WHERE table_schema='rag' AND table_name='query_history'"
                    " AND column_name='visible_teams'"
                )
            )
        ).scalar_one()
        assert col == 1
        legacy = (
            await session.execute(
                text("SELECT visible_teams FROM rag.query_history WHERE question='旧问句'")
            )
        ).scalar_one()
        assert legacy is not None and len(legacy) == 0  # '{}'=公共
        await _run_alembic(session, cfg, "0051")
        col = (
            await session.execute(
                text(
                    "SELECT count(*) FROM information_schema.columns"
                    " WHERE table_schema='rag' AND table_name='query_history'"
                    " AND column_name='visible_teams'"
                )
            )
        ).scalar_one()
        assert col == 0
        await _run_alembic(session, cfg, "head")


class _FixedTeams:
    """成员团队集合只读桩(可见集合单点判定直测)。"""

    def __init__(self, teams: list[str]) -> None:
        self._teams = teams

    async def team_ids_of_user(self, user_id: str) -> list[str]:
        return self._teams


class _FixedEmbedder:
    """embedding 端口桩(同文本同向量,保证召回向量可比)。"""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 2560 for _ in texts]

    async def describe(self, block: TableBlock, schema_name: str) -> tuple[bool, TableBlock]:
        raise AssertionError("回写路径不应触 describer")


async def test_history_sample_visibility_roundtrip(session: AsyncSession) -> None:
    """历史路旁路治理:成员回写的样本带其团队集合,非成员召回
    不可见、成员与公共可见;无身份回写=公共样本全员可见;幂等覆盖以最新
    一次来源刷新集合。"""
    import uuid as uuid_mod
    from typing import cast

    from kongming.rag.cache import ContextCache
    from kongming.rag.repository import RagRepository
    from kongming.rag.service import RagService

    tid = await _ensure_default_tenant(session)
    team_y = await _mk_team(session, tid, "hist_y")
    member_uid = str(uuid_mod.uuid4())
    await session.execute(
        text(
            "INSERT INTO public.sys_team_member (team_id, user_id, role)"
            " VALUES (CAST(:t AS uuid), CAST(:u AS uuid), 'MEMBER')"
        ),
        {"t": team_y, "u": member_uid},
    )
    await session.commit()
    repo = RagRepository(session, ts_config="simple")

    # 成员回写:样本来源集合=其团队
    null_cache = cast("ContextCache", _NullCache())
    svc_member = RagService(
        repo,
        embedder=_FixedEmbedder(),
        cache=null_cache,
        member_teams=_FixedTeams([team_y]),
    )
    await svc_member.writeback(
        tenant_id=tid,
        datasource_key="default",
        question="团队口径问句",
        sql_text="SELECT 1",
        caller_user_id=member_uid,
    )
    # 无身份回写:公共样本
    svc_anon = RagService(repo, embedder=_FixedEmbedder(), cache=null_cache)
    await svc_anon.writeback(
        tenant_id=tid,
        datasource_key="default",
        question="公共问句",
        sql_text="SELECT 2",
    )
    row = (
        await session.execute(
            text("SELECT visible_teams FROM rag.query_history WHERE question='团队口径问句'")
        )
    ).scalar_one()
    assert list(row) == [team_y]
    row_pub = (
        await session.execute(
            text("SELECT visible_teams FROM rag.query_history WHERE question='公共问句'")
        )
    ).scalar_one()
    assert row_pub is not None and len(row_pub) == 0

    async def recall(teams: list[str]) -> set[str]:
        hits = await repo.recall.recall_history(
            tenant_id=tid,
            datasource_key="default",
            vector=[0.1] * 2560,
            visible_team_ids=teams,
        )
        return {h.question for h in hits}

    assert await recall([]) == {"公共问句"}  # 无身份:只见公共
    assert await recall([team_y]) == {"团队口径问句", "公共问句"}  # 成员:两者
    # 幂等覆盖刷新:同题以公共身份重写 → 集合清空(最新一次来源为准)
    await svc_anon.writeback(
        tenant_id=tid,
        datasource_key="default",
        question="团队口径问句",
        sql_text="SELECT 1 -- 重写",
        caller_user_id=None,
    )
    refreshed = (
        await session.execute(
            text("SELECT visible_teams FROM rag.query_history WHERE question='团队口径问句'")
        )
    ).scalar_one()
    assert refreshed is not None and len(refreshed) == 0


class _NullCache:
    """测试桩:回写路径不触缓存(writeback 不消费 ContextCache)。"""

    def __getattr__(self, name: str) -> object:
        raise AssertionError("writeback 路径不应触缓存")
