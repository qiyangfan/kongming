"""024 document-rag 测试:分块/上传幂等/检索隔离/知识分流两路。"""
# pyright: reportPrivateUsage=false

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.config import get_settings
from tests.conftest import MakeUser
from tests.test_toolcall import _bind, _login

INTERNAL = {"X-Internal-Token": "test-internal-token"}

DOC = """# 退货政策

## 退货条件

自签收之日起 7 天内,商品未使用且包装完好可申请退货。
定制类商品不支持退货。

## 退货流程

用户在订单页提交退货申请,客服 24 小时内审核。
审核通过后,商品寄回验收无误,3 个工作日内原路退款。
"""


# ── 分块器纯函数 ──


def test_chunker_parent_child_structure() -> None:
    from kongming.rag.chunker import chunk_document

    chunks = chunk_document("退货政策.md", DOC)
    parents = [c for c in chunks if c["level"] == "PARENT"]
    children = [c for c in chunks if c["level"] == "CHILD"]
    assert len(parents) >= 2  # 按标题分父块
    assert children
    parent_seqs = {c["seq"] for c in parents}
    for c in children:
        assert c["parent_seq"] in parent_seqs  # 子块 parent 指向有效
        assert str(c["source_ref"]).startswith("退货政策.md §")
    # 子块尺寸与 overlap
    for c in children[:-1]:
        assert len(str(c["content"])) <= 200


def test_chunker_empty_rejected() -> None:
    from kongming.rag.chunker import EmptyDocument, chunk_document

    with pytest.raises(EmptyDocument):
        chunk_document("x.md", "   \n  ")


# ── 上传与幂等 ──


async def _admin(client: AsyncClient, s: AsyncSession) -> str:
    await seed_all(s, get_settings())
    return await _login(client, "admin", "admin123")


async def test_upload_and_idempotent(client: AsyncClient, session: AsyncSession) -> None:
    token = await _admin(client, session)
    headers = {"Authorization": f"Bearer {token}", "X-Tenant-Id": "default"}
    r: Response
    for _ in range(2):
        r = await client.post(
            "/system/document/upload",
            headers=headers,
            files={"file": ("退货政策.md", DOC.encode(), "text/markdown")},
            data={"datasource_key": "default"},
        )
        assert r.status_code == 200
    body = r.json()  # pyright: ignore[reportPossiblyUnboundVariable]  # range(2) 恒迭代,r 必已绑定
    assert body["skipped"] is True  # 第二次同内容幂等跳过
    # 块结构:PARENT 无向量(测试无 agent),CHILD 有 parent_id
    rows = (
        await session.execute(
            text(
                "SELECT chunk_level, parent_id IS NOT NULL AS has_parent,"
                " embedding IS NOT NULL AS has_vec FROM rag.document_chunks"
            )
        )
    ).all()
    assert any(r.chunk_level == "PARENT" for r in rows)
    assert any(r.chunk_level == "CHILD" and r.has_parent for r in rows)
    assert not any(r.chunk_level == "PARENT" and r.has_vec for r in rows)
    # 文档列表
    r2 = await client.get("/system/document/documents?datasource_key=default", headers=headers)
    assert r2.status_code == 200
    assert any(d["doc_name"] == "退货政策.md" for d in r2.json())


async def test_upload_bad_type_400(client: AsyncClient, session: AsyncSession) -> None:
    token = await _admin(client, session)
    r = await client.post(
        "/system/document/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("x.exe", b"MZ", "application/octet-stream")},
        data={"datasource_key": "default"},
    )
    assert r.status_code == 400


async def test_doc_admin_forbidden(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    await seed_all(session, get_settings())
    uid = await make_user("doc_plain")
    tid = (
        await session.execute(text("SELECT id FROM public.sys_tenant WHERE tenant_key='default'"))
    ).scalar_one()
    await _bind(session, "sys_user_tenant", uid, str(tid))
    token = await _login(client, "doc_plain")
    r = await client.get("/system/document/documents", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


# ── 检索双通道隔离 ──


async def test_retrieve_doc_hints_isolated(client: AsyncClient, session: AsyncSession) -> None:
    """文档命中不进 schema_hint;doc_hints 带 source_ref(双通道隔离)。

    R1-L8:断言无条件——ASCII 词构造确定性全文命中(simple 配置对中文整段
    词命中不稳定;向量路在测试环境无 agent 降级)。"""
    token = await _admin(client, session)
    await client.post(
        "/system/document/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={
            "file": (
                "policy.md",
                b"return policy: within 7 days. refund process: apply first.",
                "text/markdown",
            )
        },
        data={"datasource_key": "default"},
    )
    r = await client.post(
        "/internal/rag/retrieve",
        headers=INTERNAL,
        json={"tenant_key": "default", "question": "return policy"},
    )
    assert r.status_code == 200
    body = r.json()  # pyright: ignore[reportPossiblyUnboundVariable]  # range(2) 恒迭代,r 必已绑定
    assert body["doc_hints"], "全文路应确定性命中 return/policy"
    assert body["doc_hints"][0]["source_ref"].startswith("policy.md §")
    assert "schema_hint" in body  # schema 通道独立(可为空)
