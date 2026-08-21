"""rag 管理端点全栈测试(009,安全规则类测试先行)。

真实 PG/Redis;AgentRagClient(embed/describe)类级打补丁为确定性替身。
安全面:manage 权限 403/租户上下文白名单/越租户删除同形 404/
datasource 校验同形 400/非法 schema_name 400/上传块数上限/审计落库/
覆盖优先级(自动不覆盖人工)/幂等 upsert。
"""

import hashlib
import math
from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.config import get_settings
from kongming.rag.agent_callback import AgentRagClient
from tests.conftest import MakeUser
from tests.test_chat_query import _auth, _bind, _default_tenant_id, _login

RAG = "/system/rag"
EMB_DIM = 1536


@pytest.fixture(scope="module", autouse=True)
async def _biz() -> AsyncIterator[None]:
    """tenant_default 业务表(default 源内省的活数据)。"""
    from kongming.db import get_engine

    async with get_engine().begin() as conn:
        await conn.execute(
            text("CREATE SCHEMA IF NOT EXISTS tenant_default")
        )
        await conn.execute(
            text("CREATE TABLE IF NOT EXISTS tenant_default.goods"
                 " (id int primary key, name text, create_by uuid)")
        )
    yield
    async with get_engine().begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS tenant_default.goods"))
        await conn.execute(text("DROP SCHEMA IF EXISTS tenant_default CASCADE"))


def _fake_vector(seed_text: str) -> list[float]:
    """确定性伪向量(同文本同向量——向量召回可预期;维度=列宽 1536)。"""
    digest = hashlib.sha256(seed_text.encode()).digest()
    vals = [((digest[i % len(digest)] << 8 | digest[(i + 1) % len(digest)]) % 1000) / 1000.0
            for i in range(EMB_DIM)]
    norm = math.sqrt(sum(v * v for v in vals)) or 1.0
    return [v / norm for v in vals]


@pytest.fixture
def fake_agent(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """AgentRagClient 类级替身:embed=确定性向量;describe=可控脚本。"""
    state: dict[str, object] = {"enrich": True, "described": []}

    async def fake_embed(self: AgentRagClient, texts: list[str]) -> list[list[float]]:
        return [_fake_vector(t) for t in texts]

    async def fake_describe(
        self: AgentRagClient, block: object, schema_name: str
    ) -> tuple[bool, object]:
        from kongming.rag.models import TableBlock

        state["described"] = state.get("described", []) + [block.table_name]  # type: ignore[operator]
        if not state["enrich"]:
            return False, block
        b = block if isinstance(block, TableBlock) else block
        return True, TableBlock(
            table_name=b.table_name,  # type: ignore[attr-defined]
            table_comment=f"AI:{b.table_name}",  # type: ignore[attr-defined]
            columns_comment={
                k: f"{v} 中文说明" for k, v in b.columns_comment.items()  # type: ignore[attr-defined]
            },
            sample_queries=["SELECT count(*) FROM x.y GROUP BY 1"],
        )

    monkeypatch.setattr(AgentRagClient, "embed", fake_embed)
    monkeypatch.setattr(AgentRagClient, "describe", fake_describe)
    return state


async def _admin_token(client: AsyncClient, session: AsyncSession) -> str:
    await seed_all(session, get_settings())
    return await _login(client, "admin", "admin123")  # SUPER_ADMIN 自动覆盖全部权限点


async def _noperm_user(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> str:
    """仅 chat:execute(DEVELOPER)——无 system:rag:manage。"""
    await seed_all(session, get_settings())
    uid = await make_user("ragless")
    rid = (
        await session.execute(text("SELECT id FROM public.sys_role WHERE role_key='DEVELOPER'"))
    ).scalar_one()
    await _bind(session, "sys_user_role", uid, str(rid))
    await _bind(session, "sys_user_tenant", uid, await _default_tenant_id(session))
    return await _login(client, "ragless")


def _md(*tables: str) -> bytes:
    parts = [f"## {t}\n说明:{t}说明\n字段:id 主键\n" for t in tables]
    return "\n".join(parts).encode()


async def _upload(
    client: AsyncClient,
    token: str,
    *,
    content: bytes,
    datasource_key: str = "default",
    schema_name: str = "tenant_default",
    tenant: str | None = None,
) -> object:
    headers = _auth(token)
    if tenant is not None:
        headers = {**headers, "X-Tenant-Id": tenant}
    return await client.post(
        f"{RAG}/documents/upload",
        files={"file": ("tables.md", content, "text/markdown")},
        data={"datasource_key": datasource_key, "schema_name": schema_name},
        headers=headers,
    )


# ============ 上传 ============


async def test_upload_success_and_audit(
    client: AsyncClient, session: AsyncSession, fake_agent: dict[str, object]
) -> None:
    token = await _admin_token(client, session)
    r = await _upload(client, token, content=_md("customer", "film"))
    assert r.status_code == 200, r.text
    assert r.json()["tables"] == ["customer", "film"]
    row = (
        await session.execute(
            text(
                "SELECT table_comment, source_type, embedding IS NOT NULL AS has_vec"
                " FROM rag.table_descriptions WHERE table_name='customer'"
            )
        )
    ).mappings().one()
    assert row["table_comment"] == "customer说明"
    assert row["source_type"] == "MANUAL_UPLOAD"
    assert row["has_vec"] is True
    audit = (
        await session.execute(
            text("SELECT count(*) FROM public.sys_oper_log WHERE action='rag.upload'")
        )
    ).scalar_one()
    assert audit == 1


async def test_upload_idempotent_upsert(
    client: AsyncClient, session: AsyncSession, fake_agent: dict[str, object]
) -> None:
    token = await _admin_token(client, session)
    await _upload(client, token, content=_md("goods"))
    r = await _upload(client, token, content=_md("goods"))
    assert r.status_code == 200
    n = (
        await session.execute(
            text("SELECT count(*) FROM rag.table_descriptions WHERE table_name='goods'")
        )
    ).scalar_one()
    assert n == 1  # 幂等:不产生重复行


async def test_upload_case_fold_same_table(
    client: AsyncClient, session: AsyncSession, fake_agent: dict[str, object]
) -> None:
    """`## Customer` 与 `## customer` 折叠为同一表(第二次 UPDATE)。"""
    token = await _admin_token(client, session)
    await _upload(client, token, content="## Customer\n说明:v1".encode())
    await _upload(client, token, content="## customer\n说明:v2".encode())
    rows = (
        await session.execute(
            text(
                "SELECT table_comment FROM rag.table_descriptions ORDER BY table_name"
            )
        )
    ).scalars().all()
    assert rows == ["v2"]  # 单行,后者覆盖


async def test_upload_no_perm_403(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser, fake_agent: dict[str, object]
) -> None:
    token = await _noperm_user(client, session, make_user)
    r = await _upload(client, token, content=_md("t1"))
    assert r.status_code == 403
    assert (
        await session.execute(text("SELECT count(*) FROM rag.table_descriptions"))
    ).scalar_one() == 0


async def test_upload_bad_datasource_same_shape_400(
    client: AsyncClient, session: AsyncSession, fake_agent: dict[str, object]
) -> None:
    """datasource 不存在/禁用/越属:同形 400(不区分,防探测)。"""
    token = await _admin_token(client, session)
    r = await _upload(client, token, content=_md("t1"), datasource_key="ghost_ds")
    assert r.status_code == 400
    assert r.json()["detail"] == "DATASOURCE_INVALID"


async def test_upload_invalid_schema_name_400(
    client: AsyncClient, session: AsyncSession, fake_agent: dict[str, object]
) -> None:
    token = await _admin_token(client, session)
    r = await _upload(client, token, content=_md("t1"), schema_name="Bad Schema")
    assert r.status_code == 400


async def test_upload_too_many_blocks_400(
    client: AsyncClient, session: AsyncSession, fake_agent: dict[str, object]
) -> None:
    token = await _admin_token(client, session)
    r = await _upload(client, token, content=_md(*[f"t{i}" for i in range(501)]))
    assert r.status_code == 400
    assert "表块数超上限" in r.json()["detail"]


async def test_upload_oversize_400(
    client: AsyncClient, session: AsyncSession, fake_agent: dict[str, object]
) -> None:
    token = await _admin_token(client, session)
    big = b"## x\n" + b"a" * (1024 * 1024 + 10)
    r = await _upload(client, token, content=big)
    assert r.status_code == 400
    assert r.json()["detail"] == "DOCUMENT_TOO_LARGE"


async def test_upload_no_table_blocks_400(
    client: AsyncClient, session: AsyncSession, fake_agent: dict[str, object]
) -> None:
    token = await _admin_token(client, session)
    r = await _upload(client, token, content="# 只有H1\n没有二级标题".encode())
    assert r.status_code == 400
    assert "##" in r.json()["detail"]


def _make_pdf() -> bytes:
    """代码构造最小合法 PDF(pypdf 空文档写出,单页文本)。"""
    from pypdf import PdfWriter
    from pypdf._text_extraction import layout  # noqa: F401  # 触发布局模块加载(无副作用)

    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    import io

    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _make_docx() -> bytes:
    """代码构造最小 docx(python-docx 空文档+段落)。"""
    import io

    import docx

    d = docx.Document()
    d.add_paragraph("## goods")
    d.add_paragraph("说明:商品表")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


async def test_upload_docx_parsed_via_markdown_chain(
    client: AsyncClient, session: AsyncSession, fake_agent: dict[str, object]
) -> None:
    """>docx 解析为纯文本后复用 `## 表名` 切块链路(rag spec PDF/Word scenario)。"""
    token = await _admin_token(client, session)
    r = await client.post(
        f"{RAG}/documents/upload",
        files={
            "file": (
                "tables.docx",
                _make_docx(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        data={"datasource_key": "default", "schema_name": "tenant_default"},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["tables"] == ["goods"]
    n = (
        await session.execute(
            text("SELECT count(*) FROM rag.table_descriptions WHERE table_name='goods'")
        )
    ).scalar_one()
    assert n == 1


async def test_upload_corrupt_pdf_400(
    client: AsyncClient, session: AsyncSession, fake_agent: dict[str, object]
) -> None:
    """损坏/非 PDF 字节伪装 .pdf:解析失败 400(不写入知识库)。"""
    token = await _admin_token(client, session)
    r = await client.post(
        f"{RAG}/documents/upload",
        files={"file": ("x.pdf", b"not a pdf at all", "application/pdf")},
        data={"datasource_key": "default", "schema_name": "tenant_default"},
        headers=_auth(token),
    )
    assert r.status_code == 400
    n = (await session.execute(text("SELECT count(*) FROM rag.table_descriptions"))).scalar_one()
    assert n == 0


async def test_upload_binary_oversize_400(
    client: AsyncClient, session: AsyncSession, fake_agent: dict[str, object]
) -> None:
    """伪 .pdf 超 10MB 分档上限:DOCUMENT_TOO_LARGE(读 limit+1 即停,不解析)。"""
    token = await _admin_token(client, session)
    r = await client.post(
        f"{RAG}/documents/upload",
        files={
            "file": ("big.pdf", b"%PDF-1.4" + b"0" * (10 * 1024 * 1024 + 10), "application/pdf")
        },
        data={"datasource_key": "default", "schema_name": "tenant_default"},
        headers=_auth(token),
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "DOCUMENT_TOO_LARGE"


async def test_upload_table_name_too_long_400(
    client: AsyncClient, session: AsyncSession, fake_agent: dict[str, object]
) -> None:
    """表名超 128 列宽:400(防 DB 异常 500;复审 S2)。"""
    token = await _admin_token(client, session)
    long_name = "t" * 129
    md = ("## " + long_name + chr(10) + "说明:x").encode()
    r = await _upload(client, token, content=md)
    assert r.status_code == 400
    assert "表名超长" in r.json()["detail"]


async def test_upload_unsupported_type_400(
    client: AsyncClient, session: AsyncSession, fake_agent: dict[str, object]
) -> None:
    token = await _admin_token(client, session)
    r = await client.post(
        f"{RAG}/documents/upload",
        files={"file": ("x.exe", b"MZ", "application/octet-stream")},
        data={"datasource_key": "default", "schema_name": "tenant_default"},
        headers=_auth(token),
    )
    assert r.status_code == 400


# ============ 列表/删除 ============


async def _seed_one(
    session: AsyncSession, tenant_id: str, table: str, *, ds: str = "default",
    source: str = "MANUAL_UPLOAD",
) -> str:
    """直插一条知识(arrange 独立于被测实现)。"""
    tid = (
        await session.execute(
            text(
                "INSERT INTO rag.table_descriptions"
                " (id, tenant_id, datasource_key, schema_name, table_name,"
                "  table_comment, columns_comment, sample_queries, source_type)"
                " VALUES (gen_random_uuid(), :t, :d, 'tenant_default', :n,"
                "  'c', '{}', '[]', :st) RETURNING id"
            ),
            {"t": tenant_id, "d": ds, "n": table, "st": source},
        )
    ).scalar_one()
    await session.commit()
    return str(tid)


async def test_list_scoped_to_tenant(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser, fake_agent: dict[str, object]
) -> None:
    token = await _admin_token(client, session)
    from tests.test_datasource_admin import _mk_tenant

    other = await _mk_tenant(session, "othert")
    mine = await _default_tenant_id(session)
    await _seed_one(session, mine, "mine_t")
    await _seed_one(session, other, "other_t")
    r = await client.get(f"{RAG}/tables", headers=_auth(token))
    assert r.status_code == 200
    names = [i["table_name"] for i in r.json()["items"]]
    assert names == ["mine_t"]  # 租户隔离:他租户条目不可见
    # 向量/全文列不在响应
    assert "embedding" not in r.json()["items"][0]


async def test_list_requires_perm(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    token = await _noperm_user(client, session, make_user)
    r = await client.get(f"{RAG}/tables", headers=_auth(token))
    assert r.status_code == 403


async def test_delete_cross_tenant_404(
    client: AsyncClient, session: AsyncSession, fake_agent: dict[str, object]
) -> None:
    token = await _admin_token(client, session)
    from tests.test_datasource_admin import _mk_tenant

    other = await _mk_tenant(session, "othert")
    tid = await _seed_one(session, other, "other_t")
    r = await client.delete(f"{RAG}/tables/{tid}", headers=_auth(token))
    assert r.status_code == 404  # 同形 404(不暴露他租户存在性)
    n = (
        await session.execute(text("SELECT count(*) FROM rag.table_descriptions"))
    ).scalar_one()
    assert n == 1  # 未删


async def test_delete_success_with_audit(
    client: AsyncClient, session: AsyncSession, fake_agent: dict[str, object]
) -> None:
    token = await _admin_token(client, session)
    tid = await _seed_one(session, await _default_tenant_id(session), "gone")
    r = await client.delete(f"{RAG}/tables/{tid}", headers=_auth(token))
    assert r.status_code == 204
    audit = (
        await session.execute(
            text("SELECT count(*) FROM public.sys_oper_log WHERE action='rag.delete'")
        )
    ).scalar_one()
    assert audit == 1


# ============ fetch-metadata / reindex ============


async def test_fetch_metadata_default_enriched(
    client: AsyncClient, session: AsyncSession, fake_agent: dict[str, object]
) -> None:
    """default 源:内省 tenant_default 业务表(goods),润色成功→AI_GENERATED。"""
    token = await _admin_token(client, session)
    r = await client.post(
        f"{RAG}/fetch-metadata", json={"datasource_key": "default"}, headers=_auth(token)
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["found"] >= 1  # goods 表在(tests conftest 建过)
    assert body["enriched"] == body["found"]  # 替身恒润色成功
    row = (
        await session.execute(
            text(
                "SELECT source_type, table_comment FROM rag.table_descriptions"
                " WHERE table_name='goods'"
            )
        )
    ).mappings().one()
    assert row["source_type"] == "AI_GENERATED"
    assert row["table_comment"].startswith("AI:")


async def test_fetch_metadata_llm_fail_degrades(
    client: AsyncClient, session: AsyncSession, fake_agent: dict[str, object]
) -> None:
    """润色失败(enrich=False):降级纯结构 INTROSPECTION,整批不失败。"""
    fake_agent["enrich"] = False
    token = await _admin_token(client, session)
    r = await client.post(
        f"{RAG}/fetch-metadata", json={"datasource_key": "default"}, headers=_auth(token)
    )
    assert r.status_code == 200
    body = r.json()
    assert body["found"] >= 1 and body["enriched"] == 0
    row = (
        await session.execute(
            text(
                "SELECT source_type FROM rag.table_descriptions WHERE table_name='goods'"
            )
        )
    ).scalar_one()
    assert row == "INTROSPECTION"


async def test_fetch_does_not_overwrite_manual(
    client: AsyncClient, session: AsyncSession, fake_agent: dict[str, object]
) -> None:
    """覆盖优先级:AI_GENERATED 不覆盖既有 MANUAL_UPLOAD(跳过计 skipped)。"""
    token = await _admin_token(client, session)
    tid = await _default_tenant_id(session)
    await _seed_one(session, tid, "goods", source="MANUAL_UPLOAD")
    r = await client.post(
        f"{RAG}/fetch-metadata", json={"datasource_key": "default"}, headers=_auth(token)
    )
    body = r.json()
    assert body["skipped"] >= 1  # goods 被保护
    row = (
        await session.execute(
            text("SELECT source_type, table_comment FROM rag.table_descriptions"
                 " WHERE table_name='goods'")
        )
    ).mappings().one()
    assert row["source_type"] == "MANUAL_UPLOAD"
    assert row["table_comment"] == "c"  # 原内容未动


async def test_fetch_no_perm_403(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser, fake_agent: dict[str, object]
) -> None:
    token = await _noperm_user(client, session, make_user)
    r = await client.post(
        f"{RAG}/fetch-metadata", json={"datasource_key": "default"}, headers=_auth(token)
    )
    assert r.status_code == 403


async def test_fetch_invalid_datasource_400(
    client: AsyncClient, session: AsyncSession, fake_agent: dict[str, object]
) -> None:
    token = await _admin_token(client, session)
    r = await client.post(
        f"{RAG}/fetch-metadata", json={"datasource_key": "ghost"}, headers=_auth(token)
    )
    assert r.status_code == 400


async def test_reindex_clears_and_rebuilds_idempotent(
    client: AsyncClient, session: AsyncSession, fake_agent: dict[str, object]
) -> None:
    """reindex:清空人工条目重建(显式语义);连续两次结果一致。"""
    token = await _admin_token(client, session)
    tid = await _default_tenant_id(session)
    await _seed_one(session, tid, "manual_keep", source="MANUAL_EDIT")  # 人工也会被清
    r1 = await client.post(
        f"{RAG}/reindex", json={"datasource_key": "default"}, headers=_auth(token)
    )
    assert r1.status_code == 200, r1.text
    first = r1.json()
    assert first["deleted"] >= 1 and first["rebuilt"] == first["enriched"]
    # 人工条目已被清空重建为自动来源
    assert (
        await session.execute(
            text(
                "SELECT count(*) FROM rag.table_descriptions"
                " WHERE table_name='manual_keep'"
            )
        )
    ).scalar_one() == 0
    r2 = await client.post(
        f"{RAG}/reindex", json={"datasource_key": "default"}, headers=_auth(token)
    )
    second = r2.json()
    assert second["rebuilt"] == first["rebuilt"]  # 幂等:确定性重建
    audit = (
        await session.execute(
            text("SELECT count(*) FROM public.sys_oper_log WHERE action='rag.reindex'")
        )
    ).scalar_one()
    assert audit == 2


async def test_fetch_metadata_column_comment_into_pipeline(
    client: AsyncClient, session: AsyncSession, fake_agent: dict[str, object]
) -> None:
    """014 挂账⑦:数据库列注释(col_description)进润色输入——有注释列以注释为
    说明(经替身「中文说明」后缀形态落库),无注释列保持英文类型形态。"""
    await session.execute(
        text("COMMENT ON COLUMN tenant_default.goods.name IS '商品名称(人工注释)'")
    )
    await session.commit()
    token = await _admin_token(client, session)
    r = await client.post(
        f"{RAG}/fetch-metadata", json={"datasource_key": "default"}, headers=_auth(token)
    )
    assert r.status_code == 200, r.text
    cols = (
        await session.execute(
            text(
                "SELECT columns_comment FROM rag.table_descriptions WHERE table_name='goods'"
            )
        )
    ).scalar_one()
    # 有注释列:DB 注释进润色输入,替身加「中文说明」后缀
    assert "商品名称(人工注释)" in cols["name"]
    # 无注释列:保持类型形态进润色(替身同样加后缀)
    assert "中文说明" in cols["create_by"]


async def test_reindex_preserving_existing_on_prefetch_failure(
    client: AsyncClient, session: AsyncSession, fake_agent: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """014 挂账⑪:先润色后删写——内省/润色阶段(前置)失败不进入删写事务,
    既有条目保留(旧形态 delete 先行,失败即丢条目)。"""
    token = await _admin_token(client, session)
    r = await client.post(
        f"{RAG}/fetch-metadata", json={"datasource_key": "default"}, headers=_auth(token)
    )
    assert r.status_code == 200
    before = (
        await session.execute(text("SELECT count(*) FROM rag.table_descriptions"))
    ).scalar_one()
    assert before >= 1

    from kongming.rag.repository import RagRepository

    async def _boom(self: RagRepository, **kw: object) -> list[object]:
        raise LookupError("datasource gone")

    monkeypatch.setattr(RagRepository, "introspect_all", _boom)
    r = await client.post(
        f"{RAG}/reindex", json={"datasource_key": "default"}, headers=_auth(token)
    )
    assert r.status_code == 400  # DatasourceInvalid 同形
    after = (
        await session.execute(text("SELECT count(*) FROM rag.table_descriptions"))
    ).scalar_one()
    assert after == before  # 既有条目零损失(删写事务从未进入)
