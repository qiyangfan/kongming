"""文档实体抽取与检索扩展测试:伴表级联/词法兜底/实体 join/租户过滤/降级零回归/出站留痕/句界校准。"""
# pyright: reportPrivateUsage=false

from collections.abc import Callable
from typing import Any

import pytest
from httpx import AsyncClient, MockTransport, Request, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.config import Settings, get_settings
from kongming.rag.chunker import chunk_document
from kongming.rag.documents_repo import SqlDocumentRepo
from kongming.rag.documents_service import upload_document
from kongming.rag.entity_extract import (
    DocEntityExtractor,
    EntityExtractError,
    build_doc_entity_extractor,
    normalize_entity_name,
)
from tests.conftest import MakeUser
from tests.test_toolcall import _bind, _login


class _FakeExtractor:
    """脚本化抽取器(extract/query_entities 可注入;error=True 恒抛)。

    结构化实现 EntityExtractorPort(计划可调用体显式定型,lambda 参数随签名推断)。"""

    def __init__(
        self,
        *,
        extract_plan: Callable[[str, list[str]], list[list[str]]] | None = None,
        query_plan: Callable[[str], list[str]] | None = None,
        error: bool = False,
    ) -> None:
        self.extract_plan = extract_plan
        self.query_plan = query_plan
        self.error = error
        self.calls = 0

    async def extract(self, parent_text: str, child_texts: list[str]) -> list[list[str]]:
        self.calls += 1
        if self.error:
            raise EntityExtractError("unavailable", "boom")
        if self.extract_plan is not None:
            return list(self.extract_plan(parent_text, child_texts))
        return [[] for _ in child_texts]

    async def query_entities(self, question: str, *, max_entities: int = 8) -> list[str]:
        self.calls += 1
        if self.error:
            raise EntityExtractError("unavailable", "boom")
        if self.query_plan is not None:
            return list(self.query_plan(question))[:max_entities]
        return []


class _FakeEmbedder:
    """定维假向量(embedding 出站替换;列迁移 0058 后为 halfvec(2560) 定长)。"""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 2560 for _ in texts]


def _doc_text(name: str) -> str:
    return f"# {name}\n\nVIP 会员享专属通道,逾期费率 0.5 元每日每部。\n"


async def _tid_default(session: AsyncSession) -> str:
    return str(
        (
            await session.execute(
                text("SELECT id FROM public.sys_tenant WHERE tenant_key='default'")
            )
        ).scalar_one()
    )


async def _admin_login(session: AsyncSession, client: AsyncClient) -> str:
    await seed_all(session, get_settings())
    return _login(get_settings().admin_subject)


async def _upload_doc(
    session: AsyncSession, *, tenant_id: str, datasource_key: str, doc_name: str
) -> str:
    repo = SqlDocumentRepo(session)
    doc_id, _, _ = await upload_document(
        repo,
        _FakeEmbedder(),
        tenant_id=tenant_id,
        datasource_key=datasource_key,
        doc_name=doc_name,
        text=_doc_text(doc_name),
    )
    await repo.commit()
    return doc_id


async def _first_child_id(session: AsyncSession, doc_id: str) -> str:
    return str(
        (
            await session.execute(
                text(
                    "SELECT id FROM rag.document_chunks"
                    " WHERE document_id=:d AND chunk_level='CHILD' LIMIT 1"
                ),
                {"d": doc_id},
            )
        ).scalar_one()
    )


async def _seed_entity(
    session: AsyncSession, *, chunk_id: str, tenant_id: str, datasource_key: str, name: str
) -> None:
    norm = normalize_entity_name(name)
    await session.execute(
        text(
            "INSERT INTO rag.document_chunk_entities"
            " (id, document_id, chunk_id, tenant_id, datasource_key, name, name_normalized,"
            "  name_tsv)"
            " VALUES (gen_random_uuid(),"
            "  (SELECT document_id FROM rag.document_chunks WHERE id=:c),"
            "  :c, :t, :d, :n, :nn, to_tsvector('simple', CAST(:nn AS varchar)))"
        ),
        {"c": chunk_id, "t": tenant_id, "d": datasource_key, "n": name, "nn": norm},
    )
    await session.commit()


# ============ 纯函数:规范化 / 工厂出口校验 ============


def _entity_settings(
    *,
    enabled: bool = True,
    base_url: str = "https://llm.example.com/v1",
    api_key: str = "sk-x",
    model: str = "m",
    allow_private: bool = False,
) -> Settings:
    """实体抽取测试配置基座(显式逐参,防 ** 解包让全字段漂成 bool | str)。"""
    return Settings(
        doc_entity_enabled=enabled,
        doc_entity_llm_base_url=base_url,
        doc_entity_llm_api_key=api_key,
        doc_entity_llm_model=model,
        doc_entity_allow_private_llm=allow_private,
    )


def test_normalize_entity_name_nfkc_casefold_space() -> None:
    assert normalize_entity_name("  VIP　会员 ") == "vip 会员"
    assert normalize_entity_name("Gold-Card") == "gold-card"
    assert normalize_entity_name("ＡＢＣ") == "abc"


def test_build_extractor_disabled_returns_none() -> None:
    assert build_doc_entity_extractor(_entity_settings(enabled=False)) is None


def test_build_extractor_incomplete_config_returns_none() -> None:
    assert build_doc_entity_extractor(_entity_settings()) is not None
    assert build_doc_entity_extractor(_entity_settings(api_key="")) is None


def test_build_extractor_private_host_requires_explicit_exemption() -> None:
    assert build_doc_entity_extractor(_entity_settings(base_url="http://127.0.0.1:9000/v1")) is None
    assert (
        build_doc_entity_extractor(
            _entity_settings(base_url="http://127.0.0.1:9000/v1", allow_private=True)
        )
        is not None
    )


def test_build_extractor_public_http_rejected_even_with_exemption() -> None:
    assert (
        build_doc_entity_extractor(
            _entity_settings(base_url="http://llm.example.com/v1", allow_private=True)
        )
        is None
    )


# ============ 存储与级联(真 PG;迁移 0063 经 conftest upgrade head) ============


async def test_entity_rows_and_cascade(client: AsyncClient, session: AsyncSession) -> None:
    token = await _admin_login(session, client)
    headers = {"Authorization": f"Bearer {token}", "X-Tenant-Id": "default"}
    r: Response = await client.post(
        "/system/document/upload",
        headers=headers,
        files={"file": ("实体文档.md", _doc_text("实体文档").encode(), "text/markdown")},
        data={"datasource_key": "default"},
    )
    assert r.status_code == 200
    doc_id = r.json()["doc_id"]
    chunk_id = await _first_child_id(session, doc_id)
    await _seed_entity(
        session,
        chunk_id=chunk_id,
        tenant_id=await _tid_default(session),
        datasource_key="default",
        name="VIP",
    )
    n_before = (
        await session.execute(
            text("SELECT count(*) FROM rag.document_chunk_entities WHERE document_id=:d"),
            {"d": doc_id},
        )
    ).scalar_one()
    assert n_before == 1
    # 文档删除级联清理实体行(FK CASCADE)
    r2 = await client.delete(
        f"/system/document/documents/{doc_id}", headers={"Authorization": f"Bearer {token}"}
    )
    assert r2.status_code == 204
    n_after = (
        await session.execute(text("SELECT count(*) FROM rag.document_chunk_entities"))
    ).scalar_one()
    assert n_after == 0


async def test_entity_join_tenant_and_datasource_filtered(
    session: AsyncSession, make_user: MakeUser
) -> None:
    """他租户/他数据源实体行 SHALL NOT 进 join 结果(与两路同口径双过滤)。"""
    await seed_all(session, get_settings())
    uid = await make_user("ent_probe")
    tid = await _tid_default(session)
    await _bind(session, "sys_user_tenant", uid, tid)
    repo = SqlDocumentRepo(session)
    doc_id = await _upload_doc(
        session, tenant_id=tid, datasource_key="default", doc_name="join域.md"
    )
    chunk_id = await _first_child_id(session, doc_id)
    await _seed_entity(
        session, chunk_id=chunk_id, tenant_id=tid, datasource_key="default", name="VIP"
    )
    # 他数据源实体(同租户同块不同名,绕开块+名唯一键)——双过滤不得互窜
    await _seed_entity(
        session, chunk_id=chunk_id, tenant_id=tid, datasource_key="other", name="OTHERTAG"
    )
    hits = await repo.recall_children_by_entities(
        tenant_id=tid, datasource_key="default", names=["vip"], limit=8
    )
    assert len(hits) == 1
    assert str(hits[0]["id"]) == chunk_id
    assert hits[0]["match_count"] == 1
    # 入池门槛:查询实体含未播种的第二个名,子块仅命中 vip(共享数 1<2)→ 不入池
    assert (
        await repo.recall_children_by_entities(
            tenant_id=tid, datasource_key="default", names=["vip", "逾期费"], limit=8, min_shared=2
        )
        == []
    )
    # 播种第二个实体后共享数达标 → 入池且原生分=2
    await _seed_entity(
        session, chunk_id=chunk_id, tenant_id=tid, datasource_key="default", name="逾期费"
    )
    hits2 = await repo.recall_children_by_entities(
        tenant_id=tid, datasource_key="default", names=["vip", "逾期费"], limit=8, min_shared=2
    )
    assert len(hits2) == 1 and hits2[0]["match_count"] == 2
    # 词法兜底同样双过滤:default 查不到 other 的实体,反之亦然
    names = await repo.match_entity_names(
        tenant_id=tid, datasource_key="default", question="VIP 有什么权益", limit=20
    )
    assert names == ["vip"]
    assert (
        await repo.match_entity_names(
            tenant_id=tid, datasource_key="default", question="OTHERTAG 在哪", limit=20
        )
        == []
    )
    assert (
        await repo.match_entity_names(
            tenant_id=tid, datasource_key="other", question="VIP 有什么权益", limit=20
        )
        == []
    )


async def test_entity_query_path_adds_hits(client: AsyncClient, session: AsyncSession) -> None:
    """注入抽取器:双实体共享子块入池(门槛语义);单实体/未注入均无实体路命中。"""
    token = await _admin_login(session, client)
    headers = {"Authorization": f"Bearer {token}", "X-Tenant-Id": "default"}
    r = await client.post(
        "/system/document/upload",
        headers=headers,
        files={"file": ("权益.md", _doc_text("权益").encode(), "text/markdown")},
        data={"datasource_key": "default"},
    )
    assert r.status_code == 200
    doc_id = r.json()["doc_id"]
    chunk_id = await _first_child_id(session, doc_id)
    tid = await _tid_default(session)
    await _seed_entity(
        session, chunk_id=chunk_id, tenant_id=tid, datasource_key="default", name="VIP"
    )
    await _seed_entity(
        session, chunk_id=chunk_id, tenant_id=tid, datasource_key="default", name="逾期费"
    )
    probe = "/system/document/retrieval-probe"
    params = {"datasource_key": "default", "question": "VIP 专属通道是什么"}
    # 未注入抽取器(部署缺省):无实体路痕迹
    r1 = await client.get(probe, headers=headers, params=params)
    assert r1.status_code == 200
    body1 = r1.json()
    assert set(body1["paths"].keys()) == {"vector", "fulltext"}
    # 注入脚本抽取器:实体路出现,双实体共享命中(原生分=共享实体数 2)
    import kongming.rag.entity_extract as ee

    orig = ee.build_doc_entity_extractor
    r2: Response | None = None
    r3: Response | None = None
    try:
        ee.build_doc_entity_extractor = lambda settings=None: _FakeExtractor(  # type: ignore[assignment]
            query_plan=lambda q: ["vip", "逾期费"]
        )
        r2 = await client.get(probe, headers=headers, params=params)
        # 单查询实体:合并集不足门槛,整路短路为空(不入池挤占融合池)
        ee.build_doc_entity_extractor = lambda settings=None: _FakeExtractor(  # type: ignore[assignment]
            query_plan=lambda q: ["vip"]
        )
        r3 = await client.get(probe, headers=headers, params=params)
    finally:
        ee.build_doc_entity_extractor = orig
    assert r2 is not None and r2.status_code == 200
    body2 = r2.json()
    assert set(body2["paths"].keys()) == {"vector", "fulltext", "entity"}
    assert body2["paths"]["entity"], "双实体共享应命中 VIP 子块"
    assert body2["paths"]["entity"][0]["score"] == 2
    assert r3 is not None and r3.status_code == 200
    assert r3.json()["paths"]["entity"] == []


async def test_upload_with_extractor_persists_entities(session: AsyncSession) -> None:
    await seed_all(session, get_settings())
    tid = await _tid_default(session)
    repo = SqlDocumentRepo(session)
    fake = _FakeExtractor(
        extract_plan=lambda parent, children: [["VIP", "逾期费率"] for _ in children]
    )
    doc_id, _, _ = await upload_document(
        repo,
        _FakeEmbedder(),
        tenant_id=tid,
        datasource_key="default",
        doc_name="抽取落库.md",
        text=_doc_text("抽取落库"),
        entity_extractor=fake,
    )
    await repo.commit()
    assert fake.calls >= 1  # 每父块一次
    rows = (
        (
            await session.execute(
                text(
                    "SELECT name_normalized FROM rag.document_chunk_entities WHERE document_id=:d"
                ),
                {"d": doc_id},
            )
        )
        .scalars()
        .all()
    )
    assert set(rows) == {"vip", "逾期费率"}


async def test_upload_extract_failure_degrades(session: AsyncSession) -> None:
    """抽取失败降级:上传成功完成、零实体行、无异常逃逸。"""
    await seed_all(session, get_settings())
    tid = await _tid_default(session)
    repo = SqlDocumentRepo(session)
    fake = _FakeExtractor(error=True)
    doc_id, blocks, _ = await upload_document(
        repo,
        _FakeEmbedder(),
        tenant_id=tid,
        datasource_key="default",
        doc_name="抽取失败.md",
        text=_doc_text("抽取失败"),
        entity_extractor=fake,
    )
    await repo.commit()
    assert blocks > 0
    n = (
        await session.execute(
            text("SELECT count(*) FROM rag.document_chunk_entities WHERE document_id=:d"),
            {"d": doc_id},
        )
    ).scalar_one()
    assert n == 0


async def test_upload_enrich_db_error_non_blocking(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """enrich 编排的 DB 异常不逃逸上传(文档已落库成功,抽取只降级)。"""
    await seed_all(session, get_settings())
    tid = await _tid_default(session)
    repo = SqlDocumentRepo(session)

    async def _boom(self: SqlDocumentRepo, *, tenant_id: str, doc_id: str) -> dict[str, Any] | None:
        raise RuntimeError("db down")

    monkeypatch.setattr(SqlDocumentRepo, "fetch_doc_for_enrich", _boom)
    fake = _FakeExtractor(extract_plan=lambda parent, children: [["VIP"] for _ in children])
    doc_id, blocks, _ = await upload_document(
        repo,
        _FakeEmbedder(),
        tenant_id=tid,
        datasource_key="default",
        doc_name="补抽库错.md",
        text=_doc_text("补抽库错"),
        entity_extractor=fake,
    )
    await repo.commit()
    assert doc_id
    assert blocks > 0
    n_docs = (
        await session.execute(
            text("SELECT count(*) FROM rag.documents WHERE id=CAST(:d AS uuid)"), {"d": doc_id}
        )
    ).scalar_one()
    assert n_docs == 1


async def test_upload_overlong_entity_name_skipped(session: AsyncSession) -> None:
    """超列宽实体名跳过(单条不毒化整批落库),其余实体照常入库。"""
    await seed_all(session, get_settings())
    tid = await _tid_default(session)
    repo = SqlDocumentRepo(session)
    long_name = "超" * 300
    fake = _FakeExtractor(
        extract_plan=lambda parent, children: [["VIP", long_name] for _ in children]
    )
    doc_id, _, _ = await upload_document(
        repo,
        _FakeEmbedder(),
        tenant_id=tid,
        datasource_key="default",
        doc_name="超长名.md",
        text=_doc_text("超长名"),
        entity_extractor=fake,
    )
    await repo.commit()
    rows = (
        (
            await session.execute(
                text(
                    "SELECT name_normalized FROM rag.document_chunk_entities WHERE document_id=:d"
                ),
                {"d": doc_id},
            )
        )
        .scalars()
        .all()
    )
    assert set(rows) == {"vip"}


# ============ 出站留痕(status=结果态口径;MockTransport 零真实出站) ============


def _llm_handler(content: bytes | None) -> Callable[[Request], Response]:
    """MockTransport 处理器:content=None 时返回非 JSON 报文(畸形形态)。"""

    def handler(request: Request) -> Response:
        if content is None:
            return Response(200, content=b"not-json")
        return Response(200, json={"choices": [{"message": {"content": content.decode()}}]})

    return handler


def _extractor_with_transport(handler: Callable[[Request], Response]) -> DocEntityExtractor:
    return DocEntityExtractor(
        base_url="https://llm.example.com/v1",
        api_key="sk-x",
        model="m",
        timeout_s=1.0,
        transport=MockTransport(handler),
    )


async def _doc_entity_usage_statuses(session: AsyncSession) -> list[str]:
    rows = await session.execute(
        text(
            "SELECT status FROM public.sys_llm_usage"
            " WHERE task='doc_entity' AND model='m'"
            "   AND created_at >= now() - interval '5 seconds'"
        )
    )
    return [str(r[0]) for r in rows.all()]


async def test_extract_success_records_usage(session: AsyncSession) -> None:
    extractor = _extractor_with_transport(_llm_handler(b'{"entities": [["vip"]]}'))
    out = await extractor.extract("父块文本", ["子块一"])
    assert out == [["vip"]]
    assert await _doc_entity_usage_statuses(session) == ["SUCCESS"]


async def test_extract_malformed_body_records_error(session: AsyncSession) -> None:
    """HTTP 200 但报文畸形:抽取降级异常 + 留痕 status=ERROR(结果态口径)。"""
    extractor = _extractor_with_transport(_llm_handler(None))
    with pytest.raises(EntityExtractError):
        await extractor.extract("父块文本", ["子块一"])
    assert await _doc_entity_usage_statuses(session) == ["ERROR"]


async def test_enrich_endpoint_disabled_and_enabled_forms(
    client: AsyncClient, session: AsyncSession
) -> None:
    await seed_all(session, get_settings())
    token = await _admin_login(session, client)
    headers = {"Authorization": f"Bearer {token}", "X-Tenant-Id": "default"}
    r = await client.post(
        "/system/document/upload",
        headers=headers,
        files={"file": ("补抽.md", _doc_text("补抽").encode(), "text/markdown")},
        data={"datasource_key": "default"},
    )
    assert r.status_code == 200
    doc_id = r.json()["doc_id"]
    # 未启用:400 DOC_ENTITY_DISABLED
    r1 = await client.post(f"/system/document/documents/{doc_id}/enrich", headers=headers)
    assert r1.status_code == 400
    assert r1.json()["detail"] == "DOC_ENTITY_DISABLED"
    # 启用(monkeypatch 工厂→脚本抽取器):覆盖式落库返回行数
    import kongming.rag.entity_extract as ee

    fake = _FakeExtractor(extract_plan=lambda parent, children: [["VIP"] for _ in children])
    orig = ee.build_doc_entity_extractor
    try:
        ee.build_doc_entity_extractor = lambda settings=None: fake  # type: ignore[assignment]
        r2 = await client.post(f"/system/document/documents/{doc_id}/enrich", headers=headers)
        assert r2.status_code == 200
        assert r2.json()["entities"] > 0
        # 不存在文档 404 同形
        missing = "00000000-0000-7fff-ffff-000000000001"
        r3 = await client.post(f"/system/document/documents/{missing}/enrich", headers=headers)
        assert r3.status_code == 404
    finally:
        ee.build_doc_entity_extractor = orig


# ============ chunker 句界校准(搭车件) ============


def _long_text() -> str:
    sentence = "这是一句用于切分测试的完整句子,长度适中并且以句号结尾。"
    return "标题句。\n" + sentence * 12  # ~600 字符,多句


def test_chunker_default_mode_unchanged() -> None:
    """缺省关:切分与既有实现逐字一致(定长滑窗步进)。"""
    text = _long_text()
    chunks = chunk_document("校准.md", text)
    children = [str(c["content"]) for c in chunks if c["level"] == "CHILD"]
    parent = next(c for c in chunks if c["level"] == "PARENT")
    # 既有公式复算:起点=0,150,300,...;尾块跳过规则
    ptext = str(parent["content"])
    expected: list[str] = []
    i = 0
    while i < len(ptext):
        tail = ptext[i : i + 200]
        if expected and len(tail) <= 50:
            break
        expected.append(tail)
        i += 150
    assert children == expected


def test_chunker_sentence_mode_boundaries_and_coverage() -> None:
    """句界校准:非尾块右缘落句界;内容零丢失;纯函数确定性。"""
    text = _long_text()
    chunks = chunk_document("校准.md", text, sentence_boundary=True)
    children = [str(c["content"]) for c in chunks if c["level"] == "CHILD"]
    assert len(children) >= 2
    for c in children[:-1]:
        assert c[-1:] in ("。", "!", "?", ";", "\n"), f"非尾块未落句界: {c[-10:]!r}"
    assert len(children[-1]) > 0
    # 内容零丢失(overlap 允许重复,不允许缺口):核心句完整出现在拼接中
    assert "这是一句用于切分测试的完整句子" in "".join(children)
    # 确定性
    again = chunk_document("校准.md", text, sentence_boundary=True)
    assert [c["content"] for c in again] == [c["content"] for c in chunks]


def test_chunker_sentence_mode_oversized_sentence_falls_back() -> None:
    """单句超长(无句界)退回定长截断(句中切分仅此形态)。"""
    text = "无句界超长内容" * 60  # 420 字符无任何句界字符
    chunks = chunk_document("超长.md", text, sentence_boundary=True)
    children = [str(c["content"]) for c in chunks if c["level"] == "CHILD"]
    assert len(children) >= 2
    assert all(len(c) <= 200 for c in children)
