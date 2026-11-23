"""验证全文检索配置白名单、缺省分词行为、启动自检和缓存键隔离;
缺省配置下的 SQL 与现有查询保持一致。"""

import os
import types
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
from kongming.rag.catalog import assert_fulltext_ts_config_available
from kongming.rag.documents_repo import SqlDocumentRepo
from kongming.rag.models import RagContext
from kongming.rag.repository import RagRepository

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
    """env 注入非法值 → get_settings 拒启(同款 cache_clear 试env 形态)。"""
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
    """键串直接断言:配置段在 hash 前缀段,异配置键串必异;配置段之后
    必含查询语义段(AND-only 时代旧条目在 TTL 窗口内不串味)。"""
    k1 = _ctx_key("t1", "default", "键材料", "simple")
    k2 = _ctx_key("t1", "default", "键材料", "zhparser")
    assert k1.startswith("km:ragctx:t1:default:simple:")
    assert k2.startswith("km:ragctx:t1:default:zhparser:")
    assert ":and-cw:" in k1 and ":and-cw:" in k2
    assert k1 != k2


async def test_ctx_cache_isolated_by_ts_config_without_flush() -> None:
    """双配置各自命中、旧配置键对新配置不可见(全程零 flushdb——键含配置
    即天然隔离,中途清缓存会掩盖键缺陷,缓存测试纪律)。"""
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
# 期望字面量 = 缺省 simple 的逐字 SQL 形态 + 归属维度(team_id 列与可见集合
# 子句);配置名是唯一自由度,断言语义不变。

_EXPECTED_UPSERT_SQL = (
    "INSERT INTO rag.table_descriptions"
    " (id, tenant_id, datasource_key, schema_name, table_name,"
    "  table_comment, columns_comment, sample_queries, source_type,"
    "  embedding, tsv, alias, team_id, created_at, updated_at)"
    " VALUES (:id, :tenant, :ds, :schema, :table, :comment,"
    "  CAST(:cols AS jsonb), CAST(:samples AS jsonb), :source,"
    "  CAST(:vec AS halfvec),"
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

# 内容词兜底轮:与主轮仅谓词行异(EXISTS 任一内容词命中;词条限
# length >= 2 排除单字虚词噪声;词条逐个过 plainto_tsquery,
# 外部文本不进 to_tsquery 语法位),排序/量纲/过滤不变
_EXPECTED_RECALL_FULLTEXT_OR_SQL = (
    "SELECT schema_name, table_name,"
    " ts_rank(tsv, plainto_tsquery('simple', :q)) AS score"
    " FROM rag.table_descriptions"
    " WHERE tenant_id = :t AND datasource_key = :d"
    " AND EXISTS (SELECT 1 FROM"
    " unnest(tsvector_to_array(to_tsvector('simple', :q))) AS tok"
    " WHERE length(tok) >= 2"
    "   AND tsv @@ plainto_tsquery('simple', tok))"
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
    "  to_tsvector('simple', :idx), CAST(:vec AS halfvec))"
)

_EXPECTED_DOC_RECALL_SQL = (
    "SELECT id, parent_id, source_ref,"
    " ts_rank(tsv, plainto_tsquery('simple', :q)) AS score"
    " FROM rag.document_chunks"
    " WHERE tenant_id=:t AND datasource_key=:d AND chunk_level='CHILD'"
    "   AND tsv @@ plainto_tsquery('simple', :q)"
    " ORDER BY ts_rank(tsv, plainto_tsquery('simple', :q)) DESC"
    " LIMIT :k"
)

# 内容词兜底轮(文档路):同表说明路口径,仅谓词行异
_EXPECTED_DOC_RECALL_OR_SQL = (
    "SELECT id, parent_id, source_ref,"
    " ts_rank(tsv, plainto_tsquery('simple', :q)) AS score"
    " FROM rag.document_chunks"
    " WHERE tenant_id=:t AND datasource_key=:d AND chunk_level='CHILD'"
    "   AND EXISTS (SELECT 1 FROM"
    " unnest(tsvector_to_array(to_tsvector('simple', :q))) AS tok"
    " WHERE length(tok) >= 2"
    "   AND tsv @@ plainto_tsquery('simple', tok))"
    " ORDER BY ts_rank(tsv, plainto_tsquery('simple', :q)) DESC"
    " LIMIT :k"
)


class _CaptureResult:
    """桩 execute 结果:缺省恒空集形态(mappings/all/one_or_none 全空),
    可注入预设行(供 _ScriptedSession 回放命中场景)。"""

    def __init__(self, rows: list[object] | None = None) -> None:
        self._rows: list[object] = rows or []

    def mappings(self) -> "_CaptureResult":
        return self

    def one_or_none(self) -> None:
        return None

    def all(self) -> list[object]:
        return self._rows


class _CaptureSession:
    """捕获桩会话:记录渲染出的 SQL 字符串(现算对拍;不触真库)。"""

    def __init__(self) -> None:
        self.sqls: list[str] = []

    async def execute(self, clause: object, params: object = None) -> _CaptureResult:
        self.sqls.append(str(clause))
        return _CaptureResult()


class _ScriptedSession(_CaptureSession):
    """回放桩会话:按预设顺序返回结果集;结果耗尽后返回空集。"""

    def __init__(self, results: list[list[object]]) -> None:
        super().__init__()
        self._results = list(results)

    async def execute(self, clause: object, params: object = None) -> _CaptureResult:
        self.sqls.append(str(clause))
        return _CaptureResult(self._results.pop(0) if self._results else [])


_CHUNK: dict[str, object] = {
    "seq": 1,
    "level": "CHILD",
    "parent_seq": None,
    "source_ref": "文档.md §1",
    "content": "customer data",
}


async def _upsert_sample(repo: RagRepository) -> None:
    """桩会话 upsert(SQL 对拍与 zhparser 变体共用同一写入材料)。"""
    await repo.entries.upsert_table(
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
    """桩恒空=AND 零命中场景 → 渲染 AND+内容词兜底两条 SQL(轮次
    纪律:兜底轮只在零命中后发生;两轮仅谓词行异,主体逐字同构)。"""
    cap = _CaptureSession()
    await RagRepository(cast("Any", cap)).recall.recall_fulltext(
        tenant_id="t", datasource_key="d", question="q"
    )
    assert cap.sqls == [_EXPECTED_RECALL_FULLTEXT_SQL, _EXPECTED_RECALL_FULLTEXT_OR_SQL]


async def test_recall_fulltext_and_hit_skips_fallback_round() -> None:
    """优先级纪律(spec 场景「AND 有命中时不放大混入」对拍):AND 轮
    有命中 → 仅发 1 条 SQL,单内容词条目不经兜底轮混入。"""
    hit = types.SimpleNamespace(schema_name="public", table_name="tbl", score=0.5)
    cap = _ScriptedSession([[hit]])
    hits = await RagRepository(cast("Any", cap)).recall.recall_fulltext(
        tenant_id="t", datasource_key="d", question="q"
    )
    assert len(cap.sqls) == 1 and cap.sqls[0] == _EXPECTED_RECALL_FULLTEXT_SQL
    assert len(hits) == 1 and hits[0].table_name == "tbl" and hits[0].source == "fulltext"


async def test_upsert_sql_switches_to_zhparser_single_point() -> None:
    """切 zhparser 后 SQL 与缺省形态逐字同构,唯配置名字面量替换
    (钉「配置名是唯一自由度」,防注入面与第二处拼接点)。"""
    cap = _CaptureSession()
    await _upsert_sample(RagRepository(cast("Any", cap), ts_config="zhparser"))
    assert cap.sqls == [_EXPECTED_UPSERT_SQL.replace("'simple'", "'zhparser'")]


async def test_recall_fulltext_sql_switches_to_zhparser_single_point() -> None:
    cap = _CaptureSession()
    await RagRepository(cast("Any", cap), ts_config="zhparser").recall.recall_fulltext(
        tenant_id="t", datasource_key="d", question="q"
    )
    assert cap.sqls == [
        _EXPECTED_RECALL_FULLTEXT_SQL.replace("'simple'", "'zhparser'"),
        _EXPECTED_RECALL_FULLTEXT_OR_SQL.replace("'simple'", "'zhparser'"),
    ]


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
    assert cap2.sqls == [_EXPECTED_DOC_RECALL_SQL, _EXPECTED_DOC_RECALL_OR_SQL]


async def test_doc_fulltext_and_hit_skips_fallback_round() -> None:
    """文档路同款轮次纪律:AND 命中 → 仅 1 条 SQL。"""
    row = {"id": "c1", "parent_id": None, "source_ref": "文档.md §1"}
    cap = _ScriptedSession([[row]])
    rows = await SqlDocumentRepo(cast("Any", cap)).recall_children_fulltext(
        tenant_id="t", datasource_key="d", question="q", top_k=5
    )
    assert len(cap.sqls) == 1 and cap.sqls[0] == _EXPECTED_DOC_RECALL_SQL
    assert len(rows) == 1 and rows[0]["id"] == "c1"


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
    assert cap2.sqls == [
        _EXPECTED_DOC_RECALL_SQL.replace("'simple'", "'zhparser'"),
        _EXPECTED_DOC_RECALL_OR_SQL.replace("'simple'", "'zhparser'"),
    ]


def test_repo_constructor_rejects_illegal_config() -> None:
    """仓储构造期防线:白名单外值 fail-fast(未过 settings 直构也进不来)。"""
    with pytest.raises(ValueError, match="非法值"):
        RagRepository(cast("Any", _CaptureSession()), ts_config="english")
    with pytest.raises(ValueError, match="非法值"):
        SqlDocumentRepo(cast("Any", _CaptureSession()), ts_config="english")
    with pytest.raises(ValueError, match="非法值"):
        ContextCache(None, ts_config="english")


def test_no_simple_literal_outside_single_source() -> None:
    """全仓 'simple' 字面量清零断言；唯一例外是配置单源。
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


_SKIP_ZH = "本机 PG 无 zhparser 扩展(HANDOFF「中文分词(zhparser)启用配方」装好后真跑)"


@pytest.mark.skipif(not _HAS_ZHPARSER, reason=_SKIP_ZH)
async def test_zhparser_tokenization_hits_table_description(session: AsyncSession) -> None:
    """写入和检索均使用 zhparser;AND 轮无命中时,内容词兜底可命中表说明。

    「查询客户」的 AND 轮因「查询」不在表说明中而无命中,内容词兜底应命中「客户」。
    过滤内容词可避免无差别匹配。
    """
    tid = await _mk_tenant(session, "zh_ft")
    repo = RagRepository(session, ts_config="zhparser")
    await repo.entries.upsert_table(
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
    hits = await repo.recall.recall_fulltext(
        tenant_id=tid, datasource_key="default", question="查询客户"
    )
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


@pytest.mark.skipif(not _HAS_ZHPARSER, reason=_SKIP_ZH)
async def test_zhparser_contentword_fallback_hits_after_and_miss(
    session: AsyncSession,
) -> None:
    """兜底真跑(spec 场景「虚词断裂问句经内容词兜底命中」):问句切
    『有/部/影片』,表说明仅『影片』为内容词词条——AND 零命中落内容词
    兜底轮。实现若丢失内容词过滤(回退无差别形态)本测试仍绿,由对拍桩
    的谓词字面量与 evals 基线守卫。"""
    tid = await _mk_tenant(session, "zh_cwfb")
    repo = RagRepository(session, ts_config="zhparser")
    await repo.entries.upsert_table(
        tenant_id=tid,
        datasource_key="default",
        schema_name="public",
        table_name="film_info",
        table_comment="影片信息表",
        columns_comment={"id": "影片编号"},
        sample_queries=[],
        source_type="MANUAL_UPLOAD",
        embedding=None,
        alias="",
    )
    await repo.commit()
    hits = await repo.recall.recall_fulltext(
        tenant_id=tid, datasource_key="default", question="有多少部影片"
    )
    assert any(h.table_name == "film_info" and h.source == "fulltext" for h in hits)


@pytest.mark.skipif(not _HAS_ZHPARSER, reason=_SKIP_ZH)
async def test_zhparser_doc_contentword_fallback(session: AsyncSession) -> None:
    """文档路同款内容词兜底真跑:同根因同构,虚词断裂问句经『影片』命中。"""
    tid = await _mk_tenant(session, "zh_docfb")
    repo = SqlDocumentRepo(session, ts_config="zhparser")
    await repo.replace_document(
        tenant_id=tid,
        datasource_key="default",
        doc_name="影片规则.md",
        content_hash="h1",
        chunks=[
            {
                "seq": 1,
                "level": "CHILD",
                "parent_seq": None,
                "source_ref": "影片规则.md §1",
                "content": "影片规则:一部影片可被多名用户评分。",
            }
        ],
        vectors=[],
    )
    await repo.commit()
    hits = await repo.recall_children_fulltext(
        tenant_id=tid, datasource_key="default", question="有多少部影片", top_k=5
    )
    assert hits


async def test_simple_default_chinese_whole_sentence_misses(session: AsyncSession) -> None:
    """对照组(任意环境真跑):缺省 simple 下中文整句成单 token,表说明
    中文注释不命中——问题陈述的原始形态与基线。"""
    tid = await _mk_tenant(session, "simple_ft")
    repo = RagRepository(session)  # 缺省 simple
    await repo.entries.upsert_table(
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
    hits = await repo.recall.recall_fulltext(
        tenant_id=tid, datasource_key="default", question="查询客户"
    )
    assert hits == []
