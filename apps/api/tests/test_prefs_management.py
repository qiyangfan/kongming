"""偏好管理集成测试，覆盖省略、显式删除、并发合并、旧提取冲突和显式聊天槽位更新。"""

# pyright: reportPrivateUsage=false

import asyncio
from typing import cast

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.chat.agent_client import AgentReply
from kongming.chat.router import get_agent_client
from kongming.config import get_settings
from kongming.db import get_sessionmaker
from kongming.main import app
from tests.conftest import MakeUser
from tests.test_chat_query import _auth, _bind, _default_tenant_id, _login


class ExtractAgent:
    """模拟成功问答并返回指定槽位候选的 Agent。"""

    def __init__(self, candidates: list[dict[str, object]]) -> None:
        self.reply = AgentReply(success=True, verdict="PASS", summary="共 4 种商品")
        self.candidates = candidates

    async def query(self, **kw: object) -> AgentReply:
        return self.reply

    async def rewrite_title(self, *, question: str) -> str | None:
        return None

    async def extract_memory(self, **kw: object) -> list[dict[str, object]]:
        return self.candidates


async def _user(
    client: AsyncClient, s: AsyncSession, make_user: MakeUser, username: str
) -> tuple[str, str]:
    await seed_all(s, get_settings())
    uid = await make_user(username)
    rid = (
        await s.execute(text("SELECT id FROM public.sys_role WHERE role_key='DEVELOPER'"))
    ).scalar_one()
    await _bind(s, "sys_user_role", uid, str(rid))
    await _bind(s, "sys_user_tenant", uid, await _default_tenant_id(s))
    return uid, await _login(client, username)


async def _row(s: AsyncSession, uid: str) -> tuple[dict[str, object], int] | None:
    """直查 (prefs, manual_revision);行不存在 → None。列均 VARCHAR,子查询结果对齐。"""
    r = (
        await s.execute(
            text(
                "SELECT prefs, manual_revision FROM public.user_prefs"
                " WHERE user_id=:u"
                " AND tenant_id=(SELECT id::varchar FROM public.sys_tenant"
                "  WHERE tenant_key='default')"
            ),
            {"u": uid},
        )
    ).one_or_none()
    if r is None:
        return None
    return cast("dict[str, object]", r[0]), int(cast("int", r[1]))


def _svc():
    """服务层直测组装(embedding 失败降级无关;operlog 落同会话)。"""
    from kongming.auth.repository import SqlOperLogRepo
    from kongming.memory.repository import SqlMemoryRepo
    from kongming.memory.service import MemoryService

    class _FailEmbed:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("no agent in test")

    async def _mk(s: AsyncSession) -> MemoryService:
        return MemoryService(SqlMemoryRepo(s), _FailEmbed(), SqlOperLogRepo(s))

    return _mk


# ============ PUT 语义 ============


async def test_put_explicit_null_deletes_slot(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    uid, token = await _user(client, session, make_user, "pref_del")
    r = await client.put(
        "/memory/prefs", json={"language": "zh", "timezone": "UTC"}, headers=_auth(token)
    )
    assert r.status_code == 200
    r2 = await client.put("/memory/prefs", json={"language": None}, headers=_auth(token))
    assert r2.status_code == 200
    prefs = r2.json()["prefs"]
    assert "language" not in prefs
    assert prefs["timezone"] == "UTC"
    row = await _row(session, uid)
    assert row is not None and "language" not in row[0]


async def test_put_omitted_and_empty_not_delete(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """省略字段保留;空字符串/空列表不解释为删除(既有归一化语义)。"""
    _uid, token = await _user(client, session, make_user, "pref_keep")
    await client.put(
        "/memory/prefs",
        json={"language": "zh", "metric_aliases": ["GMV=成交额"]},
        headers=_auth(token),
    )
    r = await client.put(
        "/memory/prefs",
        json={"language": "", "metric_aliases": []},
        headers=_auth(token),
    )
    assert r.status_code == 200
    prefs = r.json()["prefs"]
    assert prefs["language"] == "zh"  # 空串≠删除
    assert prefs["metric_aliases"] == ["GMV=成交额"]  # 空列表≠删除


async def test_put_empty_object_true_noop(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """空补丁:不建行、不增版本、不伪造审计、不取消在途提取。"""
    uid, token = await _user(client, session, make_user, "pref_noop")
    r = await client.put("/memory/prefs", json={}, headers=_auth(token))
    assert r.status_code == 200 and r.json()["prefs"] == {}
    assert await _row(session, uid) is None  # 不创建行
    n = (
        await session.execute(
            text("SELECT count(*) FROM public.sys_oper_log WHERE action LIKE 'memory.prefs%'")
        )
    ).scalar_one()
    assert n == 0  # 不伪造修改审计

    # 在途提取不被取消:先有真人工修改(rev=1),空补丁后 rev 仍 1,旧快照仍可落
    await client.put("/memory/prefs", json={"language": "zh"}, headers=_auth(token))
    await client.put("/memory/prefs", json={"language": ""}, headers=_auth(token))
    row = await _row(session, uid)
    assert row is not None and int(row[1]) == 1  # 空补丁不增版本
    mk = _svc()
    async with get_sessionmaker()() as s2:
        svc = await mk(s2)
        ok = await svc.apply_implicit_patch(
            user_id=uid,
            tenant_id=await _default_tenant_id(session),
            raw={"display_style": "结论先行"},
            base_revision=1,
        )
        assert ok is True  # 未被空补丁失效


async def test_put_null_on_absent_slot_still_bumps_revision(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """显式 null 属有效人工指令:目标槽位不存在也推进版本。"""
    uid, token = await _user(client, session, make_user, "pref_absent")
    r = await client.put("/memory/prefs", json={"language": None}, headers=_auth(token))
    assert r.status_code == 200
    row = await _row(session, uid)
    assert row is not None and int(row[1]) == 1


# ============ DELETE 清空 ============


async def test_delete_clears_all_keeps_row_and_bumps(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    uid, token = await _user(client, session, make_user, "pref_clear")
    await client.put(
        "/memory/prefs",
        json={"language": "zh", "timezone": "UTC"},
        headers=_auth(token),
    )
    r = await client.delete("/memory/prefs", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["prefs"] == {}
    row = await _row(session, uid)
    assert row is not None  # 保留空行(旧任务不得因重建行回版本 0 复活)
    assert row[0] == {} and int(row[1]) == 2
    # 清空留审计
    n = (
        await session.execute(
            text("SELECT count(*) FROM public.sys_oper_log WHERE action='memory.prefs.clear'")
        )
    ).scalar_one()
    assert n == 1


async def test_clear_twice_still_bumps(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """已空再清空仍递增版本(防旧任务按版本 0 复活)。"""
    uid, token = await _user(client, session, make_user, "pref_clear2")
    await client.delete("/memory/prefs", headers=_auth(token))
    await client.delete("/memory/prefs", headers=_auth(token))
    row = await _row(session, uid)
    assert row is not None and int(row[1]) == 2


async def test_clear_keeps_notes_and_team_memory(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """清空仅影响偏好:个人笔记/记忆条目保持不变。"""
    uid, token = await _user(client, session, make_user, "pref_note")
    await session.execute(
        text(
            "INSERT INTO public.user_memory"
            " (id, user_id, tenant_id, content, confidence, source)"
            " VALUES (gen_random_uuid(), CAST(:u AS uuid),"
            "  (SELECT id FROM public.sys_tenant WHERE tenant_key='default'),"
            "  '我的笔记', 1.0, 'EXPLICIT')"
        ),
        {"u": uid},
    )
    await session.commit()
    await client.put("/memory/prefs", json={"language": "zh"}, headers=_auth(token))
    r = await client.delete("/memory/prefs", headers=_auth(token))
    assert r.status_code == 200
    n = (await session.execute(text("SELECT count(*) FROM public.user_memory"))).scalar_one()
    assert n == 1  # 笔记不受影响


# ============ 并发与复活防护 ============


async def test_concurrent_manual_merge_keeps_both_slots(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """并发人工修改不同字段均保留(短事务锁行+锁内基于最新合并)。"""
    uid, token = await _user(client, session, make_user, "pref_conc")
    await client.put("/memory/prefs", json={"language": "zh"}, headers=_auth(token))

    tid = await _default_tenant_id(session)

    async def _merge(slot: str, value: str) -> None:
        async with get_sessionmaker()() as s2:
            from kongming.memory.repository import SqlMemoryRepo

            await SqlMemoryRepo(s2).merge_prefs_manual(
                user_id=uid, tenant_id=tid, values={slot: value}, deletes=[]
            )
            await s2.commit()

    await asyncio.gather(_merge("timezone", "UTC"), _merge("chart_pref", "bar"))
    row = await _row(session, uid)
    assert row is not None
    prefs = row[0]
    assert prefs["language"] == "zh"
    assert prefs["timezone"] == "UTC"
    assert prefs["chart_pref"] == "bar"  # 两侧并发合并均保留
    assert int(row[1]) == 3  # 两次并发各 +1


async def test_manual_edit_abandons_stale_extraction(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """问答准备后人工修改 → 旧问答的自动补丁放弃,不覆盖人工值。"""
    uid, token = await _user(client, session, make_user, "pref_stale")
    tid = await _default_tenant_id(session)
    mk = _svc()
    # 问答准备:捕获版本 0
    async with get_sessionmaker()() as s2:
        svc = await mk(s2)
        rev = await svc.current_revision(user_id=uid, tenant_id=tid)
        assert rev == 0
    # 用户人工修改语言(版本→1)
    await client.put("/memory/prefs", json={"language": "zh"}, headers=_auth(token))
    # 旧问答的自动提取迟到达
    async with get_sessionmaker()() as s3:
        svc = await mk(s3)
        ok = await svc.apply_implicit_patch(
            user_id=uid, tenant_id=tid, raw={"language": "en"}, base_revision=0
        )
        assert ok is False  # 放弃
    row = await _row(session, uid)
    assert row is not None and row[0]["language"] == "zh"  # 人工值未被覆盖


async def test_clear_then_stale_extraction_not_resurrect(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """清空偏好后，已启动的旧问答任务不得恢复这些偏好。"""
    uid, token = await _user(client, session, make_user, "pref_resur")
    tid = await _default_tenant_id(session)
    mk = _svc()
    async with get_sessionmaker()() as s2:
        svc = await mk(s2)
        rev = await svc.current_revision(user_id=uid, tenant_id=tid)
        assert rev == 0  # 行不存在=已知版本 0
    await client.delete("/memory/prefs", headers=_auth(token))  # 清空(版本→1)
    async with get_sessionmaker()() as s3:
        svc = await mk(s3)
        ok = await svc.apply_implicit_patch(
            user_id=uid, tenant_id=tid, raw={"language": "zh"}, base_revision=0
        )
        assert ok is False
    row = await _row(session, uid)
    assert row is not None and row[0] == {}  # 不复活


async def test_concurrent_auto_patches_fill_missing(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """无人工修改时,两个自动补丁只补缺失槽位,串行合并不互相吞。"""
    uid, _token = await _user(client, session, make_user, "pref_auto")
    tid = await _default_tenant_id(session)
    mk = _svc()
    async with get_sessionmaker()() as s2:
        svc = await mk(s2)
        rev = await svc.current_revision(user_id=uid, tenant_id=tid)
        ok1 = await svc.apply_implicit_patch(
            user_id=uid, tenant_id=tid, raw={"language": "zh"}, base_revision=rev or 0
        )
        assert ok1 is True
        # 第二个自动补丁(同基准版本,模拟并发任务先后落库)
        ok2 = await svc.apply_implicit_patch(
            user_id=uid, tenant_id=tid, raw={"display_style": "结论先行"}, base_revision=rev or 0
        )
        assert ok2 is True
    row = await _row(session, uid)
    assert row is not None
    assert row[0]["language"] == "zh" and row[0]["display_style"] == "结论先行"
    assert int(row[1]) == 0  # 自动写入不递增人工版本


async def test_auto_patch_only_fills_missing_never_overwrites(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """自动提取只补缺失槽位,不覆盖已有值。"""
    uid, token = await _user(client, session, make_user, "pref_fill")
    tid = await _default_tenant_id(session)
    await client.put("/memory/prefs", json={"display_style": "全表"}, headers=_auth(token))
    mk = _svc()
    async with get_sessionmaker()() as s2:
        svc = await mk(s2)
        ok = await svc.apply_implicit_patch(
            user_id=uid,
            tenant_id=tid,
            raw={"display_style": "结论先行", "language": "zh"},
            base_revision=1,
        )
        assert ok is True
    row = await _row(session, uid)
    assert row is not None
    assert row[0]["display_style"] == "全表"  # 已有值不被覆盖
    assert row[0]["language"] == "zh"  # 缺失槽位被补


# ============ 租户隔离与版本读取失败 ============


async def test_prefs_tenant_isolation(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """偏好按 (user, tenant) 隔离:切租户不见彼此;清空只影响当前租户。"""
    uid, token = await _user(client, session, make_user, "pref_tenant")
    await client.put("/memory/prefs", json={"language": "zh"}, headers=_auth(token))
    t2 = (
        await session.execute(
            text(
                "INSERT INTO public.sys_tenant (id, tenant_key, tenant_name)"
                " VALUES (gen_random_uuid(), 'pref-t2', 'pref-t2') RETURNING id"
            )
        )
    ).scalar_one()
    await _bind(session, "sys_user_tenant", uid, str(t2))
    h2 = {**_auth(token), "X-Tenant-Id": "pref-t2"}
    r = await client.get("/memory/prefs", headers=h2)
    assert r.status_code == 200 and r.json()["prefs"] == {}  # 不见 default 的偏好
    r2 = await client.put("/memory/prefs", json={"language": "en"}, headers=h2)
    assert r2.json()["prefs"]["language"] == "en"
    await client.delete("/memory/prefs", headers=h2)  # 清空只影响第二租户
    r3 = await client.get("/memory/prefs", headers=_auth(token))
    assert r3.json()["prefs"]["language"] == "zh"  # default 租户不受影响


async def test_version_read_failure_skips_auto_write(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """版本快照缺失(None=读取失败语义)→ 自动写入整体跳过,不当作 0。"""
    uid, _token = await _user(client, session, make_user, "pref_revfail")
    tid = await _default_tenant_id(session)
    mk = _svc()
    async with get_sessionmaker()() as s2:
        svc = await mk(s2)
        ok = await svc.apply_implicit_patch(
            user_id=uid, tenant_id=tid, raw={"language": "zh"}, base_revision=None
        )
        assert ok is False
    assert await _row(session, uid) is None  # 不落库不建行


# ============ 显式聊天与偏好更新 ============


async def test_explicit_chat_slot_write_is_manual(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """聊天显式写槽位=人工语义:递增人工版本(而非隐式零版本)。"""
    uid, token = await _user(client, session, make_user, "pref_chat")
    r = await client.post(
        "/chat/query",
        json={"question": "请记住:默认用 sakila 数据源"},
        headers=_auth(token),
    )
    assert r.json()["success"] is True
    row = await _row(session, uid)
    assert row is not None
    assert row[0]["preferred_datasource"] == "sakila"
    assert int(row[1]) == 1  # 人工语义:版本推进
    n = (
        await session.execute(
            text("SELECT count(*) FROM public.sys_oper_log WHERE action='memory.prefs.update'")
        )
    ).scalar_one()
    assert n == 1


async def test_sync_chat_carries_revision_snapshot(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """同步问答后台任务携带版本快照:提取候选按准备期版本落库。"""
    uid, token = await _user(client, session, make_user, "pref_snap")
    agent = ExtractAgent([{"display_style": "结论先行"}])
    app.dependency_overrides[get_agent_client] = lambda: agent
    try:
        r = await client.post("/chat/query", json={"question": "有多少商品"}, headers=_auth(token))
        assert r.json()["success"] is True
    finally:
        app.dependency_overrides.pop(get_agent_client, None)
    row = await _row(session, uid)
    assert row is not None and row[0]["display_style"] == "结论先行"
    assert int(row[1]) == 0  # 自动写入不增人工版本
