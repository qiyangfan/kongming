"""rag 内部召回端点测试(009):三路召回/双过滤隔离/缓存/降级/chat 回写闭环。"""
# pyright: reportPrivateUsage=false

import hashlib
import math
from typing import Any, cast

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.rag.agent_callback import AgentRagClient
from tests.conftest import MakeUser

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


async def _seed_knowledge(
    s: AsyncSession,
    tenant_id: str,
    *,
    ds: str,
    table: str,
    comment: str,
    embed_text: str,
    schema_name: str = "tenant_default",
    alias: str = "",
) -> None:
    vec = "[" + ",".join(repr(v) for v in _fake_vector(embed_text)) + "]"
    await s.execute(
        text(
            "INSERT INTO rag.table_descriptions"
            " (id, tenant_id, datasource_key, schema_name, table_name,"
            "  table_comment, columns_comment, sample_queries, source_type,"
            "  embedding, tsv, alias)"
            " VALUES (gen_random_uuid(), :t, :d, :s, :n, :c, '{}', '[]',"
            "  'MANUAL_UPLOAD', CAST(:vec AS vector),"
            "  to_tsvector('simple', :n_t || ' ' || :a_t || ' ' || :c_t),"
            "  NULLIF(:a, ''))"
            " ON CONFLICT (tenant_id, datasource_key, schema_name, table_name)"
            " DO UPDATE SET embedding = EXCLUDED.embedding, tsv = EXCLUDED.tsv,"
            "  alias = EXCLUDED.alias"
        ),
        {
            "t": tenant_id,
            "d": ds,
            "s": schema_name,
            "n": table,
            "c": comment,
            "vec": vec,
            "n_t": table,
            "c_t": comment or " ",
            "a_t": alias or " ",
            "a": alias,
        },
    )
    await s.commit()


async def _seed_history(
    s: AsyncSession, tenant_id: str, *, ds: str, question: str, sql: str
) -> None:
    vec = "[" + ",".join(repr(v) for v in _fake_vector(question)) + "]"
    await s.execute(
        text(
            "INSERT INTO rag.query_history"
            " (id, tenant_id, datasource_key, question, sql_text, status, embedding)"
            " VALUES (gen_random_uuid(), :t, :d, :q, :sql, 'SUCCESS',"
            "  CAST(:vec AS vector))"
        ),
        {"t": tenant_id, "d": ds, "q": question, "sql": sql, "vec": vec},
    )
    await s.commit()


async def test_internal_token_required(client: AsyncClient) -> None:
    r = await client.post(
        "/internal/rag/retrieve",
        json={"tenant_key": "default", "question": "q"},
    )
    assert r.status_code == 403  # 内部令牌缺失


async def test_unknown_tenant_404(client: AsyncClient) -> None:
    r = await client.post(
        "/internal/rag/retrieve",
        json={"tenant_key": "ghost", "question": "q"},
        headers=INTERNAL,
    )
    assert r.status_code == 404


async def test_invalid_datasource_400(client: AsyncClient, session: AsyncSession) -> None:
    await _ensure_default_tenant(session)
    r = await client.post(
        "/internal/rag/retrieve",
        json={"tenant_key": "default", "datasource_key": "ghost", "question": "q"},
        headers=INTERNAL,
    )
    assert r.status_code == 400


async def test_recall_vector_and_history(
    client: AsyncClient, session: AsyncSession, fake_embed: None
) -> None:
    tid = await _ensure_default_tenant(session)
    # customer 向量=问句同种子(余弦距离 0,必居首);film 用无关种子
    await _seed_knowledge(
        session, tid, ds="default", table="customer", comment="客户表", embed_text="查询所有客户"
    )
    await _seed_knowledge(
        session, tid, ds="default", table="film", comment="影片表", embed_text="影片电影 movie film"
    )
    await _seed_history(
        session,
        tid,
        ds="default",
        question="查询所有客户的邮箱",
        sql="SELECT email FROM tenant_default.customer",
    )
    r = await client.post(
        "/internal/rag/retrieve",
        json={"tenant_key": "default", "question": "查询所有客户"},
        headers=INTERNAL,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # 向量路:与「客户…」文本同种子 → customer 相似度最高,排在首位
    assert body["schema_hint"].splitlines()[0].startswith("tenant_default.customer")
    # 历史路:相似问句的 SQL 进 few-shot 段
    assert "SELECT email FROM tenant_default.customer" in body["history_sql"]


async def test_recall_history_inherits_prior_tables(
    client: AsyncClient, session: AsyncSession, fake_embed: None
) -> None:
    """047 多轮继承:追问主路召回缺上轮依赖表,继承路(history 末条)补入。

    复刻 mt_005 事故形态:追问「他们各自的邮箱」主路召回被填充表占满,
    行为表 rental 只与上轮问句相似——无继承即缺表,模型越界引用清单外表。
    """
    from kongming.redis_client import get_redis

    await cast("Any", get_redis()).flushdb()
    tid = await _ensure_default_tenant(session)
    q = "他们各自的邮箱是什么"
    # 6 张填充表:向量=追问同种子(距离 0),占满主路 top-5
    for i in range(6):
        await _seed_knowledge(
            session, tid, ds="default", table=f"filler{i}", comment="填充", embed_text=q
        )
    # rental 只与上轮问句相似(主路必缺位;继承路距离 0 必中)
    await _seed_knowledge(
        session,
        tid,
        ds="default",
        table="rental",
        comment="租赁记录",
        embed_text="查询租赁次数最多的前5位客户",
    )
    r = await client.post(
        "/internal/rag/retrieve",
        json={"tenant_key": "default", "question": q, "history": ["查询租赁次数最多的前5位客户"]},
        headers=INTERNAL,
    )
    assert r.status_code == 200, r.text
    hint = r.json()["schema_hint"]
    assert "rental" in hint  # 继承路补入上轮依赖表


async def test_recall_history_merge_caps_and_dedupes(
    client: AsyncClient, session: AsyncSession, fake_embed: None
) -> None:
    """047:继承路只补主路未含的表,总量封顶 2×top-K=10。"""
    from kongming.redis_client import get_redis

    await cast("Any", get_redis()).flushdb()
    tid = await _ensure_default_tenant(session)
    q = "主路问句"
    p = "继承路问句"
    for i in range(6):
        await _seed_knowledge(
            session, tid, ds="default", table=f"main{i}", comment="主路", embed_text=q
        )
    for i in range(6):
        await _seed_knowledge(
            session, tid, ds="default", table=f"prior{i}", comment="继承", embed_text=p
        )
    r = await client.post(
        "/internal/rag/retrieve",
        json={"tenant_key": "default", "question": q, "history": [p]},
        headers=INTERNAL,
    )
    hint = r.json()["schema_hint"]
    tables = [ln for ln in hint.splitlines() if ln.startswith("tenant_default.")]
    assert len(tables) <= 10  # 封顶
    main_hit = sum(1 for t in tables if t.startswith("tenant_default.main"))
    prior_hit = sum(1 for t in tables if t.startswith("tenant_default.prior"))
    assert main_hit == 5  # 主路满额优先
    assert 1 <= prior_hit <= 5  # 继承路补入且不超预算


async def test_recall_history_cache_key_splits_on_context(
    client: AsyncClient, session: AsyncSession, fake_embed: None
) -> None:
    """047:同问句不同 history 缓存不互串(键并入历史指纹)。"""
    from kongming.redis_client import get_redis

    await cast("Any", get_redis()).flushdb()
    tid = await _ensure_default_tenant(session)
    q = "他们各自的邮箱是什么"
    for i in range(6):
        await _seed_knowledge(
            session, tid, ds="default", table=f"filler{i}", comment="填充", embed_text=q
        )
    await _seed_knowledge(
        session, tid, ds="default", table="rental_a", comment="租赁A", embed_text="上轮问句A"
    )
    await _seed_knowledge(
        session, tid, ds="default", table="rental_b", comment="租赁B", embed_text="上轮问句B"
    )
    r1 = await client.post(
        "/internal/rag/retrieve",
        json={"tenant_key": "default", "question": q, "history": ["上轮问句A"]},
        headers=INTERNAL,
    )
    r2 = await client.post(
        "/internal/rag/retrieve",
        json={"tenant_key": "default", "question": q, "history": ["上轮问句B"]},
        headers=INTERNAL,
    )
    assert "rental_a" in r1.json()["schema_hint"]
    # r2 若误读 r1 的缓存,继承路只会补 rental_a——rental_b 出现即证缓存按
    # 历史指纹分叉(rental_a 是否再现属继承路向量相似度的合法形态,不钉)
    assert "rental_b" in r2.json()["schema_hint"]


async def test_recall_fulltext_exact_table_promoted(
    client: AsyncClient, session: AsyncSession, fake_embed: None
) -> None:
    """精确表名 bed_bath_table:全文路命中并经 RRF 提权(spec 场景)。"""
    tid = await _ensure_default_tenant(session)
    # 向量种子让 customer 看起来最相似;bed_bath_table 的向量偏移
    await _seed_knowledge(
        session, tid, ds="default", table="customer", comment="客户", embed_text="查询所有客户"
    )
    await _seed_knowledge(
        session,
        tid,
        ds="default",
        table="bed_bath_table",
        comment="床浴表",
        embed_text="zzz 无关语义",
    )
    r = await client.post(
        "/internal/rag/retrieve",
        json={"tenant_key": "default", "question": "查询 bed_bath_table 数据"},
        headers=INTERNAL,
    )
    first = r.json()["schema_hint"].splitlines()[0]
    assert first.startswith("tenant_default.bed_bath_table")  # 全文修正排序


async def test_recall_alias_hits_and_hint_carries_alias(
    client: AsyncClient, session: AsyncSession, fake_embed: None
) -> None:
    """037:业务称谓经别名命中召回(向量路,embedding 文本拼入 alias);
    schema_hint 表说明段含「goods(商品表)」形态(rag delta 场景)。"""
    from kongming.redis_client import get_redis

    await cast("Any", get_redis()).flushdb()
    tid = await _ensure_default_tenant(session)
    # goods 的 embedding 文本拼入 alias「商品表」——问句同文本,余弦距离 0 必中
    await _seed_knowledge(
        session,
        tid,
        ds="default",
        table="goods",
        comment="商品基础信息",
        embed_text="商品表有多少种商品",
        alias="商品表",
    )
    await _seed_knowledge(
        session, tid, ds="default", table="film", comment="影片表", embed_text="影片电影 movie film"
    )
    r = await client.post(
        "/internal/rag/retrieve",
        json={"tenant_key": "default", "question": "商品表有多少种商品"},
        headers=INTERNAL,
    )
    assert r.status_code == 200, r.text
    first = r.json()["schema_hint"].splitlines()[0]
    assert first.startswith("tenant_default.goods(商品表)")  # 别名随表说明段注入
    # 单词中文问句走全文路也命中(tsv 拼入 alias token)
    r2 = await client.post(
        "/internal/rag/retrieve",
        json={"tenant_key": "default", "question": "商品表"},
        headers=INTERNAL,
    )
    assert "goods" in r2.json()["schema_hint"]


async def test_recall_tenant_isolation(
    client: AsyncClient, session: AsyncSession, fake_embed: None
) -> None:
    """双过滤:他租户/他数据源同名表不串(rag spec 隔离场景)。"""
    a = await _ensure_default_tenant(session)
    b = await _mk_tenant(session, "tenant_b")
    await _seed_knowledge(
        session, a, ds="default", table="orders", comment="A租户订单", embed_text="订单 orders"
    )
    await _seed_knowledge(
        session, b, ds="default", table="orders", comment="B租户订单", embed_text="订单 orders"
    )
    await _seed_knowledge(
        session, a, ds="other_ds", table="orders", comment="A租户他源订单", embed_text="订单 orders"
    )
    r = await client.post(
        "/internal/rag/retrieve",
        json={"tenant_key": "default", "question": "订单"},
        headers=INTERNAL,
    )
    hint = r.json()["schema_hint"]
    assert "A租户订单" in hint
    assert "B租户订单" not in hint  # 跨租户不串
    assert "A租户他源订单" not in hint  # 跨数据源不串


async def test_recall_usable_dynamic_datasource_ok(
    client: AsyncClient, session: AsyncSession, fake_embed: None
) -> None:
    """合法动态数据源(存在+启用+归属):召回放行(正向——UUID/str 比较回归)。"""
    import uuid as uuidlib

    tid = await _ensure_default_tenant(session)
    ds_id = str(uuidlib.uuid4())
    await session.execute(
        text(
            "INSERT INTO public.sys_datasource"
            " (id, datasource_key, tenant_id, name, host, port, database, username,"
            "  password_enc, default_schema, status)"
            " VALUES (:i, 'smoke_ds', :t, '冒烟源', '127.0.0.1', 5432, 'kongming_test',"
            "  'u', 'ZmFrZQ==', 'public', 'ACTIVE')"
        ),
        {"i": ds_id, "t": tid},
    )
    await _seed_knowledge(
        session, tid, ds="smoke_ds", table="orders", comment="订单", embed_text="订单"
    )
    r = await client.post(
        "/internal/rag/retrieve",
        json={"tenant_key": "default", "datasource_key": "smoke_ds", "question": "订单"},
        headers=INTERNAL,
    )
    assert r.status_code == 200, r.text
    assert "orders" in r.json()["schema_hint"]


async def test_recall_empty_returns_blank(
    client: AsyncClient, session: AsyncSession, fake_embed: None
) -> None:
    """空知识:返回空结果不报错(agent 侧回退内省);且空结果不写 ctx 缓存
    (冒烟实证改进:降级空结果入缓存会让恢复后服务 TTL 窗口内持续返回空)。"""
    from kongming.redis_client import get_redis

    await cast("Any", get_redis()).flushdb()
    await _ensure_default_tenant(session)
    r = await client.post(
        "/internal/rag/retrieve",
        json={"tenant_key": "default", "question": "任何问题"},
        headers=INTERNAL,
    )
    assert r.status_code == 200
    assert r.json() == {
        "schema_hint": "",
        "history_sql": "",
        "relations_hint": "",  # 023 纯增语义段(空)
        "metric_hints": "",
        "metric_hits": [],  # 076 纯增结构化口径命中(空)
        "doc_hints": [],  # 024 纯增文档段(空)
        "skill_terms": "",  # 056 纯增 skill 段(空=零包零回归)
        "skill_examples": "",
        "skill_persona": "",
    }
    keys = [k async for k in cast("Any", get_redis()).scan_iter(match="km:ragctx:*")]
    assert keys == []  # 空结果不写缓存(下次请求重试全链路)


async def test_recall_history_only_result_cached(
    client: AsyncClient, session: AsyncSession, fake_embed: None
) -> None:
    """纯历史命中(schema 空+history 有):有效形态写缓存且原样返回
    (R2-S1 口径:api 认为纯 history 命中是有效结果)。"""
    from kongming.redis_client import get_redis

    await cast("Any", get_redis()).flushdb()
    tid = await _ensure_default_tenant(session)
    await _seed_history(session, tid, ds="default", question="客户邮箱", sql="SELECT email FROM c")
    r = await client.post(
        "/internal/rag/retrieve",
        json={"tenant_key": "default", "question": "客户邮箱"},
        headers=INTERNAL,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["schema_hint"] == ""
    assert "SELECT email FROM c" in body["history_sql"]
    keys = [k async for k in cast("Any", get_redis()).scan_iter(match="km:ragctx:*")]
    assert len(keys) == 1  # 纯 history 形态照常入缓存


async def test_emb_cache_dim_drift_treated_as_miss(
    client: AsyncClient, session: AsyncSession, fake_embed: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """emb 缓存维度漂移(换模型未 bump v1):坏维度缓存按未命中重取(R2-R1 防线)。"""
    from kongming.rag.agent_callback import _emb_key
    from kongming.redis_client import get_redis

    await cast("Any", get_redis()).flushdb()
    tid = await _ensure_default_tenant(session)
    await _seed_knowledge(
        session, tid, ds="default", table="payment", comment="支付", embed_text="支付记录"
    )
    # 手插坏维度缓存(8 维)——命中后应被防线识别并重取真向量
    await get_redis().set(_emb_key("支付记录"), "[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]")
    r = await client.post(
        "/internal/rag/retrieve",
        json={"tenant_key": "default", "question": "支付记录"},
        headers=INTERNAL,
    )
    assert r.status_code == 200
    assert "payment" in r.json()["schema_hint"]  # 重取真向量后召回正常


async def test_recall_embedding_failure_degrades_fulltext(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """embedding 不可用:降级纯全文路(不报错)。"""

    async def boom(self: AgentRagClient, texts: list[str]) -> list[list[float]]:
        from kongming.rag.ports import EmbeddingError

        raise EmbeddingError("embedding 服务不可达")

    monkeypatch.setattr(AgentRagClient, "embed", boom)
    tid = await _ensure_default_tenant(session)
    await _seed_knowledge(
        session, tid, ds="default", table="inventory", comment="库存", embed_text="库存"
    )
    r = await client.post(
        "/internal/rag/retrieve",
        json={"tenant_key": "default", "question": "库存"},
        headers=INTERNAL,
    )
    assert r.status_code == 200
    assert "inventory" in r.json()["schema_hint"]  # 全文路仍可用


async def test_recall_ctx_cache_roundtrip(
    client: AsyncClient, session: AsyncSession, fake_embed: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ctx 缓存:同问句二次召回命中缓存(内容一致)。"""
    from kongming.redis_client import get_redis

    await cast("Any", get_redis()).flushdb()
    tid = await _ensure_default_tenant(session)
    await _seed_knowledge(
        session, tid, ds="default", table="payment", comment="支付表", embed_text="支付 payment"
    )
    q = {"tenant_key": "default", "question": "支付记录"}
    r1 = await client.post("/internal/rag/retrieve", json=q, headers=INTERNAL)
    keys = [k async for k in cast("Any", get_redis()).scan_iter(match="km:ragctx:*")]
    assert len(keys) == 1  # 已写缓存
    r2 = await client.post("/internal/rag/retrieve", json=q, headers=INTERNAL)
    assert r1.json() == r2.json()


async def test_recall_ctx_evicted_on_delete(
    client: AsyncClient, session: AsyncSession, fake_embed: None
) -> None:
    """知识写操作(删除)后 ctx 缓存精确失效。"""
    from kongming.auth.seed import seed_all
    from kongming.config import get_settings
    from kongming.redis_client import get_redis
    from tests.test_chat_query import _login

    await cast("Any", get_redis()).flushdb()
    tid = await _ensure_default_tenant(session)
    await _seed_knowledge(
        session, tid, ds="default", table="staff", comment="员工表", embed_text="员工 staff"
    )
    q = {"tenant_key": "default", "question": "员工"}
    await client.post("/internal/rag/retrieve", json=q, headers=INTERNAL)
    assert [k async for k in cast("Any", get_redis()).scan_iter(match="km:ragctx:*")]

    # 管理面删除该条 → 缓存被失效
    await seed_all(session, get_settings())
    token = await _login(client, "admin", "admin123")
    kid = (
        await session.execute(
            text("SELECT id FROM rag.table_descriptions WHERE table_name='staff'")
        )
    ).scalar_one()
    from tests.test_chat_query import _auth

    r = await client.delete(f"/system/rag/tables/{kid}", headers=_auth(token))
    assert r.status_code == 204
    assert not [k async for k in cast("Any", get_redis()).scan_iter(match="km:ragctx:*")]  # 已失效


async def test_ctx_evict_scoped_to_tenant(
    client: AsyncClient, session: AsyncSession, fake_embed: None
) -> None:
    """失效范围精确:A 租户知识删除只清 A 的 ctx 缓存,B 租户缓存不受扰(spec)。"""
    from kongming.redis_client import get_redis

    await cast("Any", get_redis()).flushdb()
    a = await _ensure_default_tenant(session)
    b = await _mk_tenant(session, "tenant_b2")
    await _seed_knowledge(session, a, ds="default", table="t_a", comment="A", embed_text="共享问句")
    await _seed_knowledge(session, b, ds="default", table="t_b", comment="B", embed_text="共享问句")
    q = {"tenant_key": "default", "question": "共享问句"}
    qb = {"tenant_key": "tenant_b2", "question": "共享问句"}
    await client.post("/internal/rag/retrieve", json=q, headers=INTERNAL)
    await client.post("/internal/rag/retrieve", json=qb, headers=INTERNAL)
    keys_before = sorted([k async for k in cast("Any", get_redis()).scan_iter(match="km:ragctx:*")])
    assert len(keys_before) == 2  # 两租户各一条

    # 删除 A 租户条目(管理面)→ 仅 A 缓存失效
    from kongming.auth.seed import seed_all
    from kongming.config import get_settings
    from tests.test_chat_query import _auth, _login

    await seed_all(session, get_settings())
    token = await _login(client, "admin", "admin123")
    kid = (
        await session.execute(text("SELECT id FROM rag.table_descriptions WHERE table_name='t_a'"))
    ).scalar_one()
    r = await client.delete(f"/system/rag/tables/{kid}", headers=_auth(token))
    assert r.status_code == 204
    keys_after = sorted([k async for k in cast("Any", get_redis()).scan_iter(match="km:ragctx:*")])
    assert len(keys_after) == 1  # B 租户缓存保留
    # 剩余键的租户段 = B(键名含租户 id)
    import re as _re

    assert _re.search(rf"{b}:", keys_after[0])


async def _ensure_default_tenant(s: AsyncSession) -> str:
    row = (
        await s.execute(text("SELECT id FROM public.sys_tenant WHERE tenant_key='default'"))
    ).scalar_one_or_none()
    if row is not None:
        return str(row)
    return await _mk_tenant(s, "default")


# ============ chat 回写闭环 ============


async def test_chat_success_writes_query_history(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    fake_embed: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """问答成功 → query_history 回写(原始问句+SQL+status=SUCCESS)。"""
    from kongming.chat.agent_client import AgentClient, AgentReply
    from tests.test_chat_query import _auth, _user_with_chat_perm

    reply = AgentReply(
        success=True,
        verdict="PASS",
        summary="共 2 行",
        columns=["n"],
        rows=[[2]],
        sql="SELECT count(*) FROM goods",
    )

    def _fake_query(self: AgentClient, **kw: object) -> object:  # noqa: ARG001
        return _areply(reply)

    monkeypatch.setattr(AgentClient, "query", _fake_query)
    _, token = await _user_with_chat_perm(client, session, make_user)
    r = await client.post(
        "/chat/query",
        json={"question": "有多少商品"},
        headers=_auth(token),
    )
    assert r.status_code == 200 and r.json()["success"]
    row = (
        (await session.execute(text("SELECT question, sql_text, status FROM rag.query_history")))
        .mappings()
        .one()
    )
    assert row["question"] == "有多少商品"  # 原始问句(非改写)
    assert row["sql_text"] == "SELECT count(*) FROM goods"
    assert row["status"] == "SUCCESS"


async def test_chat_rejected_no_writeback(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BLOCKED(拦截)不回写(错误 SQL 不污染 few-shot)。"""
    from kongming.chat.agent_client import AgentClient, AgentReply
    from tests.test_chat_query import _auth, _user_with_chat_perm

    reply = AgentReply(success=False, verdict="BLOCKED", sql="DELETE FROM goods")

    def _fake_query(self: AgentClient, **kw: object) -> object:  # noqa: ARG001
        return _areply(reply)

    monkeypatch.setattr(AgentClient, "query", _fake_query)
    _, token = await _user_with_chat_perm(client, session, make_user)
    await client.post("/chat/query", json={"question": "删数据"}, headers=_auth(token))
    n = (await session.execute(text("SELECT count(*) FROM rag.query_history"))).scalar_one()
    assert n == 0


async def test_writeback_failure_does_not_break_chat(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """回写内部异常:不阻断问答主链路(响应照常)。"""
    from kongming.chat.agent_client import AgentClient, AgentReply
    from kongming.rag.service import RagService
    from tests.test_chat_query import _auth, _user_with_chat_perm

    reply = AgentReply(
        success=True, verdict="PASS", columns=["n"], rows=[[1]], sql="SELECT 1", summary="ok"
    )

    def _fake_query(self: AgentClient, **kw: object) -> object:  # noqa: ARG001
        return _areply(reply)

    monkeypatch.setattr(AgentClient, "query", _fake_query)

    async def boom(self: RagService, **kw: object) -> None:
        raise RuntimeError("回写故障")

    monkeypatch.setattr(RagService, "writeback", boom)
    _, token = await _user_with_chat_perm(client, session, make_user)
    r = await client.post("/chat/query", json={"question": "查一下"}, headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["success"] is True  # 主链路未受影响


def _areply(reply: object):
    from asyncio import Future

    fut: Future[object] = Future()
    fut.set_result(reply)
    return fut
