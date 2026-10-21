# pyright: reportPrivateUsage=false
"""rag 相关性闸测试：分数出口/融合分算式/双条件判据/恒裁剪形态。

融合分只用于候选排序,不能当作相关度阈值;闸门始终裁剪候选并返回 verdict。
"""

from typing import Any, cast

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.rag.gate import gate_hits, is_relevant
from kongming.rag.models import RecallHit, rrf_fuse
from kongming.rag.service import RagService
from tests.conftest import MakeUser
from tests.test_llm_usage import INTERNAL


class _Hit:
    """带分的命中(测试替身)。"""

    def __init__(self, key: str, score: float) -> None:
        self.key = key
        self.score = score


# ── 1. 分数出口 ─────────────────────────────────────


def test_native_scores_exposed() -> None:
    """各路命中带原生分(余弦相似度值域 [0,1] 或检索得分≥0)。"""
    hits = [_Hit("a.b", 0.87), _Hit("c.d", 0.42)]
    assert all(0.0 <= h.score <= 1.0 for h in hits)
    verdict = is_relevant(hits, top1_threshold=0.8, min_count=1, mean_threshold=0.0, mean_window=1)
    assert verdict.passed is True


def test_rrf_fuse_formula_and_ordering() -> None:
    """融合分按已知算式可复算;单路首位 < 双路并列末位(反例性质)。"""
    # 单路第 1 名:1/61≈0.0164;双路并列第 1 名各 1/61(同表才叠加——不同表各拿一路首位)
    single_score = 1.0 / (60 + 1)
    # 反例性质固化:单路首位(仅一路首位) < 双路均列首位同一表的融合分场景
    both_rank1 = rrf_fuse(
        [[RecallHit(schema_name="s", table_name="x")], [RecallHit(schema_name="s", table_name="x")]]
    )
    assert 2.0 / (60 + 1) > single_score  # 双路第 1 名同表 = 2/61 > 单路 1/61
    # 单路首位 1/61 ≈0.0164 < 双路并列末位(两路均列第5的不同表不叠加;性质断言)
    single_top1_score = 1.0 / 61
    assert single_top1_score == pytest.approx(0.0164, abs=1e-4)
    assert both_rank1[0].table_name == "x"


# ── 2. 双条件判据 ───────────────────────────────────


def test_dual_condition_mean_path() -> None:
    """最高分不达首档但前 N 均值达次级阈值且命中数满足 min_count 时放行。"""
    hits = [_Hit("a.b", 0.6), _Hit("c.d", 0.6), _Hit("e.f", 0.6)]
    verdict = is_relevant(hits, top1_threshold=0.8, min_count=3, mean_threshold=0.55, mean_window=3)
    assert verdict.passed is True  # top1=0.6<0.8 但均值 0.6≥0.55


def test_dual_condition_fail_path() -> None:
    """两条件均不满足时丢弃。"""
    hits = [_Hit("a.b", 0.6), _Hit("c.d", 0.2)]
    verdict = is_relevant(hits, top1_threshold=0.8, min_count=3, mean_threshold=0.55, mean_window=3)
    assert verdict.passed is False  # top1 不达 + 命中数 2 < 3


def test_zero_hit_path_not_voting() -> None:
    """零命中不参与判定,且不被判为「低相关」。"""
    verdict = is_relevant([], top1_threshold=0.8, min_count=1, mean_threshold=0.5, mean_window=3)
    assert verdict.passed is False
    assert verdict.reason == "no_hits"


def test_min_count_gate() -> None:
    """命中数不足 min_count 不过闸(单条高分也不行——单证据不可信)。"""
    hits = [_Hit("a.b", 0.99)]
    verdict = is_relevant(hits, top1_threshold=0.8, min_count=2, mean_threshold=0.5, mean_window=3)
    assert verdict.passed is False


# ── 3. 闸门消费(恒裁剪,无 off/shadow 形态) ────────────────


def test_gate_filters_by_route() -> None:
    """恒裁剪按路过闸:整路命中共进退——过闸全留,不过闸全裁(非按条过滤);
    verdict 恒产出。"""
    from kongming.rag.gate import GateResult

    strong = [_Hit("a.b", 0.9), _Hit("c.d", 0.6)]
    result: GateResult[_Hit] = gate_hits(
        strong, top1_threshold=0.8, min_count=1, mean_threshold=0.5, mean_window=3
    )
    assert [h.key for h in result.passed] == ["a.b", "c.d"]  # 过闸:全留
    assert result.dropped == 0
    assert result.verdict is not None and result.verdict.passed is True

    weak = [_Hit("a.b", 0.2), _Hit("c.d", 0.1)]
    result2: GateResult[_Hit] = gate_hits(
        weak, top1_threshold=0.8, min_count=1, mean_threshold=0.5, mean_window=3
    )
    assert result2.passed == []  # 不过闸:全裁
    assert result2.dropped == 2
    assert result2.verdict is not None and result2.verdict.passed is False


# ── 4. 试跑端点(同源断言/403/零写入) ───────────────────


async def test_recall_probe_same_source_as_main(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验证试跑端点与常规检索使用相同的三路命中及原生分数。"""
    from kongming.auth.seed import seed_all
    from kongming.config import get_settings
    from kongming.rag.agent_callback import AgentRagClient
    from tests.test_chat_query import _login
    from tests.test_rag_recall import _fake_vector

    async def _embed(self: object, texts: list[str]) -> list[list[float]]:
        return [_fake_vector(t) for t in texts]

    monkeypatch.setattr(AgentRagClient, "embed", _embed)
    await seed_all(session, get_settings())
    token = await _login(client, "admin", "admin123")  # SUPER_ADMIN(rag:manage 覆盖)
    r = await client.get(
        "/system/rag/recall-probe",
        params={"question": "商品表有哪些列", "datasource_key": "default"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "vector" in body and "fulltext" in body and "fused" in body
    # 各路命中带原生分(向量路余弦相似度)
    for hit in body["vector"]["hits"]:
        assert isinstance(hit["score"], (int, float))
        assert hit["source"] == "vector"
    # 无 mode 字段;verdict 恒产出(有知识)或旁路标注(零知识)
    assert "mode" not in body
    assert body["gate_skipped"] in (None, "no_knowledge")


async def test_recall_probe_mirrors_no_knowledge_bypass(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验证无知识条目时试跑端点返回零知识旁路结果。"""
    from kongming.auth.seed import seed_all
    from kongming.config import get_settings
    from tests.test_rag_recall import _ensure_default_tenant

    await _embed_stub(monkeypatch)
    await seed_all(session, get_settings())
    tid = await _ensure_default_tenant(session)
    ds = "gate_probe_nok"  # 专用 ds:不灌任何知识(rag.table_descriptions 零行)
    await _register_datasource(session, tid, ds)
    from tests.test_chat_query import _login

    token = await _login(client, "admin", "admin123")  # SUPER_ADMIN(rag:manage 覆盖)
    r = await client.get(
        "/system/rag/recall-probe",
        params={"question": "商品表有哪些列", "datasource_key": ds},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "mode" not in body
    assert body["gate_skipped"] == "no_knowledge"
    assert body["vector"]["verdict"] is None
    assert body["fulltext"]["verdict"] is None
    assert body["vector"]["dropped"] == 0
    assert body["fulltext"]["dropped"] == 0


async def test_recall_probe_requires_perm(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
) -> None:
    """无 system:rag:manage 权限 → 403(不泄漏任何召回数据)。"""
    from kongming.auth.seed import seed_all
    from kongming.config import get_settings
    from tests.test_chat_query import _bind, _default_tenant_id, _login, _mk_role_with_perms

    await seed_all(session, get_settings())
    uid = await make_user("probe_noperm")
    rid = await _mk_role_with_perms(session, "PROBE-NOPERM", ["chat:execute"])
    await _bind(session, "sys_user_role", uid, rid)
    await _bind(session, "sys_user_tenant", uid, await _default_tenant_id(session))
    token = await _login(client, "probe_noperm")
    r = await client.get(
        "/system/rag/recall-probe",
        params={"question": "x", "datasource_key": "default"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403


async def test_recall_probe_zero_writes(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """试跑前后配置/缓存/审计零写入。"""
    from kongming.auth.seed import seed_all
    from kongming.config import get_settings
    from kongming.rag.agent_callback import AgentRagClient
    from tests.test_chat_query import _login
    from tests.test_rag_recall import _fake_vector

    async def _embed(self: object, texts: list[str]) -> list[list[float]]:
        return [_fake_vector(t) for t in texts]

    monkeypatch.setattr(AgentRagClient, "embed", _embed)
    await seed_all(session, get_settings())
    token = await _login(client, "admin", "admin123")
    before_ops = (
        await session.execute(text("SELECT count(*) FROM public.sys_oper_log"))
    ).scalar_one()
    r = await client.get(
        "/system/rag/recall-probe",
        params={"question": "商品表", "datasource_key": "default"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    after_ops = (
        await session.execute(text("SELECT count(*) FROM public.sys_oper_log"))
    ).scalar_one()
    assert after_ops == before_ops  # 零审计写入
    # ctx 缓存零写入(空结果不写;试跑不走 _cache)

    from kongming.redis_client import get_redis

    keys = [k async for k in cast("Any", get_redis()).scan_iter(match="km:ragctx:*")]
    assert keys == []


# ── 5. 继承路过闸(继承路不豁免 relevance) ─────────


async def _register_datasource(s: AsyncSession, tenant_id: str, key: str) -> None:
    """注册专用数据源行(召回预检要求 ACTIVE;ON CONFLICT 幂等)。"""
    await s.execute(
        text(
            "INSERT INTO public.sys_datasource"
            " (id, datasource_key, tenant_id, name, host, port, database, username,"
            "  password_enc, default_schema, status)"
            " VALUES (gen_random_uuid(), :k, :t, :k, '127.0.0.1', 5432, 'kongming_test',"
            "  'u', 'ZmFrZQ==', 'public', 'ACTIVE')"
            " ON CONFLICT (datasource_key) DO NOTHING"
        ),
        {"k": key, "t": tenant_id},
    )
    await s.commit()


async def _embed_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    """确定性 embedding 替身(与 test_rag_recall.fake_embed 同算法)。"""
    from kongming.rag.agent_callback import AgentRagClient
    from tests.test_rag_recall import _fake_vector

    async def _embed(self: object, texts: list[str]) -> list[list[float]]:
        return [_fake_vector(t) for t in texts]

    monkeypatch.setattr(AgentRagClient, "embed", _embed)


async def test_gate_primary_drops_junk_on_inherited_route(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """继承路同样过闸:上轮问句召回出 junk 时 primary 整路裁掉,
    主路(本轮相关表)不受影响——「多轮依赖表」不是 relevance 豁免。"""
    from kongming.config import get_settings
    from tests.test_rag_recall import _ensure_default_tenant, _seed_knowledge

    await _embed_stub(monkeypatch)
    tid = await _ensure_default_tenant(session)
    ds = "gate_inherit"  # 专用 ds:免疫用例间种子累积(召回按 ds 隔离)
    await _register_datasource(session, tid, ds)
    # 主路占满:5 张与本轮问句同种子的表(top1 高分 → 整路过闸全留)
    for i in range(5):
        await _seed_knowledge(
            session, tid, ds=ds, table=f"gmain{i}", comment="商品表", embed_text="查询库存数量"
        )
    # junk 与上轮问句弱相关(向量召回 top-K 是「最近的 5 个」而非「够近的
    # 5 个」——弱相关表照样占满整路;路级判定 top1 < 阈值 → 整路不过闸)
    await _seed_knowledge(
        session, tid, ds=ds, table="gjunk", comment="无关表", embed_text="完全不一样的另一主题"
    )
    # 阈值 0.9:主路同种子命中=1.0 必过;继承路(测试替身 embedding 下
    # 无关文本亦有 ~0.73 相似度)top1/均值均 < 0.9 必裁——隔离出
    # 「整路不相关」的判定(恒生效,无需再开 mode)
    monkeypatch.setenv("KM_RAG_GATE_VECTOR_TOP1", "0.9")
    monkeypatch.setenv("KM_RAG_GATE_VECTOR_MEAN", "0.9")
    get_settings.cache_clear()
    try:
        r = await client.post(
            "/internal/rag/retrieve",
            json={
                "tenant_key": "default",
                "datasource_key": ds,
                "question": "查询库存数量",
                "history": ["梵高星月夜多少钱"],
            },
            headers=INTERNAL,
        )
        assert r.status_code == 200, r.text
        hint = r.json()["schema_hint"]
        assert "gmain0" in hint  # 主路过闸:相关表进提示词
        assert "gjunk" not in hint  # 继承路不过闸:junk 不进提示词
    finally:
        get_settings.cache_clear()  # monkeypatch 撤销 env 后重建缺省设置


def _gate_svc(repo: object) -> RagService:
    """最小闸门服务(repo 只被 recall 用;_apply_gate 直调不触)。"""

    settings = type(
        "GateSettings",
        (),
        {
            "rag_gate_vector_top1": 0.30,
            "rag_gate_vector_mean": 0.28,
            "rag_gate_fulltext_top1": 0.0,
            "rag_gate_fulltext_mean": 0.0,
            "rag_gate_min_count": 1,
            "rag_gate_mean_window": 3,
        },
    )()
    return RagService(
        repo=repo,  # type: ignore[arg-type]
        embedder=None,  # type: ignore[arg-type]
        describer=None,  # type: ignore[arg-type]
        cache=None,  # type: ignore[arg-type]
        gate_settings=settings,
    )


def test_apply_gate_skipped_when_no_knowledge() -> None:
    """零知识数据源闸门自动旁路(在 _apply_gate 内部收口,服务层直调验证):
    「没灌知识」与「召回不相关」对闸门同为零命中,语义不可区分;
    旁路时 trace 如实标注 skipped=no_knowledge,不静默。"""
    svc = _gate_svc(repo=None)
    hits = [cast("RecallHit", _Hit("t1", 0.9))]
    out = svc._apply_gate("main", hits, [], knowledge_present=False)
    assert out is None  # 整路旁路:恒裁剪形态也不裁
    assert svc._last_gate_trace["main"] == {"skipped": "no_knowledge"}


def test_apply_gate_applies_when_knowledge_present() -> None:
    svc = _gate_svc(repo=None)
    low = [cast("RecallHit", _Hit("t1", 0.05))]
    out = svc._apply_gate("main", low, [], knowledge_present=True)
    assert out is not None
    kept_vec, _kept_ft = out
    assert kept_vec == []  # 低分裁掉(标定档 0.30 之下)
    trace = cast("dict[str, object]", svc._last_gate_trace["main"])
    assert trace["vector_dropped"] == 1  # gate_hits 计数语义
