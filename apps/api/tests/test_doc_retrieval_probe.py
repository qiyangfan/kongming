"""文档检索试跑端点测试:权限复用/逐路命中/与主检索同源/零副作用/伪造同形。"""
# pyright: reportPrivateUsage=false

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.config import get_settings
from tests.conftest import MakeUser
from tests.test_toolcall import _bind, _login

DOC = b"return policy: within 7 days. refund process: apply first."
QUESTION = "return policy"
PROBE = "/system/document/retrieval-probe"


async def _admin(client: AsyncClient, s: AsyncSession) -> str:
    await seed_all(s, get_settings())
    return _login(get_settings().admin_subject)


async def _upload(client: AsyncClient, token: str) -> None:
    r = await client.post(
        "/system/document/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("policy.md", DOC, "text/markdown")},
        data={"datasource_key": "default"},
    )
    assert r.status_code == 200


async def test_probe_forbidden(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    await seed_all(session, get_settings())
    uid = await make_user("probe_plain")
    tid = (
        await session.execute(text("SELECT id FROM public.sys_tenant WHERE tenant_key='default'"))
    ).scalar_one()
    await _bind(session, "sys_user_tenant", uid, str(tid))
    token = _login(client)
    r = await client.get(PROBE, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


async def test_probe_paths_and_parents(client: AsyncClient, session: AsyncSession) -> None:
    """逐路命中可区分、融合名次与父块回填齐全(测试无 agent,向量路恒空)。"""
    token = await _admin(client, session)
    await _upload(client, token)
    r = await client.get(
        PROBE,
        headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": "default"},
        params={"datasource_key": "default", "question": QUESTION},
    )
    assert r.status_code == 200
    body = r.json()
    assert set(body["paths"].keys()) == {"vector", "fulltext"}
    ft = body["paths"]["fulltext"]
    assert ft, "全文路应确定性命中 return/policy"
    for h in ft:
        assert set(h.keys()) == {"child_id", "parent_id", "score"}
        assert h["score"] > 0
    fused = body["fused_children"]
    assert [f["rank"] for f in fused] == list(range(1, len(fused) + 1))
    parents = body["parents"]
    assert parents, "父块应回填"
    assert len(parents) <= 5
    for rank, p in enumerate(parents, start=1):
        assert set(p.keys()) == {
            "parent_id",
            "seq",
            "doc_name",
            "source_ref",
            "citation_key",
            "content",
            "rank",
        }
        assert p["rank"] == rank
        assert p["doc_name"] == "policy.md"
        assert p["source_ref"].startswith("policy.md §")
        assert p["citation_key"].startswith("policy-")


async def test_probe_consistent_with_main_retrieval(
    client: AsyncClient, session: AsyncSession
) -> None:
    """与主检索同源:父块 source_ref 序与 /internal/rag/retrieve doc_hints 一致。"""
    token = await _admin(client, session)
    await _upload(client, token)
    headers = {"Authorization": f"Bearer {token}", "X-Tenant-Id": "default"}
    probe = await client.get(
        PROBE, headers=headers, params={"datasource_key": "default", "question": QUESTION}
    )
    internal = await client.post(
        "/internal/rag/retrieve",
        headers={"X-Internal-Token": "test-internal-token"},
        json={"tenant_key": "default", "question": QUESTION},
    )
    assert internal.status_code == 200
    hints = internal.json()["doc_hints"]
    assert hints
    assert [p["source_ref"] for p in probe.json()["parents"]] == [h["source_ref"] for h in hints]


async def test_probe_zero_side_effects_and_stable(
    client: AsyncClient, session: AsyncSession
) -> None:
    token = await _admin(client, session)
    await _upload(client, token)
    headers = {"Authorization": f"Bearer {token}", "X-Tenant-Id": "default"}
    params = {"datasource_key": "default", "question": QUESTION}
    r1 = await client.get(PROBE, headers=headers, params=params)
    r2 = await client.get(PROBE, headers=headers, params=params)
    assert r1.json() == r2.json()
    docs = (await session.execute(text("SELECT count(*) FROM rag.documents"))).scalar_one()
    chunks = (await session.execute(text("SELECT count(*) FROM rag.document_chunks"))).scalar_one()
    assert docs == 1 and chunks > 0  # 试跑不新增行


async def test_probe_fake_datasource_same_shape(client: AsyncClient, session: AsyncSession) -> None:
    token = await _admin(client, session)
    r = await client.get(
        PROBE,
        headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": "default"},
        params={"datasource_key": "nope", "question": QUESTION},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "DATASOURCE_INVALID"
