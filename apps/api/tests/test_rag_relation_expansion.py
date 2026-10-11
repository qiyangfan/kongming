"""095-relation-aware-recall 测试(宪法 Article III,先红后绿)。

覆盖:relations_touching 查询口径(1.1)/扩展纯函数一跳去重上限序(1.2)/
relations_hint 收窄基准扩展(1.3)/端口缺失与查询异常降级不阻断(1.4)/
关系写路径提交后 ctx 缓存失效(1.5)/kill switch 关停零回归(1.6)/
召回试跑透出扩展明细(1.7)/扩展的租户与数据源隔离(1.8)。
"""
# pyright: reportPrivateUsage=false

import json
from typing import Any, cast

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.semantic.repository import SqlSemanticRepo
from tests.conftest import MakeUser
from tests.test_llm_usage import INTERNAL
from tests.test_rag_gate import _register_datasource
from tests.test_rag_recall import (
    _ensure_default_tenant,
    _mk_tenant,
    _seed_knowledge,
)

EXP_DS = "rel_exp"  # 专用 ds:免疫用例间种子累积(召回按 ds 隔离)
QUESTION = "统计订单金额"


async def _embed_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    from kongming.rag.agent_callback import AgentRagClient
    from tests.test_rag_recall import _fake_vector

    async def _embed(self: object, texts: list[str]) -> list[list[float]]:
        return [_fake_vector(t) for t in texts]

    monkeypatch.setattr(AgentRagClient, "embed", _embed)


async def _seed_relation(
    s: AsyncSession,
    tenant_id: str,
    *,
    ds: str,
    source: str,
    target: str,
) -> None:
    await s.execute(
        text(
            "INSERT INTO rag.table_relations"
            " (id, tenant_id, datasource_key, source_table, target_table, join_keys, source)"
            " VALUES (gen_random_uuid(), :t, :d, :st, :tt, CAST(:jk AS jsonb), 'manual')"
        ),
        {
            "t": tenant_id,
            "d": ds,
            "st": source,
            "tt": target,
            "jk": json.dumps([{"left_col": "customer_id", "right_col": "id"}]),
        },
    )
    await s.commit()


async def _seed_expansion_scene(
    s: AsyncSession,
    tenant_id: str,
    *,
    ds: str,
    with_relation: bool = True,
) -> None:
    """标准扩展场景:orders(+4 张同种子 filler)主召回满额,customers 零命中
    待扩展;filler 占满向量 top-5 使 customers 稳出局(sha 替身相似度 ~0.7)。"""
    schema = "tenant_default"
    await _seed_knowledge(
        s,
        tenant_id,
        ds=ds,
        table="orders",
        comment="订单表",
        embed_text=QUESTION,
        schema_name=schema,
    )
    for i in range(4):
        await _seed_knowledge(
            s,
            tenant_id,
            ds=ds,
            table=f"rel_fill{i}",
            comment="填充",
            embed_text=QUESTION,
            schema_name=schema,
        )
    await _seed_knowledge(
        s,
        tenant_id,
        ds=ds,
        table="customers",
        comment="客户表",
        embed_text="zzz 无关",
        schema_name=schema,
    )
    if with_relation:
        await _seed_relation(
            s,
            tenant_id,
            ds=ds,
            source=f"{schema}.orders",
            target=f"{schema}.customers",
        )


# ============ 1.1 relations_touching 查询口径 ============


async def test_relations_touching_hits_either_end_and_double_filters(
    session: AsyncSession,
) -> None:
    """源侧命中、目标侧命中均返回;两端均在集合外不返回;跨租户/跨数据源零命中。"""
    tid_a = await _ensure_default_tenant(session)
    tid_b = await _mk_tenant(session, "rel_other_tenant")
    # A/default 被测行;A/rel_exp_other 同形行(跨数据源对照);B 不播种(跨租户对照)
    await _seed_relation(session, tid_a, ds="default", source="s.orders", target="s.customers")
    await _seed_relation(
        session, tid_a, ds="rel_exp_other", source="s.orders", target="s.customers"
    )
    repo = SqlSemanticRepo(session)

    by_source = await repo.relations_touching(
        tenant_id=tid_a, datasource_key="default", tables=["s.orders"]
    )
    assert len(by_source) == 1
    assert by_source[0]["source_table"] == "s.orders"
    assert by_source[0]["target_table"] == "s.customers"

    by_target = await repo.relations_touching(
        tenant_id=tid_a, datasource_key="default", tables=["s.customers"]
    )
    assert len(by_target) == 1
    assert by_target[0]["target_table"] == "s.customers"

    both_outside = await repo.relations_touching(
        tenant_id=tid_a, datasource_key="default", tables=["s.lonely"]
    )
    assert both_outside == []

    cross_tenant = await repo.relations_touching(
        tenant_id=tid_b, datasource_key="default", tables=["s.orders"]
    )
    assert cross_tenant == []  # A 的关系不泄漏给 B

    cross_ds = await repo.relations_touching(
        tenant_id=tid_a, datasource_key="rel_exp_none", tables=["s.orders"]
    )
    assert cross_ds == []  # default 的关系不泄漏给其他数据源


# ============ 1.2 扩展纯函数 ============


def _rel(source: str, target: str) -> dict[str, Any]:
    return {
        "source_table": source,
        "target_table": target,
        "join_keys": [{"left_col": "customer_id", "right_col": "id"}],
        "source": "manual",
    }


def test_expand_by_relations_one_hop_only() -> None:
    """一跳:对端补入,对端的对端(二跳)不补。"""
    from kongming.rag.models import expand_by_relations

    rows = [_rel("s.orders", "s.customers"), _rel("s.customers", "s.products")]
    out = expand_by_relations([("s", "orders")], rows, max_expand=2)
    assert [e.key for e in out] == [("s", "customers")]  # products 是二跳


def test_expand_by_relations_dedupes_recalled_and_repeats() -> None:
    """去重:对端已在召回集内的关系不补(零扩展);多条关系指向同表只补一次。"""
    from kongming.rag.models import expand_by_relations

    # 两端均在召回集内:该关系不产生扩展(对端已在集合,不补不占额度)
    assert (
        expand_by_relations(
            [("s", "orders"), ("s", "customers")], [_rel("s.orders", "s.customers")], max_expand=5
        )
        == []
    )
    # 多条关系指向同表:第一条补入,后续对端已被本轮补入(去重)
    dup = [_rel("s.orders", "s.customers"), _rel("s.payments", "s.customers")]
    out = expand_by_relations([("s", "orders"), ("s", "payments")], dup, max_expand=5)
    assert [e.key for e in out] == [("s", "customers")]


def test_expand_by_relations_cap_and_order_and_via() -> None:
    """上限截断;顺序=关系行输入序(尾附加序);扩展条目携带经由关系。"""
    from kongming.rag.models import expand_by_relations

    rows = [
        _rel("s.orders", "s.first"),
        _rel("s.orders", "s.second"),
        _rel("s.orders", "s.third"),
    ]
    out = expand_by_relations([("s", "orders")], rows, max_expand=2)
    assert [e.key for e in out] == [("s", "first"), ("s", "second")]  # 上限截断
    assert out[0].via["target_table"] == "s.first"  # 经由关系随条目透出

    assert expand_by_relations([("s", "orders")], rows, max_expand=0) == []


def test_expand_by_relations_symmetric_direction() -> None:
    """方向对称:源侧或目标侧命中均扩展(关系行不记录方向语义)。"""
    from kongming.rag.models import expand_by_relations

    rows = [_rel("s.orders", "s.customers")]
    out = expand_by_relations([("s", "customers")], rows, max_expand=2)
    assert [e.key for e in out] == [("s", "orders")]  # 目标侧命中反向补源表


# ============ 1.3 扩展表带 JOIN path 进 relations_hint ============


async def test_expanded_table_enters_hint_tail_and_join_narrowing(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """扩展表追加在 schema_hint 尾部;relations_hint 收窄基准随之扩展
    (覆盖扩展表与召回表的 JOIN path,而非仅主召回两两)。"""
    await _embed_stub(monkeypatch)
    tid = await _ensure_default_tenant(session)
    await _register_datasource(session, tid, EXP_DS)
    await _seed_expansion_scene(session, tid, ds=EXP_DS)
    r = await client.post(
        "/internal/rag/retrieve",
        json={"tenant_key": "default", "datasource_key": EXP_DS, "question": QUESTION},
        headers=INTERNAL,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    lines = body["schema_hint"].splitlines()
    assert any(ln.startswith("tenant_default.orders") for ln in lines)
    # 主召回满额(orders+4 filler)+ 扩展补入 customers = 6 行,customers 只能经扩展进
    idx = {ln.split("(", 1)[0]: i for i, ln in enumerate(lines)}
    assert len(idx) == 6
    assert "tenant_default.customers" in idx  # 扩展补入
    assert idx["tenant_default.customers"] == max(idx.values())  # 尾附加
    assert "tenant_default.orders" in body["relations_hint"]
    assert (
        "tenant_default.orders JOIN tenant_default.customers ON"
        " tenant_default.orders.customer_id = tenant_default.customers.id"
    ) in body["relations_hint"]  # 收窄基准含扩展表


# ============ 1.4 降级不阻断 ============


async def test_expansion_query_failure_degrades_not_blocks(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """关系查询抛异常:降级为无扩展,召回主链路不阻断、不以错误终态返回。"""
    await _embed_stub(monkeypatch)
    tid = await _ensure_default_tenant(session)
    await _register_datasource(session, tid, EXP_DS)
    await _seed_expansion_scene(session, tid, ds=EXP_DS)

    async def boom(
        self: SqlSemanticRepo, *, tenant_id: str, datasource_key: str, tables: list[str]
    ) -> list[dict[str, Any]]:
        raise RuntimeError("关系表尚未迁移")

    monkeypatch.setattr(SqlSemanticRepo, "relations_touching", boom)
    r = await client.post(
        "/internal/rag/retrieve",
        json={"tenant_key": "default", "datasource_key": EXP_DS, "question": QUESTION},
        headers=INTERNAL,
    )
    assert r.status_code == 200, r.text
    hint = r.json()["schema_hint"]
    assert "tenant_default.orders" in hint  # 主召回原样
    assert "tenant_default.customers" not in hint  # 降级为无扩展


class _RecordingExpander:
    """哨兵端口:记录调用次数(关闭/未注入形态断言零调用=零 SQL)。"""

    def __init__(self) -> None:
        self.calls = 0

    async def related_tables(
        self, *, tenant_id: str, datasource_key: str, tables: list[tuple[str, str]]
    ) -> list[dict[str, Any]]:
        self.calls += 1
        return []


class _StubEmbedder:
    """embedding 恒失败:recall 走纯全文路(最小依赖替身)。"""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        from kongming.rag.ports import EmbeddingError

        raise EmbeddingError("替身恒失败")


class _StubRepo:
    """recall 最小依赖替身:全文路单命中 orders,fetch 按键序返回条目。"""

    async def tenant_id_by_key(self, tenant_key: str) -> str | None:
        return "tid-1"

    async def datasource_usable(self, datasource_key: str, tenant_id: str) -> bool:
        return True

    async def has_knowledge(self, *, tenant_id: str, datasource_key: str) -> bool:
        # 064 闸门前置探针:recall 主链路无条件调用,协议扩展桩须同步,
        # 否则 AttributeError(本文件用例闸门缺省 off,返回值不改变行为)
        return True

    async def recall_fulltext(
        self,
        *,
        tenant_id: str,
        datasource_key: str,
        question: str,
        visible_team_ids: list[str] | None = None,  # 102:协议扩展桩须同步
    ) -> list[Any]:
        from kongming.rag.models import RecallHit

        return [
            RecallHit(
                schema_name="tenant_default", table_name="orders", score=1.0, source="fulltext"
            )
        ]

    async def fetch_entries(
        self,
        *,
        tenant_id: str,
        datasource_key: str,
        keys: list[tuple[str, str]],
        visible_team_ids: list[str] | None = None,  # 102:协议扩展桩须同步
    ) -> list[Any]:
        from kongming.rag.models import KnowledgeEntry

        return [
            KnowledgeEntry(
                id="1",
                tenant_id=tenant_id,
                datasource_key=datasource_key,
                schema_name=schema,
                table_name=table,
                table_comment="订单表",
                columns_comment={},
                sample_queries=[],
                source_type="MANUAL_UPLOAD",
                updated_at=None,
            )
            for schema, table in keys
        ]


def _make_svc(expander: object | None, *, enabled: bool = True, max_expand: int = 2) -> Any:
    from kongming.rag.cache import ContextCache
    from kongming.rag.service import RagService

    return RagService(
        _StubRepo(),  # pyright: ignore[reportArgumentType]
        embedder=_StubEmbedder(),  # pyright: ignore[reportArgumentType]
        describer=None,  # pyright: ignore[reportArgumentType]  # recall 不触润色
        cache=ContextCache(None),
        relation_expander=expander,  # pyright: ignore[reportArgumentType]
        expand_enabled=enabled,
        expand_max=max_expand,
    )


async def test_port_absent_recalls_without_expansion() -> None:
    """端口未注入(老调用方/测试):零扩展、召回主链路照常。"""
    svc = _make_svc(None)
    ctx = await svc.recall(tenant_key="default", datasource_key="default", question=QUESTION)
    assert "tenant_default.orders" in ctx.schema_hint


async def test_disabled_switch_zero_relation_queries() -> None:
    """kill switch 关停:配置一票短路,不为关闭形态付一次关系查询(零 SQL)。"""
    expander = _RecordingExpander()
    svc = _make_svc(expander, enabled=False)
    ctx = await svc.recall(tenant_key="default", datasource_key="default", question=QUESTION)
    assert expander.calls == 0
    assert "tenant_default.orders" in ctx.schema_hint


# ============ 1.5 关系写路径提交后失效 ctx 缓存 ============


async def test_relation_write_invalidates_ctx_cache(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """手工关系增/删与 introspection upsert 提交后,该租户+数据源的
    ctx 缓存被失效(写后同问句再召回即见新关系,不等待 TTL)。"""
    await _embed_stub(monkeypatch)
    from kongming.auth.seed import seed_all
    from kongming.config import get_settings
    from kongming.redis_client import get_redis
    from tests.test_chat_query import _auth, _login

    await seed_all(session, get_settings())
    token = await _login(client, "admin", "admin123")
    tid = await _ensure_default_tenant(session)
    await _seed_expansion_scene(session, tid, ds="default")
    headers = _auth(token)

    async def _ctx_keys() -> list[str]:
        return [k async for k in cast("Any", get_redis()).scan_iter(match="km:ragctx:*")]

    retrieve = {
        "tenant_key": "default",
        "datasource_key": "default",
        "question": QUESTION,
    }
    r = await client.post("/internal/rag/retrieve", json=retrieve, headers=INTERNAL)
    assert r.status_code == 200, r.text
    assert await _ctx_keys()  # 缓存已写

    r = await client.post(
        "/system/semantic/relations",
        headers=headers,
        json={
            "datasource_key": "default",
            "source_table": "tenant_default.orders",
            "target_table": "tenant_default.customers",
            "join_keys": [{"left_col": "customer_id", "right_col": "id"}],
        },
    )
    assert r.status_code == 201, r.text
    assert not await _ctx_keys()  # 手工新增 → 失效

    await client.post("/internal/rag/retrieve", json=retrieve, headers=INTERNAL)
    assert await _ctx_keys()
    rel_id = r.json()["id"]
    r = await client.delete(f"/system/semantic/relations/{rel_id}", headers=headers)
    assert r.status_code == 204
    assert not await _ctx_keys()  # 手工删除 → 失效

    await client.post("/internal/rag/retrieve", json=retrieve, headers=INTERNAL)
    assert await _ctx_keys()
    r = await client.post(
        "/system/semantic/relations/introspect",
        headers=headers,
        json={"datasource_key": "default"},
    )
    assert r.status_code == 200, r.text
    assert not await _ctx_keys()  # introspection upsert → 失效


# ============ 1.6 kill switch 关停零回归 ============


async def test_kill_switch_output_identical_to_pre_expansion(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """关停形态与引入前(无扩展)逐字一致:无扩展表、无扩展 JOIN path。"""
    await _embed_stub(monkeypatch)
    tid = await _ensure_default_tenant(session)
    await _register_datasource(session, tid, EXP_DS)
    await _seed_expansion_scene(session, tid, ds=EXP_DS)
    payload = {
        "tenant_key": "default",
        "datasource_key": EXP_DS,
        "question": QUESTION,
    }
    r_on = await client.post("/internal/rag/retrieve", json=payload, headers=INTERNAL)
    assert r_on.status_code == 200, r_on.text
    assert "tenant_default.customers" in r_on.json()["schema_hint"]  # 开:扩展生效

    from kongming.config import get_settings
    from kongming.redis_client import get_redis

    monkeypatch.setenv("KM_RAG_EXPAND_ENABLED", "false")
    get_settings.cache_clear()
    try:
        # 不先 flushdb:开启形态写入的 ctx 缓存仍在——缓存键并入扩展形态后,
        # 关停形态不得命中它(否则 TTL 窗口内返回含扩展的旧输出,违背
        # 「关停输出与开启前逐字一致」;评审 R1 缺陷钉)
        r_off = await client.post("/internal/rag/retrieve", json=payload, headers=INTERNAL)
        assert r_off.status_code == 200, r_off.text
        body = r_off.json()
        assert "tenant_default.orders" in body["schema_hint"]  # 主召回原样
        assert "tenant_default.customers" not in body["schema_hint"]  # 零扩展(=引入前)
        assert body["relations_hint"] == ""  # 收窄基准不含扩展表

        # 重算形态(清缓存后)与缓存隔离形态输出逐字一致
        await cast("Any", get_redis()).flushdb()
        r_off2 = await client.post("/internal/rag/retrieve", json=payload, headers=INTERNAL)
        assert r_off2.status_code == 200, r_off2.text
        assert r_off2.json() == body
    finally:
        get_settings.cache_clear()  # monkeypatch 撤销 env 后重建缺省设置


# ============ 1.7 试跑端点透出扩展明细 ============


async def test_recall_probe_exposes_expansion_detail(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """试跑透出:补入表/经由关系/扩展后集合;零副作用纪律不变。"""
    await _embed_stub(monkeypatch)
    from kongming.auth.seed import seed_all
    from kongming.config import get_settings
    from kongming.redis_client import get_redis
    from tests.test_chat_query import _auth, _login

    await seed_all(session, get_settings())
    token = await _login(client, "admin", "admin123")
    tid = await _ensure_default_tenant(session)
    await _register_datasource(session, tid, EXP_DS)
    await _seed_expansion_scene(session, tid, ds=EXP_DS)
    before_ops = (
        await session.execute(text("SELECT count(*) FROM public.sys_oper_log"))
    ).scalar_one()
    r = await client.get(
        "/system/rag/recall-probe",
        params={"question": QUESTION, "datasource_key": EXP_DS},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    expansion = body["expansion"]
    assert expansion["enabled"] is True
    tables = expansion["tables"]
    assert [t["table_name"] for t in tables] == ["customers"]
    assert tables[0]["schema_name"] == "tenant_default"
    assert tables[0]["via"]["source_table"] == "tenant_default.orders"
    assert tables[0]["via"]["target_table"] == "tenant_default.customers"
    after_keys = {(t["schema_name"], t["table_name"]) for t in expansion["fused_after"]}
    assert ("tenant_default", "customers") in after_keys  # 扩展后集合
    assert ("tenant_default", "orders") in after_keys
    after_ops = (
        await session.execute(text("SELECT count(*) FROM public.sys_oper_log"))
    ).scalar_one()
    assert after_ops == before_ops  # 零审计写入
    keys = [k async for k in cast("Any", get_redis()).scan_iter(match="km:ragctx:*")]
    assert keys == []  # 零缓存写入


async def test_recall_probe_expansion_requires_perm(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """无 system:rag:manage → 403(不泄漏扩展明细)。"""
    from kongming.auth.seed import seed_all
    from kongming.config import get_settings
    from tests.test_chat_query import _bind, _default_tenant_id, _login, _mk_role_with_perms

    await seed_all(session, get_settings())
    uid = await make_user("rel_noperm")
    rid = await _mk_role_with_perms(session, "REL-NOPERM", ["chat:execute"])
    await _bind(session, "sys_user_role", uid, rid)
    await _bind(session, "sys_user_tenant", uid, await _default_tenant_id(session))
    token = await _login(client, "rel_noperm")
    r = await client.get(
        "/system/rag/recall-probe",
        params={"question": "x", "datasource_key": "default"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403


# ============ 1.8 扩展的租户与数据源隔离 ============


async def test_expansion_tenant_isolation(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 租户的关系不参与 B 租户扩展:B 同形知识但零关系 → 零扩展。"""
    await _embed_stub(monkeypatch)
    tid_a = await _ensure_default_tenant(session)
    tid_b = await _mk_tenant(session, "rel_iso_b")
    ds_a, ds_b = "rel_iso_a", "rel_iso_b"
    await _register_datasource(session, tid_a, ds_a)
    await _register_datasource(session, tid_b, ds_b)
    await _seed_expansion_scene(session, tid_a, ds=ds_a, with_relation=True)
    await _seed_expansion_scene(session, tid_b, ds=ds_b, with_relation=False)

    r_a = await client.post(
        "/internal/rag/retrieve",
        json={"tenant_key": "default", "datasource_key": ds_a, "question": QUESTION},
        headers=INTERNAL,
    )
    assert r_a.status_code == 200, r_a.text
    assert "tenant_default.customers" in r_a.json()["schema_hint"]  # A 有关系:扩展

    r_b = await client.post(
        "/internal/rag/retrieve",
        json={"tenant_key": "rel_iso_b", "datasource_key": ds_b, "question": QUESTION},
        headers=INTERNAL,
    )
    assert r_b.status_code == 200, r_b.text
    hint_b = r_b.json()["schema_hint"]
    assert "tenant_default.orders" in hint_b  # B 主召回正常
    assert "tenant_default.customers" not in hint_b  # B 无关系:零扩展
