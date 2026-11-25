"""动态数据源外键内省:复用语义层契约,不越出 default_schema。"""
# pyright: reportPrivateUsage=false

from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.config import get_settings
from kongming.datasource.cipher import get_cipher
from kongming.db import get_engine
from kongming.rag.schema_graph_loader import load_schema_graph
from tests.conftest import db_conn
from tests.test_toolcall import _login

SCHEMA = "ds_sem"


@pytest.fixture(scope="module", autouse=True)
async def _biz() -> AsyncIterator[None]:  # pyright: ignore[reportUnusedFunction]
    async with get_engine().begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))
        await conn.execute(
            text(f'CREATE TABLE IF NOT EXISTS "{SCHEMA}".parent (id integer PRIMARY KEY)')
        )
        await conn.execute(
            text(
                f'CREATE TABLE IF NOT EXISTS "{SCHEMA}".child ('
                f' id integer PRIMARY KEY, parent_id integer REFERENCES "{SCHEMA}".parent(id))'
            )
        )
    yield
    async with get_engine().begin() as conn:
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE'))


async def _tenant_and_token(s: AsyncSession) -> tuple[str, str]:
    await seed_all(s, get_settings())
    admin_id = (
        await s.execute(text("SELECT id FROM public.sys_user WHERE username='admin'"))
    ).scalar_one()
    tid = (
        await s.execute(
            text(
                "INSERT INTO public.sys_tenant (id, tenant_key, tenant_name)"
                " VALUES (gen_random_uuid(), 'dynsem', 'dynsem') RETURNING id"
            )
        )
    ).scalar_one()
    await s.execute(
        text(
            "INSERT INTO public.sys_user_tenant (user_id, tenant_id)"
            " VALUES (:u, :t) ON CONFLICT DO NOTHING"
        ),
        {"u": str(admin_id), "t": str(tid)},
    )
    conn = db_conn()
    cipher = get_cipher(get_settings().datasource_encrypt_key)
    await s.execute(
        text(
            "INSERT INTO public.sys_datasource"
            " (id, datasource_key, name, tenant_id, host, port, database, username,"
            "  password_enc, default_schema, status)"
            " VALUES (gen_random_uuid(), 'dynsem', 'dynsem', :t, :h, :p, :db,"
            "         :u, :pe, :ds, 'ACTIVE')"
        ),
        {
            "t": str(tid),
            "h": conn["host"],
            "p": conn["port"],
            "db": conn["database"],
            "u": conn["username"],
            "pe": cipher.encrypt(str(conn["password"])),
            "ds": SCHEMA,
        },
    )
    await s.commit()
    return str(tid), _login(get_settings().admin_subject)


async def test_dynamic_introspection_uses_target_source(
    client: AsyncClient, session: AsyncSession
) -> None:
    _tid, token = await _tenant_and_token(session)
    r = await client.post(
        "/system/semantic/relations/introspect",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": "dynsem"},
        json={"datasource_key": "dynsem"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["relations"] >= 1
    row = (
        await session.execute(
            text(
                "SELECT source_table, target_table FROM rag.table_relations"
                " WHERE datasource_key='dynsem'"
            )
        )
    ).one()
    assert row.source_table == f"{SCHEMA}.child"
    assert row.target_table == f"{SCHEMA}.parent"


async def test_dynamic_introspection_invalid_same_shape(
    client: AsyncClient, session: AsyncSession
) -> None:
    _tid, token = await _tenant_and_token(session)
    r = await client.post(
        "/system/semantic/relations/introspect",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": "dynsem"},
        json={"datasource_key": "ghost_ds"},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "DATASOURCE_INVALID"


async def test_dynamic_schema_graph_uses_target_source(session: AsyncSession) -> None:
    tid, _token = await _tenant_and_token(session)
    graph = await load_schema_graph(
        session,
        tenant_id=tid,
        tenant_key="dynsem",
        datasource_key="dynsem",
    )
    assert graph is not None and graph.complete
    keys = {t.key for t in graph.tables}
    assert (SCHEMA, "parent") in keys and (SCHEMA, "child") in keys
    assert any(
        fk.source_key == (SCHEMA, "child") and fk.target_key == (SCHEMA, "parent")
        for fk in graph.foreign_keys
    )
