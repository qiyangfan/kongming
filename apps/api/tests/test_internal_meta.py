"""内部元数据通道测试(internal-meta spec):内省范围/截断/fail-closed/鉴权。

真实 PG:模块级建租户 schema + 带注释表,用例级断言;收尾 DROP。
"""

from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.config import get_settings
from kongming.db import get_engine

INTERNAL = {"X-Internal-Token": "test-internal-token"}
SCHEMA = "tenant_metatest"


@pytest.fixture(scope="module", autouse=True)
async def _biz() -> AsyncIterator[None]:  # pyright: ignore[reportUnusedFunction]
    async with get_engine().begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))
        await conn.execute(
            text(
                f'CREATE TABLE IF NOT EXISTS "{SCHEMA}".arms ('
                " id uuid PRIMARY KEY, title varchar(64), qty integer)"
            )
        )
        await conn.execute(text(f"COMMENT ON TABLE \"{SCHEMA}\".arms IS '兵器库'"))
        await conn.execute(text(f"COMMENT ON COLUMN \"{SCHEMA}\".arms.title IS '名称'"))
        # 第二张表:截断用例的陪衬(保留表排除后,仅靠它撑出超限场景)
        await conn.execute(
            text(f'CREATE TABLE IF NOT EXISTS "{SCHEMA}".armor (id uuid PRIMARY KEY)')
        )
        # 视图不入内省(收录收窄 BASE TABLE 的证物)
        await conn.execute(text(f'DROP VIEW IF EXISTS "{SCHEMA}".arms_view'))
        await conn.execute(
            text(f'CREATE VIEW "{SCHEMA}".arms_view AS SELECT * FROM "{SCHEMA}".arms'),  # noqa: S608
        )
        # 另一个租户的 schema(边界证物:不应出现在 metatest 的内省结果)
        await conn.execute(text('CREATE SCHEMA IF NOT EXISTS "tenant_other"'))
        await conn.execute(
            text('CREATE TABLE IF NOT EXISTS "tenant_other".secret (id uuid PRIMARY KEY)')
        )
    yield
    async with get_engine().begin() as conn:
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE'))
        await conn.execute(text('DROP SCHEMA IF EXISTS "tenant_other" CASCADE'))


async def _mk_tenant(s: AsyncSession, key: str, status: str = "ACTIVE") -> None:
    await s.execute(
        text(
            "INSERT INTO public.sys_tenant (id, tenant_key, tenant_name, status)"
            " VALUES (gen_random_uuid(), :k, :k, :st) ON CONFLICT DO NOTHING"
        ),
        {"k": key, "st": status},
    )
    await s.commit()


async def test_requires_internal_token(client: AsyncClient) -> None:
    r = await client.post("/internal/meta/schema", json={"tenant_key": "metatest"})
    assert r.status_code == 403


async def test_unknown_tenant_404(client: AsyncClient, session: AsyncSession) -> None:
    r = await client.post("/internal/meta/schema", json={"tenant_key": "ghost"}, headers=INTERNAL)
    assert r.status_code == 404


async def test_disabled_tenant_404(client: AsyncClient, session: AsyncSession) -> None:
    await _mk_tenant(session, "disabled1", status="DISABLED")
    r = await client.post(
        "/internal/meta/schema", json={"tenant_key": "disabled1"}, headers=INTERNAL
    )
    assert r.status_code == 404


async def test_non_default_datasource_400(client: AsyncClient, session: AsyncSession) -> None:
    await _mk_tenant(session, "metatest")
    r = await client.post(
        "/internal/meta/schema",
        json={"tenant_key": "metatest", "datasource_key": "ext1"},
        headers=INTERNAL,
    )
    assert r.status_code == 400


async def test_introspection_with_comments(client: AsyncClient, session: AsyncSession) -> None:
    await _mk_tenant(session, "metatest")
    r = await client.post(
        "/internal/meta/schema", json={"tenant_key": "metatest"}, headers=INTERNAL
    )
    assert r.status_code == 200
    body = r.json()
    assert body["truncated"] is False
    by_name = {(t["schema"], t["name"]): t for t in body["tables"]}
    arms = by_name.get((SCHEMA, "arms"))
    assert arms is not None, f"arms 表未内省到: {list(by_name)}"
    assert arms["comment"] == "兵器库"
    cols = {c["name"]: c for c in arms["columns"]}
    assert cols["title"]["comment"] == "名称"
    assert cols["qty"]["data_type"] == "integer"
    # 边界:其他租户 schema 不出现
    assert ("tenant_other", "secret") not in by_name
    # 控制面保留表不进入提示，避免泄露 sys_user.password_hash。
    assert ("public", "sys_user") not in by_name
    assert ("public", "chat_message") not in by_name
    assert ("public", "user_identities") not in by_name
    # 跨域泄漏回归闸:非保留名的控制面表(report/judge_run 族)同样不得
    # 入租户知识——default 路径内省只看租户 schema,public 整体不进
    assert ("public", "report") not in by_name
    assert ("public", "judge_run") not in by_name
    # 视图不入内省(便捷视图行扇出/列缺失对生成上下文有毒)
    assert (SCHEMA, "arms_view") not in by_name


async def test_truncation_marked(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _mk_tenant(session, "metatest")
    monkeypatch.setattr(get_settings(), "meta_schema_max_tables", 1)
    r = await client.post(
        "/internal/meta/schema", json={"tenant_key": "metatest"}, headers=INTERNAL
    )
    body = r.json()
    assert body["truncated"] is True
    assert len(body["tables"]) == 1
