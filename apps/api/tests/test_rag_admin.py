"""rag 管理端点全栈测试(009,安全规则类测试先行)。

真实 PG/Redis;AgentRagClient(embed/describe)类级打补丁为确定性替身。
安全面:manage 权限 403/租户上下文白名单/越租户删除同形 404/
datasource 校验同形 400/非法 schema_name 400/上传块数上限/审计落库/
覆盖优先级(自动不覆盖人工)/幂等 upsert。
"""
# pyright: reportPrivateUsage=false

import hashlib
import math
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, cast

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.config import get_settings
from kongming.rag.agent_callback import AgentRagClient
from tests.conftest import MakeUser

if TYPE_CHECKING:
    from kongming.rag.models import TableBlock
from tests.test_chat_query import _auth, _bind, _default_tenant_id, _login

RAG = "/system/rag"
EMB_DIM = 1536


@pytest.fixture(scope="module", autouse=True)
async def _biz() -> AsyncIterator[None]:  # pyright: ignore[reportUnusedFunction]
    """tenant_default 业务表(default 源内省的活数据)。"""
    from kongming.db import get_engine

    # 注:身份表(含租户)由 conftest._clean 用例级 TRUNCATE,租户/用户播种
    # 须在测试内经 _admin_token(内含 seed_all)完成——勿上提至模块 fixture。
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


def _vec_literal(seed_text: str) -> str:
    """确定性伪向量的 SQL 字面量形态(embedding <=> 比对用)。"""
    return "[" + ",".join(repr(v) for v in _fake_vector(seed_text)) + "]"


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
        self: AgentRagClient, block: "TableBlock", schema_name: str
    ) -> tuple[bool, object]:
        from kongming.rag.models import TableBlock

        described = cast("list[str]", state.get("described", []))
        state["described"] = described + [block.table_name]
        if not state["enrich"]:
            return False, block
        b = block
        return True, TableBlock(
            table_name=b.table_name,
            table_comment=f"AI:{b.table_name}",
            columns_comment={
                k: f"{v} 中文说明" for k, v in b.columns_comment.items()
            },
            sample_queries=["SELECT count(*) FROM x.y GROUP BY 1"],
            alias=str(state.get("alias") or ""),  # 037:可控 AI 提炼别名
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
) -> Response:
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


def _make_pdf() -> bytes:  # pyright: ignore[reportUnusedFunction]
    """代码构造最小合法 PDF(pypdf 空文档写出,单页文本)。"""
    import importlib

    from pypdf import PdfWriter

    importlib.import_module("pypdf._text_extraction.layout")  # 触发布局模块加载(无副作用)

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
    source: str = "MANUAL_UPLOAD", alias: str = "", schema: str = "tenant_default",
) -> str:
    """直插一条知识(arrange 独立于被测实现;037 可控 alias;042 可控 schema)。"""
    tid = (
        await session.execute(
            text(
                "INSERT INTO rag.table_descriptions"
                " (id, tenant_id, datasource_key, schema_name, table_name,"
                "  table_comment, columns_comment, sample_queries, source_type, alias)"
                " VALUES (gen_random_uuid(), :t, :d, :sn, :n,"
                "  'c', '{}', '[]', :st, NULLIF(:al, '')) RETURNING id"
            ),
            {"t": tenant_id, "d": ds, "sn": schema, "n": table, "st": source, "al": alias},
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


async def test_fetch_alias_overwrite_priority(
    client: AsyncClient, session: AsyncSession, fake_agent: dict[str, object]
) -> None:
    """037 alias 覆盖优先级:自动来源只补空不改非空;手编整行跳过保护。"""
    token = await _admin_token(client, session)
    tid = await _default_tenant_id(session)

    # ① 既有自动行 alias 空 → AI 提炼补齐
    await _seed_one(session, tid, "goods", source="AI_GENERATED", alias="")
    fake_agent["alias"] = "AI商品表"
    r1 = await client.post(
        f"{RAG}/fetch-metadata", json={"datasource_key": "default"}, headers=_auth(token)
    )
    assert r1.status_code == 200, r1.text
    alias1 = (
        await session.execute(
            text("SELECT alias FROM rag.table_descriptions WHERE table_name='goods'")
        )
    ).scalar_one()
    assert alias1 == "AI商品表"  # 空位补齐

    # ② 既有非空 alias(自动来源写入)→ 再次扫描不改写(即便新产出为空);
    #    且 tsv/embedding 以生效别名(=既有别名)同源重算(R1-S1:否则派生检索
    #    文本丢别名,「AI商品表」召回静默失效)
    fake_agent["alias"] = ""
    await client.post(
        f"{RAG}/fetch-metadata", json={"datasource_key": "default"}, headers=_auth(token)
    )
    from kongming.rag.models import build_embedding_text

    row2 = (
        await session.execute(
            text(
                "SELECT alias, tsv @@ plainto_tsquery('simple', 'AI商品表') AS tsv_hit,"
                " embedding <=> CAST(:vec AS vector) AS d_keep,"
                " embedding <=> CAST(:vec0 AS vector) AS d_drop"
                " FROM rag.table_descriptions WHERE table_name='goods'"
            ),
            {
                "vec": "[" + ",".join(repr(v) for v in _fake_vector(
                    build_embedding_text(
                        "goods", "AI:goods",
                        {"id": "integer 中文说明", "name": "text 中文说明",
                         "create_by": "uuid 中文说明"},
                        alias="AI商品表",
                    )
                )) + "]",
                "vec0": "[" + ",".join(repr(v) for v in _fake_vector(
                    build_embedding_text(
                        "goods", "AI:goods",
                        {"id": "integer 中文说明", "name": "text 中文说明",
                         "create_by": "uuid 中文说明"},
                        alias="",
                    )
                )) + "]",
            },
        )
    ).mappings().one()
    assert row2["alias"] == "AI商品表"  # 非空不被自动来源改写
    assert row2["tsv_hit"] is True      # tsv 以生效别名重算
    assert float(row2["d_keep"]) < 1e-6   # embedding=生效别名文本的向量
    assert float(row2["d_drop"]) > 0.1    # ≠ 丢别名文本的向量(同源直证)

    # ③ 手编(MANUAL_EDIT)alias:整行跳过保护,alias 不被 AI 触碰
    await session.execute(
        text(
            "UPDATE rag.table_descriptions SET source_type='MANUAL_EDIT',"
            " alias='手编商品表' WHERE table_name='goods'"
        )
    )
    await session.commit()
    fake_agent["alias"] = "AI改写名"
    r3 = await client.post(
        f"{RAG}/fetch-metadata", json={"datasource_key": "default"}, headers=_auth(token)
    )
    assert r3.json()["skipped"] >= 1
    row3 = (
        await session.execute(
            text(
                "SELECT alias, source_type FROM rag.table_descriptions"
                " WHERE table_name='goods'"
            )
        )
    ).mappings().one()
    assert row3["alias"] == "手编商品表"
    assert row3["source_type"] == "MANUAL_EDIT"


# ============ 042 扫描对账(登记=现实) ============


async def test_fetch_metadata_reconciles_stale_entries(
    client: AsyncClient, session: AsyncSession, fake_agent: dict[str, object]
) -> None:
    """042 对账:扫描集外自动来源孤儿删除(removed 计数+审计);人工孤儿保留。"""
    token = await _admin_token(client, session)
    tid = await _default_tenant_id(session)
    # 事故形态复刻:扫描范围外(tenant_sakila)的自动来源残留 + 人工资产
    stale_auto = await _seed_one(
        session, tid, "staff", schema="tenant_sakila", source="AI_GENERATED"
    )
    await _seed_one(
        session, tid, "legacy", schema="tenant_sakila", source="MANUAL_EDIT"
    )
    r = await client.post(
        f"{RAG}/fetch-metadata", json={"datasource_key": "default"}, headers=_auth(token)
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["removed"] >= 1  # 契约字段(042)
    gone = (
        await session.execute(
            text("SELECT count(*) FROM rag.table_descriptions WHERE id = :i"),
            {"i": stale_auto},
        )
    ).scalar_one()
    assert gone == 0  # 自动来源孤儿被对账删除
    kept = (
        await session.execute(
            text(
                "SELECT source_type FROM rag.table_descriptions"
                " WHERE tenant_id = :t AND schema_name = 'tenant_sakila'"
                " AND table_name = 'legacy'"
            ),
            {"t": tid},
        )
    ).scalar_one()
    assert kept == "MANUAL_EDIT"  # 扫描集外人工条目保留(交人工裁决)
    audit = (
        await session.execute(
            text(
                "SELECT detail FROM sys_oper_log WHERE action = 'rag.fetch_metadata'"
                " ORDER BY created_at DESC LIMIT 1"
            )
        )
    ).scalar_one()
    # 审计可溯;cast 仅定型,运行时不变
    assert cast("dict[str, int]", audit or {}).get("removed", 0) >= 1


async def test_fetch_metadata_excludes_views_from_scan(
    client: AsyncClient, session: AsyncSession, fake_agent: dict[str, object]
) -> None:
    """047:内省收录限基表——视图不入扫描集;存量视图登记由对账清偿。

    视图行扇出使聚合失真(film_list 实证)、样例 SQL 挤占召回 top-K 使
    维度表缺位(sakila_021 实证)——收录即引入歧义,基表可完整表达视图。
    """
    token = await _admin_token(client, session)
    tid = await _default_tenant_id(session)
    from kongming.db import get_engine

    async with get_engine().begin() as conn:
        await conn.execute(
            text("CREATE TABLE IF NOT EXISTS public.vw_base (id int primary key)")
        )
        await conn.execute(
            text(
                "CREATE OR REPLACE VIEW public.vw_view"
                " AS SELECT * FROM public.vw_base"
            )
        )
    # 存量视图登记(AI_GENERATED=自动来源)——旧收录形态产物
    view_row = await _seed_one(
        session, tid, "vw_view", schema="public", source="AI_GENERATED"
    )
    try:
        r = await client.post(
            f"{RAG}/fetch-metadata", json={"datasource_key": "default"}, headers=_auth(token)
        )
        assert r.status_code == 200, r.text
        gone = await session.execute(
            text("SELECT count(*) FROM rag.table_descriptions WHERE id = :i"),
            {"i": view_row},
        )
        assert gone.scalar_one() == 0  # 视图登记被对账清偿(扫描集只含基表)
        base_kept = await session.execute(
            text(
                "SELECT count(*) FROM rag.table_descriptions"
                " WHERE tenant_id = :t AND schema_name = 'public'"
                " AND table_name = 'vw_base'"
            ),
            {"t": tid},
        )
        assert base_kept.scalar_one() == 1  # 基表照常收录
    finally:
        async with get_engine().begin() as conn:
            await conn.execute(text("DROP VIEW IF EXISTS public.vw_view"))
            await conn.execute(text("DROP TABLE IF EXISTS public.vw_base"))


async def test_fetch_metadata_evicts_suggestion_cache(
    client: AsyncClient, session: AsyncSession, fake_agent: dict[str, object]
) -> None:
    """047:知识写路径级联失效建议缓存——存量建议不随治理自动失效会持续
    推荐已被排除的问题(保留表收编后 default 源建议撞墙 3/5 实证)。"""
    token = await _admin_token(client, session)
    tid = await _default_tenant_id(session)
    await session.execute(
        text(
            "INSERT INTO public.chat_suggestion"
            " (id, tenant_id, datasource_key, questions)"
            " VALUES (gen_random_uuid(), :t, 'default',"
            " CAST('[\"用户记忆中置信度大于0.8的记录共有多少条\"]' AS jsonb))"
        ),
        {"t": tid},
    )
    await session.commit()
    r = await client.post(
        f"{RAG}/fetch-metadata", json={"datasource_key": "default"}, headers=_auth(token)
    )
    assert r.status_code == 200, r.text
    n = await session.execute(
        text("SELECT count(*) FROM public.chat_suggestion WHERE datasource_key = 'default'")
    )
    assert n.scalar_one() == 0  # 该数据源建议缓存被同事务级联失效


async def test_fetch_metadata_cross_schema_same_name_no_shadow(
    client: AsyncClient, session: AsyncSession, fake_agent: dict[str, object]
) -> None:
    """042:优先级判定以 (schema, table) 二元定位——跨 schema 同名表不互遮。

    一元键形态下两行共占一个 dict 槽,tenant_default.goods(MANUAL_EDIT)
    的整行保护会被 public.goods 的自动来源身份误顶替(覆盖或漏 skip 视
    行序而定)——二元化后各自归位。
    """
    token = await _admin_token(client, session)
    tid = await _default_tenant_id(session)
    await _seed_one(session, tid, "goods", schema="tenant_default", source="MANUAL_EDIT")
    # public.goods 须真实存在(对账 keep=真实扫描集——知识行无表支撑会被
    # 当孤儿删掉,那是对账语义而非遮蔽);建真实表使 (public, goods) 入 keep
    from kongming.db import get_engine

    async with get_engine().begin() as conn:
        await conn.execute(
            text("CREATE TABLE public.goods (id int primary key, name text)")
        )
    try:
        await session.execute(
            text(
                "INSERT INTO rag.table_descriptions"
                " (id, tenant_id, datasource_key, schema_name, table_name,"
                "  table_comment, columns_comment, sample_queries, source_type)"
                " VALUES (gen_random_uuid(), :t, 'default', 'public', 'goods',"
                "  'OLD', '{}', '[]', 'AI_GENERATED')"
            ),
            {"t": tid},
        )
        await session.commit()
        r = await client.post(
            f"{RAG}/fetch-metadata",
            json={"datasource_key": "default"}, headers=_auth(token),
        )
        assert r.status_code == 200, r.text
    finally:
        async with get_engine().begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS public.goods"))
    rows = (
        await session.execute(
            text(
                "SELECT schema_name, table_comment, source_type"
                " FROM rag.table_descriptions WHERE table_name = 'goods'"
                " ORDER BY schema_name"
            )
        )
    ).all()
    by_schema = {r.schema_name: r for r in rows}
    assert by_schema["tenant_default"].source_type == "MANUAL_EDIT"  # 整行保护不破
    assert by_schema["tenant_default"].table_comment == "c"
    assert by_schema["public"].source_type == "AI_GENERATED"  # 扫描集内正常更新
    assert by_schema["public"].table_comment == "AI:goods"    # 未被误 skip


# ============ 037 表说明编辑(alias 与 table_comment 同面) ============


async def test_edit_table_alias_input_validation(
    client: AsyncClient, session: AsyncSession, fake_agent: dict[str, object]
) -> None:
    """R1-F1:别名禁括号/竖线/换行(防伪表名注入与行结构破坏);strip 落库。"""
    token = await _admin_token(client, session)
    tid = await _default_tenant_id(session)
    row_id = await _seed_one(session, tid, "goods")
    # 括号:可伪造「fake.table(」形态破坏 023 表名提取——拒收
    r1 = await client.put(
        f"{RAG}/tables/{row_id}",
        json={"table_comment": "c", "alias": "a\nfake.table(q)"},
        headers=_auth(token),
    )
    assert r1.status_code == 422
    r2 = await client.put(
        f"{RAG}/tables/{row_id}",
        json={"table_comment": "c", "alias": "商品(表)"},
        headers=_auth(token),
    )
    assert r2.status_code == 422
    # 换行破坏 schema_hint 行结构——拒收
    r3 = await client.put(
        f"{RAG}/tables/{row_id}",
        json={"table_comment": "c", "alias": "商品\n表"},
        headers=_auth(token),
    )
    assert r3.status_code == 422
    # 合法值 strip 后落库
    r4 = await client.put(
        f"{RAG}/tables/{row_id}",
        json={"table_comment": "  商品信息  ", "alias": "  商品表  "},
        headers=_auth(token),
    )
    assert r4.status_code == 200
    assert r4.json()["alias"] == "商品表"
    assert r4.json()["table_comment"] == "商品信息"


async def test_upload_preserves_existing_alias(
    client: AsyncClient, session: AsyncSession, fake_agent: dict[str, object]
) -> None:
    """R1-B2:重复上传同表文档(parser 无别名语法)不清既有非空 alias。"""
    token = await _admin_token(client, session)
    tid = await _default_tenant_id(session)
    await _seed_one(session, tid, "goods", source="MANUAL_UPLOAD", alias="手编商品表")
    r = await _upload(client, token, content=_md("goods"))
    assert r.status_code == 200, r.text
    row = (
        await session.execute(
            text(
                "SELECT alias, table_comment,"
                " embedding <=> CAST(:vk AS vector) AS d_keep,"
                " embedding <=> CAST(:vd AS vector) AS d_drop"
                " FROM rag.table_descriptions WHERE table_name='goods'"
            ),
            {
                "vk": _vec_literal("goods 手编商品表 goods说明 id 主键"),
                "vd": _vec_literal("goods goods说明 id 主键"),
            },
        )
    ).mappings().one()
    assert row["alias"] == "手编商品表"  # alias 只补空不改非空
    assert row["table_comment"] != "c"   # 说明照常显式覆盖更新
    # 审查循环 F1:embedding 须以生效别名(=既有别名)同源重算——上传的
    # parser 无别名语法,曾用无别名文本的向量覆盖(别名向量召回静默退化)
    assert float(row["d_keep"]) < 1e-6
    assert float(row["d_drop"]) > 0.1


async def test_edit_table_manual_edit_success(
    client: AsyncClient, session: AsyncSession, fake_agent: dict[str, object]
) -> None:
    """编辑 alias+table_comment:落库 MANUAL_EDIT;响应回传;tsv 拼别名;向量重算。"""
    token = await _admin_token(client, session)
    tid = await _default_tenant_id(session)
    row_id = await _seed_one(session, tid, "goods")
    r = await client.put(
        f"{RAG}/tables/{row_id}",
        json={"table_comment": "商品信息表", "alias": "商品表"},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["alias"] == "商品表"
    assert body["table_comment"] == "商品信息表"
    assert body["source_type"] == "MANUAL_EDIT"
    row = (
        await session.execute(
            text(
                "SELECT alias, source_type, embedding IS NOT NULL AS has_vec,"
                " tsv @@ plainto_tsquery('simple', '商品表') AS alias_hit"
                " FROM rag.table_descriptions WHERE id = :i"
            ),
            {"i": row_id},
        )
    ).mappings().one()
    assert row["alias"] == "商品表"
    assert row["source_type"] == "MANUAL_EDIT"
    assert row["has_vec"] is True  # 编辑后向量重算(曾误清空,实施期抓回)
    assert row["alias_hit"] is True  # tsv 拼入别名:全文路「商品表」命中
    assert (
        await session.execute(
            text("SELECT count(*) FROM public.sys_oper_log WHERE action='rag.edit_table'")
        )
    ).scalar_one() >= 1


async def test_edit_table_empty_alias_clears(
    client: AsyncClient, session: AsyncSession, fake_agent: dict[str, object]
) -> None:
    """alias 传空串=清除别名(允许);表说明不受影响。"""
    token = await _admin_token(client, session)
    tid = await _default_tenant_id(session)
    row_id = await _seed_one(session, tid, "goods", alias="商品表")
    r = await client.put(
        f"{RAG}/tables/{row_id}",
        json={"table_comment": "商品信息", "alias": ""},
        headers=_auth(token),
    )
    assert r.status_code == 200
    assert r.json()["alias"] == ""
    assert (
        await session.execute(
            text("SELECT COALESCE(alias, '') FROM rag.table_descriptions WHERE id = :i"),
            {"i": row_id},
        )
    ).scalar_one() == ""


async def test_edit_table_preserves_vector_when_embed_fails(
    client: AsyncClient,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    fake_agent: dict[str, object],
) -> None:
    """embed 失败:保留旧向量不清空(清向量会使该表退出向量召回)。"""
    from kongming.rag.ports import EmbeddingError

    token = await _admin_token(client, session)
    tid = await _default_tenant_id(session)
    row_id = await _seed_one(session, tid, "goods")
    r1 = await client.put(  # 第一轮:替身 embed 正常,向量落库
        f"{RAG}/tables/{row_id}",
        json={"table_comment": "商品信息", "alias": "商品表"},
        headers=_auth(token),
    )
    assert r1.status_code == 200

    async def boom_embed(self: AgentRagClient, texts: list[str]) -> list[list[float]]:
        raise EmbeddingError("embed 故障")

    monkeypatch.setattr(AgentRagClient, "embed", boom_embed)
    r2 = await client.put(  # 第二轮:embed 故障
        f"{RAG}/tables/{row_id}",
        json={"table_comment": "商品信息2", "alias": "货品表"},
        headers=_auth(token),
    )
    assert r2.status_code == 200
    has_vec = (
        await session.execute(
            text("SELECT embedding IS NOT NULL FROM rag.table_descriptions WHERE id = :i"),
            {"i": row_id},
        )
    ).scalar_one()
    assert has_vec is True  # 旧向量保留
    assert r2.json()["alias"] == "货品表"


async def test_edit_table_cross_tenant_404(
    client: AsyncClient, session: AsyncSession, fake_agent: dict[str, object]
) -> None:
    """越租户编辑:同形 404(与删除同口径,防探测)。"""
    from tests.test_datasource_admin import _mk_tenant

    token = await _admin_token(client, session)
    other = await _mk_tenant(session, "alias-other")
    row_id = await _seed_one(session, other, "goods")
    r = await client.put(
        f"{RAG}/tables/{row_id}",
        json={"table_comment": "x", "alias": "y"},
        headers=_auth(token),
    )
    assert r.status_code == 404


async def test_edit_table_no_perm_403(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser,
    fake_agent: dict[str, object],
) -> None:
    token = await _noperm_user(client, session, make_user)
    tid = await _default_tenant_id(session)
    row_id = await _seed_one(session, tid, "goods")
    r = await client.put(
        f"{RAG}/tables/{row_id}",
        json={"table_comment": "x", "alias": "y"},
        headers=_auth(token),
    )
    assert r.status_code == 403


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


# ============ 040 可扫描 schema 枚举与上传存在性闸 ============


async def _mk_ds_row(
    session: AsyncSession, key: str, tenant_id: str, *, default_schema: str
) -> None:
    """040 测试用动态数据源行(default_schema 可指定;密码同 _mk_ds 口径)。

    先删后插:共享测试库并发会话/重跑下幂等(040 实测教训)。
    """
    from kongming.config import get_settings
    from kongming.datasource.cipher import get_cipher

    enc = get_cipher(get_settings().datasource_encrypt_key).encrypt("kongming")
    await session.execute(
        text("DELETE FROM public.sys_datasource WHERE datasource_key = :k"), {"k": key}
    )
    await session.execute(
        text(
            "INSERT INTO public.sys_datasource"
            " (id, datasource_key, name, tenant_id, host, port, database, username,"
            "  password_enc, default_schema, status)"
            " VALUES (gen_random_uuid(), :k, :k, :t, '127.0.0.1', 5432, 'kongming_test',"
            "         'kongming', :pe, :ds, 'ACTIVE')"
        ),
        {"k": key, "t": tenant_id, "pe": enc, "ds": default_schema},
    )
    await session.commit()


async def _mk_tenant_idem(session: AsyncSession, key: str) -> str:
    """先删后建的幂等租户(共享库重跑护栏;返回 tenant_id)。"""
    await session.execute(
        text("DELETE FROM public.sys_tenant WHERE tenant_key = :k"), {"k": key}
    )
    tid = (
        await session.execute(
            text(
                "INSERT INTO public.sys_tenant (id, tenant_key, tenant_name, status)"
                " VALUES (gen_random_uuid(), :k, :k, 'ACTIVE') RETURNING id"
            ),
            {"k": key},
        )
    ).scalar_one()
    await session.commit()
    return str(tid)


async def test_schemas_default_source(
    client: AsyncClient, session: AsyncSession, fake_agent: dict[str, object]
) -> None:
    """default 源:路由约定 schema 集合 [tenant_<key>, public](与内省同源)。"""
    token = await _admin_token(client, session)
    r = await client.get(
        f"{RAG}/schemas", params={"datasource_key": "default"}, headers=_auth(token)
    )
    assert r.status_code == 200, r.text
    assert r.json() == {
        "datasource_key": "default",
        "schemas": ["tenant_default", "public"],
    }


async def test_schemas_dynamic_source(
    client: AsyncClient, session: AsyncSession, fake_agent: dict[str, object]
) -> None:
    """动态源:resolver 校验后取 [default_schema] 单元素集合。"""
    token = await _admin_token(client, session)  # 先播种(_clean 每用例清身份表)
    await _mk_ds_row(
        session, "dynsch040", await _default_tenant_id(session), default_schema="sales"
    )
    r = await client.get(
        f"{RAG}/schemas", params={"datasource_key": "dynsch040"}, headers=_auth(token)
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"datasource_key": "dynsch040", "schemas": ["sales"]}


async def test_schemas_unusable_same_shape_400(
    client: AsyncClient, session: AsyncSession, fake_agent: dict[str, object]
) -> None:
    """不存在/越租户数据源:同形 400(不区分,防 key 探测)。"""

    token = await _admin_token(client, session)
    r = await client.get(
        f"{RAG}/schemas", params={"datasource_key": "ghost_ds"}, headers=_auth(token)
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "DATASOURCE_INVALID"

    other_tid = await _mk_tenant_idem(session, "sch040-other")
    await _mk_ds_row(session, "othersch040", other_tid, default_schema="sales")
    r = await client.get(
        f"{RAG}/schemas", params={"datasource_key": "othersch040"}, headers=_auth(token)
    )
    assert r.status_code == 400  # 越租户同形 400,非 404


async def test_schemas_requires_perm(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    token = await _noperm_user(client, session, make_user)
    r = await client.get(
        f"{RAG}/schemas", params={"datasource_key": "default"}, headers=_auth(token)
    )
    assert r.status_code == 403


async def test_upload_schema_not_in_set_400(
    client: AsyncClient, session: AsyncSession, fake_agent: dict[str, object]
) -> None:
    """语法合法但不在可扫描集合:400 SCHEMA_NOT_ALLOWED,不落库(写边界闸)。"""
    token = await _admin_token(client, session)
    r = await _upload(
        client, token, content=_md("film040x"), schema_name="tenant_others"
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "SCHEMA_NOT_ALLOWED"
    n = (
        await session.execute(
            text(
                "SELECT count(*) FROM rag.table_descriptions"
                " WHERE schema_name = 'tenant_others'"
            )
        )
    ).scalar_one()
    assert n == 0


async def test_upload_schema_public_member_ok(
    client: AsyncClient, session: AsyncSession, fake_agent: dict[str, object]
) -> None:
    """集合内合法成员(public)照常上传。"""
    token = await _admin_token(client, session)
    r = await _upload(client, token, content=_md("film040ok"), schema_name="public")
    assert r.status_code == 200, r.text
    assert r.json()["tables"] == ["film040ok"]
