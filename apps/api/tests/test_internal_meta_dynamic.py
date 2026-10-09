"""内部元数据通道动态源测试(008 internal-meta delta,测试先行)。

动态源限 default_schema 单 schema 内省;非法 key 同形 400(防探测);
保留表排除与默认路径同一判定;内省走动态池连接(不越出目标库)。
默认路径零回归由 test_internal_meta.py 既有用例兜底。
"""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.config import get_settings
from kongming.datasource.cipher import get_cipher
from kongming.db import get_engine
from tests.conftest import db_conn

INTERNAL = {"X-Internal-Token": "test-internal-token"}
SCHEMA = "ds_meta"
SCHEMA_OTHER = "ds_meta_other"


@pytest.fixture(scope="module", autouse=True)
async def _biz() -> AsyncIterator[None]:  # pyright: ignore[reportUnusedFunction]
    async with get_engine().begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))
        await conn.execute(
            text(
                f'CREATE TABLE IF NOT EXISTS "{SCHEMA}".arms ('
                " id uuid PRIMARY KEY, title varchar(64))"
            )
        )
        await conn.execute(text(f"COMMENT ON TABLE \"{SCHEMA}\".arms IS '动态兵器库'"))
        # 第二张可见表:截断用例陪衬(保留表不占上限,需两张可见表撑出超限)
        await conn.execute(
            text(f'CREATE TABLE IF NOT EXISTS "{SCHEMA}".armor (id uuid PRIMARY KEY)')
        )
        # 保留名表:排除判定证物
        await conn.execute(text(f'CREATE TABLE IF NOT EXISTS "{SCHEMA}".sys_cfg (k varchar(8))'))
        # 另一 schema:边界证物(动态源内省不得越出 default_schema)
        await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA_OTHER}"'))
        await conn.execute(
            text(f'CREATE TABLE IF NOT EXISTS "{SCHEMA_OTHER}".secret (id uuid PRIMARY KEY)')
        )
    yield
    async with get_engine().begin() as conn:
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE'))
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{SCHEMA_OTHER}" CASCADE'))


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


async def _mk_ds(s: AsyncSession, key: str, tenant_id: str, *, status: str = "ACTIVE") -> None:
    conn = db_conn()  # 连接参数环境派生(不硬编码端口,见 conftest.db_conn)
    enc = get_cipher(get_settings().datasource_encrypt_key).encrypt(str(conn["password"]))
    await s.execute(
        text(
            "INSERT INTO public.sys_datasource"
            " (id, datasource_key, name, tenant_id, host, port, database, username,"
            "  password_enc, default_schema, status)"
            " VALUES (gen_random_uuid(), :k, :k, :t, :h, :p, :db,"
            "         :u, :pe, :ds, :st)"
        ),
        {
            "k": key,
            "t": tenant_id,
            "h": conn["host"],
            "p": conn["port"],
            "db": conn["database"],
            "u": conn["username"],
            "pe": enc,
            "ds": SCHEMA,
            "st": status,
        },
    )
    await s.commit()


def _req(tenant_key: str, datasource_key: str) -> dict[str, Any]:
    return {"tenant_key": tenant_key, "datasource_key": datasource_key}


async def test_dynamic_meta_limited_to_default_schema(
    client: AsyncClient, session: AsyncSession
) -> None:
    tid = await _mk_tenant(session, "mta")
    await _mk_ds(session, "metadyn", tid)
    r = await client.post("/internal/meta/schema", json=_req("mta", "metadyn"), headers=INTERNAL)
    assert r.status_code == 200
    body = r.json()
    by_name = {(t["schema"], t["name"]): t for t in body["tables"]}
    arms = by_name.get((SCHEMA, "arms"))
    assert arms is not None, f"arms 未内省到: {list(by_name)}"
    assert arms["comment"] == "动态兵器库"  # 注释随动态连接取到
    # 不越出 default_schema:其他 schema / public / 租户 schema 均不出现
    assert (SCHEMA_OTHER, "secret") not in by_name
    assert all(schema == SCHEMA for schema, _ in by_name)


async def test_dynamic_meta_reserved_excluded(client: AsyncClient, session: AsyncSession) -> None:
    tid = await _mk_tenant(session, "mta")
    await _mk_ds(session, "metarsv", tid)
    r = await client.post("/internal/meta/schema", json=_req("mta", "metarsv"), headers=INTERNAL)
    names = [t["name"] for t in r.json()["tables"]]
    assert "sys_cfg" not in names  # 保留表排除同一判定
    assert "arms" in names


async def test_dynamic_meta_invalid_same_shape(client: AsyncClient, session: AsyncSession) -> None:
    """不存在/禁用/越属 → 同形 400,不返回任何 schema 信息。"""
    tid = await _mk_tenant(session, "mta")
    tid_b = await _mk_tenant(session, "mtb")
    await _mk_ds(session, "metaoff", tid, status="DISABLED")
    await _mk_ds(session, "metab", tid_b)
    details: set[str] = set()
    for key in ("ghost", "metaoff", "metab"):
        r = await client.post("/internal/meta/schema", json=_req("mta", key), headers=INTERNAL)
        assert r.status_code == 400, key
        assert "tables" not in r.json()
        details.add(r.json()["detail"])
    assert details == {"DATASOURCE_INVALID"}


async def test_dynamic_meta_truncation_still_works(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    tid = await _mk_tenant(session, "mta")
    await _mk_ds(session, "metatrunc", tid)
    monkeypatch.setattr(get_settings(), "meta_schema_max_tables", 1)
    r = await client.post("/internal/meta/schema", json=_req("mta", "metatrunc"), headers=INTERNAL)
    body = r.json()
    assert body["truncated"] is True
    assert len(body["tables"]) == 1
