"""098 全文检索分词配置单源化:白名单/缺省 simple 零回归/启动自检/缓存隔离。

零回归红线:缺省 simple 下全部全文 SQL 形态与引入前逐字一致(现算对拍——
捕获桩会话渲染 SQL 字符串,与 098 改动前字面量断言相等)。
缓存纪律:键隔离断言不做中途清缓存(键含配置即天然隔离,清库会掩盖键缺陷)。
"""

import os
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.config import (
    DEFAULT_FULLTEXT_TS_CONFIG,
    FULLTEXT_TS_CONFIGS,
    Settings,
    get_settings,
    validated_fulltext_ts_config,
)
from kongming.rag.cache import ContextCache, _ctx_key  # pyright: ignore[reportPrivateUsage]
from kongming.rag.documents_repo import SqlDocumentRepo
from kongming.rag.models import RagContext
from kongming.rag.repository import RagRepository, assert_fulltext_ts_config_available

# ============ 1.1 配置单点:白名单枚举/缺省/非法三形态 ============


def _unset_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # 隔离开发者 shell 可能 export 的 KM_RAG_FULLTEXT_TS_CONFIG(与
    # test_config_failclosed 钉空 websearch 字段同款防渗入)
    monkeypatch.delenv("KM_RAG_FULLTEXT_TS_CONFIG", raising=False)


def test_fulltext_ts_config_default_is_simple(monkeypatch: pytest.MonkeyPatch) -> None:
    _unset_env(monkeypatch)
    s = Settings(environment="dev")
    assert s.rag_fulltext_ts_config == DEFAULT_FULLTEXT_TS_CONFIG == "simple"
    assert s.require_fulltext_ts_config() == "simple"


def test_fulltext_ts_config_legal_zhparser_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    _unset_env(monkeypatch)
    s = Settings(environment="dev", rag_fulltext_ts_config="zhparser")
    assert s.require_fulltext_ts_config() == "zhparser"


def test_fulltext_ts_config_illegal_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """白名单外值一律拒(dev/prod 同——降级=换词法,无安全降级形态)。"""
    _unset_env(monkeypatch)
    s = Settings(environment="dev", rag_fulltext_ts_config="english")
    with pytest.raises(RuntimeError, match="非法值"):
        s.require_fulltext_ts_config()
    # 领域校验闸同口径(ValueError 面,供仓储/缓存构造期防线复用)
    with pytest.raises(ValueError, match="非法值"):
        validated_fulltext_ts_config("english")


def test_fulltext_ts_config_env_driven_rejected_at_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """env 注入非法值 → get_settings 拒启(095 同款 cache_clear 试env 形态)。"""
    monkeypatch.setenv("KM_RAG_FULLTEXT_TS_CONFIG", "gogogo")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="非法值"):
            get_settings()
    finally:
        get_settings.cache_clear()  # monkeypatch 撤销 env 后重建缺省设置


def test_whitelist_single_source_has_teeth() -> None:
    """值域与缺省单源断言(第二处白名单定义即红)。"""
    assert FULLTEXT_TS_CONFIGS == ("simple", "zhparser")
    assert validated_fulltext_ts_config("simple") == "simple"
    assert validated_fulltext_ts_config("zhparser") == "zhparser"


# ============ 1.2 启动自检:pg_ts_config 存在性 fail-closed ============


def _detect_zhparser() -> bool:
    """收集期探测目标库是否有 zhparser ts_config(环境事实探测;探测失败
    视同未装 → skip,不 error)。本机 localenv/测试库均无该扩展,skip 形态
    =收集但不执行;按 HANDOFF 配方装扩展后自动转真跑。"""
    try:
        import asyncio

        import asyncpg

        dsn = os.environ.get(
            "KM_DATABASE_URL",
            "postgresql+asyncpg://kongming:kongming@localhost:5432/kongming_test",
        ).replace("+asyncpg", "")

        async def _q() -> bool:
            conn = await cast("Any", asyncpg).connect(dsn)
            try:
                v = await conn.fetchval("SELECT 1 FROM pg_ts_config WHERE cfgname='zhparser'")
                return bool(v)
            finally:
                await conn.close()

        return asyncio.run(_q())
    except Exception:  # noqa: BLE001  # 收集期探测:任何失败按「未装」处理
        return False


_HAS_ZHPARSER = _detect_zhparser()


async def test_startup_selfcheck_simple_passes(session: AsyncSession) -> None:
    """缺省 simple 在任意 PG 都存在(pg 内置),自检恒过。"""
    assert await assert_fulltext_ts_config_available(session, "simple") == "simple"


async def test_startup_selfcheck_missing_config_rejected(session: AsyncSession) -> None:
    """无扩展环境配 zhparser → fail-closed 拒启,文案明示缺项与安装指引。"""
    if _HAS_ZHPARSER:
        pytest.skip("本环境已装 zhparser,「缺失拒启」形态不可探测")
    with pytest.raises(RuntimeError, match="zhparser") as ei:
        await assert_fulltext_ts_config_available(session, "zhparser")
    assert "pg_ts_config" in str(ei.value)  # 明示缺项
    assert "HANDOFF" in str(ei.value)  # 安装配方指引


# ============ 1.3 检索上下文缓存键纳入分词配置维度 ============


def test_ctx_key_layout_contains_ts_config() -> None:
    """键串直接断言:配置段在 hash 前缀段,异配置键串必异。"""
    k1 = _ctx_key("t1", "default", "键材料", "simple")
    k2 = _ctx_key("t1", "default", "键材料", "zhparser")
    assert k1.startswith("km:ragctx:t1:default:simple:")
    assert k2.startswith("km:ragctx:t1:default:zhparser:")
    assert k1 != k2


async def test_ctx_cache_isolated_by_ts_config_without_flush() -> None:
    """双配置各自命中、旧配置键对新配置不可见(全程零 flushdb——键含配置
    即天然隔离,中途清缓存会掩盖键缺陷,095 缓存测试纪律)。"""
    from kongming.redis_client import get_redis

    r = cast("Any", get_redis())
    simple_cache = ContextCache(r)
    zh_cache = ContextCache(r, ts_config="zhparser")
    ctx = RagContext(schema_hint="tenant_default.customer(客户表)", history_sql="")
    await simple_cache.set("t1", "default", "查询所有客户", ctx)
    got = await simple_cache.get("t1", "default", "查询所有客户")
    assert got is not None and got.schema_hint == ctx.schema_hint
    # 切配置后旧缓存键不命中(读不到异配置写下的任何值)
    assert await zh_cache.get("t1", "default", "查询所有客户") is None
    # 新配置写自己的键空间,两配置键并存且互异
    await zh_cache.set(
        "t1", "default", "查询所有客户", RagContext(schema_hint="zh", history_sql="")
    )
    assert (await zh_cache.get("t1", "default", "查询所有客户")) is not None
    keys = [k async for k in r.scan_iter(match="km:ragctx:*")]
    assert len(keys) == 2 and len(set(keys)) == 2
    # 数据源写失效模式跨配置全收(失效语义不随配置漂移)
    await simple_cache.evict("t1", "default")
    assert not [k async for k in r.scan_iter(match="km:ragctx:*")]


# ============ 2.1/2.2 全文 SQL 参数化:缺省 simple 逐字对拍 + 白名单防线 ============
# 期望字面量 = 098 缺省 simple 的逐字 SQL 形态 + 102 有意新增的归属维度
# (team_id 列与可见集合子句;102 后重钉——非 098 配置渲染漂移,配置名仍是
# 唯一自由度,断言语义不变)。

_EXPECTED_UPSERT_SQL = (
    "INSERT INTO rag.table_descriptions"
    " (id, tenant_id, datasource_key, schema_name, table_name,"
    "  table_comment, columns_comment, sample_queries, source_type,"
    "  embedding, tsv, alias, team_id, created_at, updated_at)"
    " VALUES (:id, :tenant, :ds, :schema, :table, :comment,"
    "  CAST(:cols AS jsonb), CAST(:samples AS jsonb), :source,"
    "  CAST(:vec AS vector),"
    "  to_tsvector('simple', :table_t || ' ' || :alias_t || ' ' ||"
    "  :comment_t || ' ' || :cols_text),"
    "  :alias, CAST(:team AS uuid), now(), now())"
    " ON CONFLICT (tenant_id, datasource_key, schema_name, table_name)"
    " DO UPDATE SET table_comment = EXCLUDED.table_comment,"
    "  columns_comment = EXCLUDED.columns_comment,"
    "  sample_queries = EXCLUDED.sample_queries,"
    "  source_type = EXCLUDED.source_type,"
    "  embedding = CASE WHEN :source IN ('INTROSPECTION', 'AI_GENERATED')"
    "   THEN EXCLUDED.embedding"
    "   ELSE COALESCE(EXCLUDED.embedding, rag.table_descriptions.embedding) END,"
    "  tsv = to_tsvector('simple', :table_t || ' ' ||"
    "  COALESCE(CASE WHEN :source IN ('INTROSPECTION', 'AI_GENERATED',"
    " 'MANUAL_UPLOAD') AND COALESCE(rag.table_descriptions.alias, '') <> ''"
    " THEN rag.table_descriptions.alias END, EXCLUDED.alias)"
    " || ' ' || :comment_t || ' ' || :cols_text),"
    "  alias = COALESCE(CASE WHEN :source IN ('INTROSPECTION', 'AI_GENERATED',"
    " 'MANUAL_UPLOAD') AND COALESCE(rag.table_descriptions.alias, '') <> ''"
    " THEN rag.table_descriptions.alias END, EXCLUDED.alias),"
    "  team_id = CASE WHEN :team_set THEN CAST(:team AS uuid)"
    "   ELSE rag.table_descriptions.team_id END,"
    "  updated_at = now()"
)

_EXPECTED_RECALL_FULLTEXT_SQL = (
    "SELECT schema_name, table_name,"
    " ts_rank(tsv, plainto_tsquery('simple', :q)) AS score"
    " FROM rag.table_descriptions"
    " WHERE tenant_id = :t AND datasource_key = :d"
    " AND tsv @@ plainto_tsquery('simple', :q)"
    " AND (team_id IS NULL OR team_id::text = ANY(CAST(:teams AS text[])))"
    " ORDER BY ts_rank(tsv, plainto_tsquery('simple', :q)) DESC"
    " LIMIT :k"
)

_EXPECTED_DOC_FIND_SQL = (
    "SELECT id, content_hash FROM rag.documents"
    " WHERE tenant_id=:t AND datasource_key=:d AND doc_name=:n"
)

_EXPECTED_DOC_INSERT_SQL = (
    "INSERT INTO rag.documents"
    " (id, tenant_id, datasource_key, doc_name, content_hash, block_count)"
    " VALUES (:i, :t, :d, :n, :h, :bc)"
)

_EXPECTED_CHUNK_INSERT_SQL = (
    "INSERT INTO rag.document_chunks"
    " (id, document_id, tenant_id, datasource_key, chunk_level,"
    "  parent_id, seq, source_ref, content, tsv, embedding)"
    " VALUES (:i, :doc, :t, :d, :lv, :pid, :seq, :ref, :content,"
    "  to_tsvector('simple', :content), CAST(:vec AS vector))"
)

_EXPECTED_DOC_RECALL_SQL = (
    "SELECT id, parent_id, source_ref FROM rag.document_chunks"
    " WHERE tenant_id=:t AND datasource_key=:d AND chunk_level='CHILD'"
    "   AND tsv @@ plainto_tsquery('simple', :q)"
    " ORDER BY ts_rank(tsv, plainto_tsquery('simple', :q)) DESC"
    " LIMIT :k"
)


class _CaptureResult:
    """桩 execute 结果:恒空集形态(mappings/all/one_or_none 全空)。"""

    def mappings(self) -> "_CaptureResult":
        return self

    def one_or_none(self) -> None:
        return None

    def all(self) -> list[object]:
        return []


class _CaptureSession:
    """捕获桩会话:记录渲染出的 SQL 字符串(现算对拍;不触真库)。"""

    def __init__(self) -> None:
        self.sqls: list[str] = []

    async def execute(self, clause: object, params: object = None) -> _CaptureResult:
        self.sqls.append(str(clause))
        return _CaptureResult()


_CHUNK: dict[str, object] = {
    "seq": 1,
    "level": "CHILD",
    "parent_seq": None,
    "source_ref": "文档.md §1",
    "content": "customer data",
}


async def _upsert_sample(repo: RagRepository) -> None:
    """桩会话 upsert(SQL 对拍与 zhparser 变体共用同一写入材料)。"""
    await repo.upsert_table(
        tenant_id="t",
        datasource_key="d",
        schema_name="public",
        table_name="tbl",
        table_comment="表注释",
        columns_comment={"id": "主键"},
        sample_queries=[],
        source_type="MANUAL_UPLOAD",
        embedding=None,
        alias="",
    )


async def test_upsert_sql_identical_with_default_simple() -> None:
    cap = _CaptureSession()
    await _upsert_sample(RagRepository(cast("Any", cap)))
    assert cap.sqls == [_EXPECTED_UPSERT_SQL]


async def test_recall_fulltext_sql_identical_with_default_simple() -> None:
    cap = _CaptureSession()
    await RagRepository(cast("Any", cap)).recall_fulltext(
        tenant_id="t", datasource_key="d", question="q"
    )
    assert cap.sqls == [_EXPECTED_RECALL_FULLTEXT_SQL]


async def test_upsert_sql_switches_to_zhparser_single_point() -> None:
    """切 zhparser 后 SQL 与缺省形态逐字同构,唯配置名字面量替换
    (钉「配置名是唯一自由度」,防注入面与第二处拼接点)。"""
    cap = _CaptureSession()
    await _upsert_sample(RagRepository(cast("Any", cap), ts_config="zhparser"))
    assert cap.sqls == [_EXPECTED_UPSERT_SQL.replace("'simple'", "'zhparser'")]


async def test_recall_fulltext_sql_switches_to_zhparser_single_point() -> None:
    cap = _CaptureSession()
    await RagRepository(cast("Any", cap), ts_config="zhparser").recall_fulltext(
        tenant_id="t", datasource_key="d", question="q"
    )
    assert cap.sqls == [_EXPECTED_RECALL_FULLTEXT_SQL.replace("'simple'", "'zhparser'")]


async def test_doc_fulltext_sql_identical_with_default_simple() -> None:
    cap = _CaptureSession()
    await SqlDocumentRepo(cast("Any", cap)).replace_document(
        tenant_id="t",
        datasource_key="d",
        doc_name="n",
        content_hash="h",
        chunks=[dict(_CHUNK)],
        vectors=[],
    )
    assert cap.sqls == [
        _EXPECTED_DOC_FIND_SQL,
        _EXPECTED_DOC_INSERT_SQL,
        _EXPECTED_CHUNK_INSERT_SQL,
    ]
    cap2 = _CaptureSession()
    await SqlDocumentRepo(cast("Any", cap2)).recall_children_fulltext(
        tenant_id="t", datasource_key="d", question="q", top_k=5
    )
    assert cap2.sqls == [_EXPECTED_DOC_RECALL_SQL]


async def test_doc_fulltext_sql_switches_to_zhparser_single_point() -> None:
    cap = _CaptureSession()
    await SqlDocumentRepo(cast("Any", cap), ts_config="zhparser").replace_document(
        tenant_id="t",
        datasource_key="d",
        doc_name="n",
        content_hash="h",
        chunks=[dict(_CHUNK)],
        vectors=[],
    )
    assert cap.sqls[-1] == _EXPECTED_CHUNK_INSERT_SQL.replace("'simple'", "'zhparser'")
    cap2 = _CaptureSession()
    await SqlDocumentRepo(cast("Any", cap2), ts_config="zhparser").recall_children_fulltext(
        tenant_id="t", datasource_key="d", question="q", top_k=5
    )
    assert cap2.sqls == [_EXPECTED_DOC_RECALL_SQL.replace("'simple'", "'zhparser'")]


def test_repo_constructor_rejects_illegal_config() -> None:
    """仓储构造期防线:白名单外值 fail-fast(未过 settings 直构也进不来)。"""
    with pytest.raises(ValueError, match="非法值"):
        RagRepository(cast("Any", _CaptureSession()), ts_config="english")
    with pytest.raises(ValueError, match="非法值"):
        SqlDocumentRepo(cast("Any", _CaptureSession()), ts_config="english")
    with pytest.raises(ValueError, match="非法值"):
        ContextCache(None, ts_config="english")


def test_no_simple_literal_outside_single_source() -> None:
    """全仓 'simple' 字面量清零断言(098 design R3):唯一例外=配置单源
    kongming/config.py;SQL/文档串/默认参数再现字面量即红。"""
    root = Path(__file__).resolve().parent.parent / "kongming"
    offenders = [
        str(p.relative_to(root))
        for p in sorted(root.rglob("*.py"))
        if not (p.name == "config.py" and p.parent == root)
        and "'simple'" in p.read_text(encoding="utf-8")
    ]
    assert offenders == []


# ============ 2.3 中文分词集成(装扩展环境真跑;本机 skip) ============


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


_SKIP_ZH = "本机 PG 无 zhparser 扩展(HANDOFF「zhparser 安装配方」装好后真跑)"


@pytest.mark.skipif(not _HAS_ZHPARSER, reason=_SKIP_ZH)
async def test_zhparser_tokenization_hits_table_description(session: AsyncSession) -> None:
    """中文问句分词后命中表说明(写入/检索同配置 zhparser)。simple 形态下
    「查询客户」整句成单 token 必不命中,分词后「客户」词元命中——全文路
    中文第二支撑腿的核心断言。"""
    tid = await _mk_tenant(session, "zh_ft")
    repo = RagRepository(session, ts_config="zhparser")
    await repo.upsert_table(
        tenant_id=tid,
        datasource_key="default",
        schema_name="public",
        table_name="customer_order",
        table_comment="客户订单信息",
        columns_comment={"id": "订单编号"},
        sample_queries=[],
        source_type="MANUAL_UPLOAD",
        embedding=None,
        alias="",
    )
    await repo.commit()
    hits = await repo.recall_fulltext(tenant_id=tid, datasource_key="default", question="查询客户")
    assert any(h.table_name == "customer_order" and h.source == "fulltext" for h in hits)


@pytest.mark.skipif(not _HAS_ZHPARSER, reason=_SKIP_ZH)
async def test_zhparser_document_fulltext_same_config(session: AsyncSession) -> None:
    """文档面同配置单源:中文块 zhparser 分词写入,检索面同配置命中。"""
    tid = await _mk_tenant(session, "zh_doc")
    repo = SqlDocumentRepo(session, ts_config="zhparser")
    await repo.replace_document(
        tenant_id=tid,
        datasource_key="default",
        doc_name="退货政策.md",
        content_hash="h1",
        chunks=[
            {
                "seq": 1,
                "level": "CHILD",
                "parent_seq": None,
                "source_ref": "退货政策.md §1",
                "content": "退货政策:商品签收后七日内可无理由退货。",
            }
        ],
        vectors=[],
    )
    await repo.commit()
    hits = await repo.recall_children_fulltext(
        tenant_id=tid, datasource_key="default", question="退货", top_k=5
    )
    assert hits  # simple 整句成 token 不命中,分词后「退货」词元命中


async def test_simple_default_chinese_whole_sentence_misses(session: AsyncSession) -> None:
    """对照组(任意环境真跑):缺省 simple 下中文整句成单 token,表说明
    中文注释不命中——098 问题陈述的原始形态与零回归基线。"""
    tid = await _mk_tenant(session, "simple_ft")
    repo = RagRepository(session)  # 缺省 simple
    await repo.upsert_table(
        tenant_id=tid,
        datasource_key="default",
        schema_name="public",
        table_name="customer_order",
        table_comment="客户订单信息",
        columns_comment={"id": "订单编号"},
        sample_queries=[],
        source_type="MANUAL_UPLOAD",
        embedding=None,
        alias="",
    )
    await repo.commit()
    hits = await repo.recall_fulltext(tenant_id=tid, datasource_key="default", question="查询客户")
    assert hits == []
