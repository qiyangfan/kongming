# pyright: reportPrivateUsage=false
"""覆盖反馈样本的提交、审核晋升、停用和失败回滚。"""

import uuid
from typing import Any, cast

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import MakeUser, migrated_tmp_db
from tests.test_chat_query import _auth, _bind, _default_tenant_id, _login
from tests.test_feedback_admin import _mk_feedback_msg
from tests.test_rag_admin import _admin_token

pytestmark = pytest.mark.usefixtures("fake_agent")


@pytest.fixture
def fake_agent(monkeypatch: pytest.MonkeyPatch):
    """构造返回可用向量服务的 Agent 替身，供纠错样本晋升测试使用。"""
    from kongming.chat.dependencies import get_agent_client
    from kongming.main import app
    from kongming.rag.agent_callback import AgentRagClient
    from tests.test_chat_stream import StreamFakeAgent
    from tests.test_rag_recall import _fake_vector

    async def _embed(self: object, texts: list[str]) -> list[list[float]]:
        return [_fake_vector(t) for t in texts]

    monkeypatch.setattr(AgentRagClient, "embed", _embed)

    holder: dict[str, StreamFakeAgent] = {}

    def _factory() -> StreamFakeAgent:
        return holder["agent"]

    app.dependency_overrides[get_agent_client] = _factory
    yield holder
    app.dependency_overrides.pop(get_agent_client, None)


async def _uid_of(s: AsyncSession) -> str:
    return str(
        (
            await s.execute(text("SELECT id FROM public.sys_user WHERE username='admin'"))
        ).scalar_one()
    )


async def _tid(s: AsyncSession) -> str:
    return str(
        (
            await s.execute(text("SELECT id FROM public.sys_tenant WHERE tenant_key='default'"))
        ).scalar_one()
    )


async def _count_history(s: AsyncSession, like: str) -> int:
    return int(
        (
            await s.execute(
                text("SELECT count(*) FROM rag.query_history WHERE sql_text LIKE :p"),
                {"p": like},
            )
        ).scalar_one()
    )


async def _adopt(
    client: AsyncClient, token: str, mid: str, status: str = "ADOPTED"
) -> httpx.Response:
    return await client.put(
        f"/feedback/{mid}/status",
        json={"status": status},
        headers=_auth(token),
    )


# ── 提交不进召回 ─────────────────────────────────────


async def test_submit_pending_no_history(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """correctedSql 提交后 PENDING、反馈与审计正常,query_history
    零写入(样本准入后移——提交路径不经召回)。"""
    from kongming.auth.seed import seed_all
    from kongming.config import get_settings

    await seed_all(session, get_settings())
    uid, token = await _user_token(client, session, make_user, "p1u")
    tid = await _tid(session)
    mid = await _mk_feedback_msg(
        session, user_id=uid, tenant_id=tid, corrected_sql="SELECT count(*) FROM goods"
    )
    r = await client.post(
        f"/chat/messages/{mid}/feedback",
        json={"rating": "DOWN", "corrected_sql": "SELECT count(*) FROM goods"},
        headers=_auth(token),
    )
    assert r.status_code == 200
    row = (
        (
            await session.execute(
                text("SELECT feedback_status FROM public.chat_message WHERE id=:i"),
                {"i": mid},
            )
        )
        .mappings()
        .one()
    )
    assert row["feedback_status"] == "PENDING"
    assert await _count_history(session, "%count(*)%") == 0


async def _user_token(
    client: AsyncClient, s: AsyncSession, make_user: MakeUser, username: str
) -> tuple[str, str]:
    uid = await make_user(username)
    from tests.test_chat_query import _mk_role_with_perms

    rid = await _mk_role_with_perms(s, f"P-{username}", ["chat:execute", "chat:feedback:submit"])
    await _bind(s, "sys_user_role", uid, rid)
    await _bind(s, "sys_user_tenant", uid, await _default_tenant_id(s))
    return uid, _login(uid)


# ── ADOPTED 晋升与幂等 ───────────────────────────────


async def test_adopted_promotes_sample(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """ADOPTED + 非空 correctedSql 幂等晋升(FEEDBACK,可召回)。"""
    token = await _admin_token(client, session)
    uid = await _uid_of(session)
    tid = await _tid(session)
    mid = await _mk_feedback_msg(
        session,
        user_id=uid,
        tenant_id=tid,
        corrected_sql="SELECT count(*) FROM tenant_default.goods WHERE qty > 5",
        question="库存大于 5 的商品有几种",
    )
    r = await _adopt(client, token, mid)
    assert r.status_code == 200
    rows = (
        (
            await session.execute(
                text(
                    "SELECT source_type, recall_enabled, feedback_message_id, question,"
                    " sql_text FROM rag.query_history WHERE sql_text LIKE '%qty%'"
                )
            )
        )
        .mappings()
        .all()
    )
    assert len(rows) == 1
    assert rows[0]["source_type"] == "FEEDBACK"
    assert rows[0]["recall_enabled"] is True
    assert str(rows[0]["feedback_message_id"]) == mid
    assert rows[0]["question"] == "库存大于 5 的商品有几种"
    # 幂等:重复 ADOPTED 仍一条(同反馈消息覆盖,不增行)
    r2 = await _adopt(client, token, mid)
    assert r2.status_code == 200
    assert await _count_history(session, "%qty%") == 1


async def test_adopted_empty_sql_no_sample(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """ADOPTED + 空 correctedSql 不创建样本。"""
    token = await _admin_token(client, session)
    uid = await _uid_of(session)
    tid = await _tid(session)
    mid = await _mk_feedback_msg(session, user_id=uid, tenant_id=tid, corrected_sql=None)
    r = await _adopt(client, token, mid)
    assert r.status_code == 200
    assert (await session.execute(text("SELECT count(*) FROM rag.query_history"))).scalar_one() == 0


async def test_pending_rejected_dismissed_no_recall(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """PENDING/REJECTED/DISMISSED 均不召回(无样本产生)。"""
    token = await _admin_token(client, session)
    uid = await _uid_of(session)
    tid = await _tid(session)
    for i, status in enumerate(("PENDING", "REJECTED", "DISMISSED")):
        mid = await _mk_feedback_msg(
            session,
            user_id=uid,
            tenant_id=tid,
            corrected_sql=f"SELECT {i} FROM goods",  # noqa: S608  测试夹具文本,不入任何查询
        )
        r = await _adopt(client, token, mid, status=status)
        assert r.status_code == 200
    assert (await session.execute(text("SELECT count(*) FROM rag.query_history"))).scalar_one() == 0


async def test_adopted_then_transition_disables(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """从 ADOPTED 转出后样本停用(行保留,recall_enabled=FALSE)。"""
    token = await _admin_token(client, session)
    uid = await _uid_of(session)
    tid = await _tid(session)
    mid = await _mk_feedback_msg(
        session,
        user_id=uid,
        tenant_id=tid,
        corrected_sql="SELECT sum(amount) FROM orders",
    )
    assert (await _adopt(client, token, mid)).status_code == 200
    assert await _count_history(session, "%sum(amount)%") == 1
    r = await _adopt(client, token, mid, status="REJECTED")
    assert r.status_code == 200
    row = (
        (
            await session.execute(
                text(
                    "SELECT source_type, recall_enabled FROM rag.query_history"
                    " WHERE sql_text LIKE '%sum(amount)%'"
                )
            )
        )
        .mappings()
        .one()
    )
    assert row["source_type"] == "FEEDBACK"
    assert row["recall_enabled"] is False  # 停用不删行(来源审计)
    # 重新 ADOPTED 可再启用(幂等晋升)
    r2 = await _adopt(client, token, mid)
    assert r2.status_code == 200
    enabled = (
        (
            await session.execute(
                text(
                    "SELECT recall_enabled FROM rag.query_history"
                    " WHERE sql_text LIKE '%sum(amount)%'"
                )
            )
        )
        .mappings()
        .one()
    )
    assert enabled["recall_enabled"] is True


async def test_resubmit_after_adopted_disables(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """重复提交(现值 ADOPTED)重置 PENDING 并停用 messageId 关联的
    旧样本(同一事务)。"""
    token = await _admin_token(client, session)
    uid = await _uid_of(session)
    tid = await _tid(session)
    mid = await _mk_feedback_msg(
        session,
        user_id=uid,
        tenant_id=tid,
        corrected_sql="SELECT 42 FROM dual",
    )
    assert (await _adopt(client, token, mid)).status_code == 200
    assert await _count_history(session, "%42%") == 1
    # 归属同租户/本人的用户重新提交(直写状态列再走提交端点不合规;
    # 提交端点需消息归属该作者——此处直接以 admin 同一用户提交)
    r = await client.post(
        f"/chat/messages/{mid}/feedback",
        json={"rating": "UP", "corrected_sql": "SELECT 43 FROM dual"},
        headers=_auth(_login(client)),
    )
    assert r.status_code == 200
    row = (
        (
            await session.execute(
                text("SELECT feedback_status FROM public.chat_message WHERE id=:i"),
                {"i": mid},
            )
        )
        .mappings()
        .one()
    )
    assert row["feedback_status"] == "PENDING"
    disabled = (
        (
            await session.execute(
                text("SELECT recall_enabled FROM rag.query_history WHERE sql_text LIKE '%42%'")
            )
        )
        .mappings()
        .one()
    )
    assert disabled["recall_enabled"] is False


# ── 同问句异来源并存与召回优先 ───────────────────────


async def test_same_question_two_sources_coexist(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """同问句执行样本与反馈样本并存;幂等键分离互不覆盖。"""
    from kongming.auth.seed import seed_all
    from kongming.config import get_settings
    from kongming.rag.repository import RagRepository

    await seed_all(session, get_settings())
    tid = await _tid(session)
    # 执行样本(通道直插,同问句)
    async with session:
        repo = RagRepository(session)
        await repo.samples.insert_history(
            tenant_id=tid,
            datasource_key="default",
            question="库存大于 5 的商品有几种",
            sql_text="SELECT count(*) FROM goods WHERE qty > 5",
            embedding=None,
        )
        # 反馈样本(通道晋升,同问句不同 SQL)
        await repo.samples.promote_feedback_sample(
            tenant_id=tid,
            datasource_key="default",
            question="库存大于 5 的商品有几种",
            sql_text="SELECT count(*) FROM tenant_default.goods WHERE qty >= 5",
            feedback_message_id=str(uuid.uuid4()),
            embedding=None,
        )
        await session.commit()
    rows = (
        (
            await session.execute(
                text(
                    "SELECT sql_text, source_type FROM rag.query_history"
                    " WHERE question='库存大于 5 的商品有几种' ORDER BY source_type"
                )
            )
        )
        .mappings()
        .all()
    )
    assert len(rows) == 2
    assert [r["source_type"] for r in rows] == ["EXECUTION", "FEEDBACK"]


def _build_recall_repo(tid: str, question: str) -> object:
    """召回替身:返回同问句的执行/反馈双样本,其余数据源为空。"""
    from kongming.rag.models import HistorySample

    candidates = [
        HistorySample(question=question, sql_text="EXEC-SQL", source_type="EXECUTION"),
        HistorySample(question=question, sql_text="FEEDBACK-SQL", source_type="FEEDBACK"),
    ]

    class _Recall:
        async def recall_history(self, *_a: object, **_kw: object) -> list[HistorySample]:
            return candidates

        async def recall_vector(self, *_a: object, **_kw: object) -> list[object]:
            return []

        async def has_knowledge(self, *_a: object, **_kw: object) -> bool:
            return True  # 前置:知识在位,闸门照常评估

        async def recall_fulltext(self, *_a: object, **_kw: object) -> list[object]:
            return []

        async def fetch_entries(self, *_a: object, **_kw: object) -> list[object]:
            return []

    class _Tenant:
        async def tenant_id_by_key(self, tenant_key: str) -> str:
            return tid

        async def datasource_usable(self, *_a: object, **_kw: object) -> bool:
            return True

    class _Repo:
        def __init__(self) -> None:
            self.recall = _Recall()
            self.tenant = _Tenant()

    return _Repo()


def _build_recall_cache() -> object:
    """缓存替身:读写均为空操作(不命中缓存)。"""

    class _Cache:
        async def get(self, *_a: object, **_kw: object) -> None:
            return None

        async def set(self, *_a: object, **_kw: object) -> None:
            return None

    return _Cache()


def _build_recall_embedder() -> object:
    """向量替身:固定维度假向量,不触发真实 embedding 调用。"""

    class _Embedder:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.1] * 4 for _ in texts]

    return _Embedder()


async def test_recall_prefers_feedback_sample(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """召回对同问句候选去重——优先 ADOPTED 反馈样本,无则执行样本。"""
    from kongming.auth.seed import seed_all
    from kongming.config import get_settings
    from kongming.rag.service import RagService

    await seed_all(session, get_settings())
    tid = await _tid(session)
    question = "库存大于 5 的商品有几种"

    svc = RagService(
        cast("Any", _build_recall_repo(tid, question)),
        embedder=cast("Any", _build_recall_embedder()),
        cache=cast("Any", _build_recall_cache()),
    )
    ctx = await cast("Any", svc).recall(
        tenant_key="default", datasource_key="default", question=question
    )
    # 反馈样本优先;执行样本被同问句去重(≤ RECALL_TOP_K 条)
    assert ctx.history_sql == "FEEDBACK-SQL"


# ── 晋升失败 503 无部分提交 ──────────────────────────


async def test_promotion_failure_503_no_partial(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    """embedding 晋升失败返回 503;反馈状态、审计、query_history
    均无部分提交(ADOPTED 前状态保持)。"""
    from kongming.feedback import router as fb_router
    from kongming.rag.ports import EmbeddingError

    token = await _admin_token(client, session)
    uid = await _uid_of(session)
    tid = await _tid(session)
    mid = await _mk_feedback_msg(
        session,
        user_id=uid,
        tenant_id=tid,
        corrected_sql="SELECT 7 FROM goods",
    )

    orig_init = fb_router._FeedbackSampleGate.__init__

    def _boom_init(
        self: object,
        repo: object,
        embedder: object,
        rag: object,
        **kwargs: object,
    ) -> None:
        orig_init(self, repo, _BoomEmbedder(), rag, **kwargs)  # type: ignore[arg-type]

    class _BoomEmbedder:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            raise EmbeddingError("agent down")

    monkeypatch.setattr(fb_router._FeedbackSampleGate, "__init__", _boom_init)
    r = await client.put(
        f"/feedback/{mid}/status", json={"status": "ADOPTED"}, headers=_auth(token)
    )
    assert r.status_code == 503
    assert r.json()["detail"] == "SAMPLE_PROMOTION_UNAVAILABLE"
    # 无部分提交:状态仍 PENDING、零样本、零审计
    st = (
        (
            await session.execute(
                text("SELECT feedback_status FROM public.chat_message WHERE id=:i"),
                {"i": mid},
            )
        )
        .mappings()
        .one()
    )
    assert st["feedback_status"] == "PENDING"
    assert await _count_history(session, "%7%") == 0
    n_audit = (
        await session.execute(
            text("SELECT count(*) FROM public.sys_oper_log WHERE action='feedback.handle'")
        )
    ).scalar_one()
    assert n_audit == 0


# ── 缓存精确失效 ─────────────────────────────────────


async def test_promote_evicts_ctx_cache(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    """晋升提交后 ctx 缓存按租户+数据源精确失效。"""
    from kongming.feedback import router as fb_router

    calls: list[tuple[str, str]] = []
    orig = fb_router._FeedbackSampleGate.evict

    async def _spy(self: object, *, tenant_id: str, datasource_key: str) -> None:
        calls.append((tenant_id, datasource_key))
        await orig(self, tenant_id=tenant_id, datasource_key=datasource_key)  # type: ignore[arg-type]

    monkeypatch.setattr(fb_router._FeedbackSampleGate, "evict", _spy)
    token = await _admin_token(client, session)
    uid = await _uid_of(session)
    tid = await _tid(session)
    mid = await _mk_feedback_msg(
        session,
        user_id=uid,
        tenant_id=tid,
        corrected_sql="SELECT 99 FROM goods",
    )
    r = await _adopt(client, token, mid)
    assert r.status_code == 200
    assert calls == [(tid, "default")]


# ── 防火墙整体拒原子性(既有契约复核) ──────────────────


async def test_firewall_reject_keeps_state(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """correctedSql 被防火墙拒 → 整体回滚,既有执行样本与反馈
    状态零变动(与原子契约一致,未削弱)。"""
    token = await _admin_token(client, session)
    uid = await _uid_of(session)
    tid = await _tid(session)
    # 先造一个 ADOPTED 样本(基线)
    mid_ok = await _mk_feedback_msg(
        session,
        user_id=uid,
        tenant_id=tid,
        corrected_sql="SELECT count(*) FROM goods",
    )
    assert (await _adopt(client, token, mid_ok)).status_code == 200
    before = await _count_history(session, "%count(*)%")
    # 危险 SQL 提交 → 400 整体拒
    mid_bad = await _mk_feedback_msg(
        session,
        user_id=uid,
        tenant_id=tid,
        corrected_sql=None,
    )
    r = await client.post(
        f"/chat/messages/{mid_bad}/feedback",
        json={"rating": "DOWN", "corrected_sql": "DELETE FROM goods"},
        headers=_auth(_login(client)),
    )
    assert r.status_code == 400
    # 零新增、Baseline 不变
    assert await _count_history(session, "%count(*)%") == before
    st = (
        (
            await session.execute(
                text("SELECT feedback_status FROM public.chat_message WHERE id=:i"),
                {"i": mid_bad},
            )
        )
        .mappings()
        .one()
    )
    assert st["feedback_status"] == "PENDING"


# ── 迁移契约(约束/索引/来源恢复/downgrade) ────────────


async def test_migration_0034_schema_contract(session: AsyncSession) -> None:
    """0034 落地后 schema 契约在位(列/CHECK/部分唯一索引/
    幂等索引/召回过滤索引);存量行(测试库为迁移后写入)EXECUTION 语义正确。"""
    from kongming.auth.seed import seed_all
    from kongming.config import get_settings

    await seed_all(session, get_settings())
    tid = await _tid(session)
    from kongming.rag.repository import RagRepository

    repo = RagRepository(session)
    await repo.samples.insert_history(
        tenant_id=tid,
        datasource_key="default",
        question="q-mig",
        sql_text="SELECT 1",
        embedding=None,
    )
    await session.commit()
    row = (
        (
            await session.execute(
                text(
                    "SELECT source_type, recall_enabled, feedback_message_id"
                    " FROM rag.query_history WHERE question='q-mig'"
                )
            )
        )
        .mappings()
        .one()
    )
    assert row["source_type"] == "EXECUTION"
    assert row["recall_enabled"] is True
    assert row["feedback_message_id"] is None
    # 部分唯一索引:执行样本同问句幂等(ON CONFLICT 目标在位)
    await repo.samples.insert_history(
        tenant_id=tid,
        datasource_key="default",
        question="q-mig",
        sql_text="SELECT 2",
        embedding=None,
    )
    await session.commit()
    n = (
        await session.execute(text("SELECT count(*) FROM rag.query_history WHERE question='q-mig'"))
    ).scalar_one()
    assert n == 1  # 覆盖不增行
    # LEGACY 来源退出召回(recall_history 候选仅 EXECUTION|FEEDBACK)
    await session.execute(
        text(
            "UPDATE rag.query_history SET source_type='LEGACY', recall_enabled=FALSE"
            " WHERE question='q-mig'"
        )
    )
    await session.commit()
    hits = await repo.recall.recall_history(tenant_id=tid, datasource_key="default", vector=[0.1])
    assert all(h.question != "q-mig" for h in hits)
    # CHECK 约束在位(非法来源拒写)
    with pytest.raises(Exception):  # noqa: B017  # CHECK 违约即抛(DBAPIError)
        await session.execute(
            text(
                "INSERT INTO rag.query_history"
                " (id, tenant_id, datasource_key, question, sql_text, status,"
                "  source_type, recall_enabled)"
                " VALUES (gen_random_uuid(), :t, 'default', 'bad', 'SELECT 0',"
                "  'SUCCESS', 'UNKNOWN', TRUE)"
            ),
            {"t": tid},
        )
    await session.rollback()


async def test_migration_0034_downgrade_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    """downgrade 往返(up 后 down 再 up 契约复现)。"""
    async with migrated_tmp_db() as (session, cfg):
        from alembic import command
        from kongming.auth.seed import seed_all
        from kongming.config import get_settings

        await seed_all(session, get_settings())
        # 0064 降级拒绝带 KEYCLOAK 绑定;这里先清理测试预建映射,不丢业务数据。
        await session.execute(text("DELETE FROM public.user_identities WHERE provider='KEYCLOAK'"))
        await session.commit()
        import asyncio

        # env.py 内部 asyncio.run:跑事件循环内直调会冲突,经线程隔离
        await asyncio.to_thread(command.downgrade, cfg, "0033")
        has_col = (
            await session.execute(
                text(
                    "SELECT count(*) FROM information_schema.columns"
                    " WHERE table_schema='rag' AND table_name='query_history'"
                    " AND column_name='source_type'"
                )
            )
        ).scalar_one()
        assert has_col == 0
        import asyncio

        # 回到当前 head,恢复后续测试需要的完整 schema。
        await asyncio.to_thread(command.upgrade, cfg, "head")
        has_col2 = (
            await session.execute(
                text(
                    "SELECT count(*) FROM information_schema.columns"
                    " WHERE table_schema='rag' AND table_name='query_history'"
                    " AND column_name='source_type'"
                )
            )
        ).scalar_one()
        assert has_col2 == 1
