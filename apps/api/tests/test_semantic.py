"""覆盖表关系和指标管理、语义注入与级联删除。"""
# pyright: reportPrivateUsage=false

import uuid
from collections.abc import AsyncIterator
from typing import Any, cast

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.config import get_settings
from kongming.db import get_engine
from tests.conftest import MakeUser
from tests.test_toolcall import _bind, _login

SCHEMA = "tenant_sem"


@pytest.fixture(scope="module", autouse=True)
async def _biz() -> AsyncIterator[None]:  # pyright: ignore[reportUnusedFunction]
    """带 FK 的两表(orders→customers)+无 FK 表,供 introspection。"""
    async with get_engine().begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))  # 模块常量
        await conn.execute(
            text(
                f'CREATE TABLE IF NOT EXISTS "{SCHEMA}".customers ('
                " id uuid PRIMARY KEY, name varchar(64))"
            )
        )
        await conn.execute(
            text(
                f'CREATE TABLE IF NOT EXISTS "{SCHEMA}".orders ('
                " id uuid PRIMARY KEY, customer_id uuid REFERENCES"
                f' "{SCHEMA}".customers(id), amount numeric)'
            )
        )
        await conn.execute(
            text(f'CREATE TABLE IF NOT EXISTS "{SCHEMA}".orphan ( id uuid PRIMARY KEY)')
        )
        # 注:身份表(含租户)由 conftest._clean 用例级 TRUNCATE(知识条目经
        # tenant FK 级联同清)——租户/知识条目播种在 _admin 内逐用例完成。
    yield
    async with get_engine().begin() as conn:
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE'))


async def _admin(client: AsyncClient, s: AsyncSession) -> str:
    await seed_all(s, get_settings())
    # admin 绑 sem 租户
    admin_id = (
        await s.execute(text("SELECT id FROM public.sys_user WHERE username='admin'"))
    ).scalar_one()
    tid = (
        await s.execute(
            text(
                "INSERT INTO public.sys_tenant (id, tenant_key, tenant_name)"
                " VALUES (gen_random_uuid(), 'sem', 'sem')"
                " ON CONFLICT (tenant_key) DO NOTHING RETURNING id"
            ),
        )
    ).scalar_one_or_none()
    if tid is None:
        tid = (
            await s.execute(text("SELECT id FROM public.sys_tenant WHERE tenant_key='sem'"))
        ).scalar_one()
    await s.execute(
        text(
            "INSERT INTO public.sys_user_tenant (user_id, tenant_id)"
            " VALUES (:u, :t) ON CONFLICT DO NOTHING"
        ),
        {"u": str(admin_id), "t": str(tid)},
    )
    # 关系存在性闸的候选源——为 sem 租户播三表知识条目(幂等;
    # 引用知识条目外的表在写边界被拒,故引用表须先有条目)
    for tb in ("orders", "customers", "orphan"):
        await s.execute(
            text(
                "INSERT INTO rag.table_descriptions"
                " (id, tenant_id, datasource_key, schema_name, table_name,"
                "  table_comment, columns_comment, source_type)"
                " VALUES (gen_random_uuid(), :t, 'default', :sn, :tb, '说明', '{}',"
                "         'MANUAL_UPLOAD')"
                " ON CONFLICT (tenant_id, datasource_key, schema_name, table_name)"
                " DO NOTHING"
            ),
            {"t": str(tid), "sn": SCHEMA, "tb": tb},
        )
    await s.commit()
    return await _login(client, "admin", "admin123")


# ============ introspection ============


async def test_introspect_idempotent_and_no_guess(
    client: AsyncClient, session: AsyncSession
) -> None:
    token = await _admin(client, session)
    r: Response
    for _ in range(2):
        r = await client.post(
            "/system/semantic/relations/introspect",
            headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": "sem"},
            json={"datasource_key": "default"},
        )
        assert r.status_code == 200
    assert r.json()["relations"] >= 1  # pyright: ignore[reportPossiblyUnboundVariable]  # range(2) 恒迭代,r 必已绑定;orders→customers FK 被扫描到
    rows = (
        await session.execute(
            text("SELECT source_table, target_table, source FROM rag.table_relations")
        )
    ).all()
    fk = [r for r in rows if "orders" in r.source_table and "customers" in r.target_table]
    assert len(fk) == 1  # 幂等:两次扫描仍一行
    assert fk[0].source == "introspection"
    # 无 FK 表不产生关系
    assert not any("orphan" in r.source_table or "orphan" in r.target_table for r in rows)


async def test_manual_relation_not_overwritten(client: AsyncClient, session: AsyncSession) -> None:
    token = await _admin(client, session)
    # 手工加一条(库内无此 FK)
    r = await client.post(
        "/system/semantic/relations",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": "sem"},
        json={
            "datasource_key": "default",
            "source_table": "tenant_sem.orders",
            "target_table": "tenant_sem.orphan",
            "join_keys": [{"left_col": "id", "right_col": "id"}],
        },
    )
    assert r.status_code == 201
    # 重扫 introspection:manual 行保留
    await client.post(
        "/system/semantic/relations/introspect",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": "sem"},
        json={"datasource_key": "default"},
    )
    row = (
        await session.execute(
            text("SELECT source FROM rag.table_relations WHERE target_table LIKE '%orphan'")
        )
    ).one()
    assert row.source == "manual"


# ============ 指标 CRUD ============


async def test_metric_upsert_and_uniqueness(client: AsyncClient, session: AsyncSession) -> None:
    token = await _admin(client, session)
    headers = {"Authorization": f"Bearer {token}", "X-Tenant-Id": "sem"}
    r1 = await client.post(
        "/system/semantic/metrics",
        headers=headers,
        json={
            "datasource_key": "default",
            "metric_name": "复购率",
            "sql_template": (
                "COUNT(DISTINCT customer_id) / COUNT(DISTINCT date_trunc('month', created_at))"
            ),
            "business_terms": ["复购"],
        },
    )
    assert r1.status_code == 201
    # 同名 upsert=更新(不重复行)
    r2 = await client.post(
        "/system/semantic/metrics",
        headers=headers,
        json={
            "datasource_key": "default",
            "metric_name": "复购率",
            "sql_template": "新口径",
            "business_terms": ["复购", "回头率"],
        },
    )
    assert r2.status_code == 201
    rows = await client.get("/system/semantic/metrics?datasource_key=default", headers=headers)
    metrics = rows.json()
    assert len([m for m in metrics if m["metric_name"] == "复购率"]) == 1
    assert metrics[0]["sql_template"] == "新口径"


# ============ 注入组装(纯函数+服务层) ============


async def test_enrich_narrowed_to_recalled(session: AsyncSession) -> None:
    """enrich 只取召回集合内关系;指标按问句命中。"""
    from kongming.semantic.repository import SqlSemanticRepo
    from kongming.semantic.service import SemanticService

    tid = str(uuid.uuid4())
    repo = SqlSemanticRepo(session)
    await repo.add_relation(
        tenant_id=tid,
        datasource_key="default",
        rel={
            "source_table": "s.orders",
            "target_table": "s.customers",
            "join_keys": [{"left_col": "customer_id", "right_col": "id"}],
        },
    )
    await repo.add_relation(
        tenant_id=tid,
        datasource_key="default",
        rel={
            "source_table": "s.payments",
            "target_table": "s.orders",
            "join_keys": [{"left_col": "order_id", "right_col": "id"}],
        },
    )
    await repo.upsert_metric(
        tenant_id=tid,
        datasource_key="default",
        metric={
            "metric_name": "复购率",
            "sql_template": "count(distinct c)/count(distinct m)",
            "business_terms": ["复购"],
            "enabled": True,
        },
    )
    await session.commit()
    svc = SemanticService(repo)
    # 召回只含 orders/customers → 只出一条关系
    rel_hint, met_hint, hits = await svc.enrich(
        tenant_id=tid,
        datasource_key="default",
        question="复购率怎么算",
        tables=["s.orders", "s.customers"],
    )
    assert "s.orders JOIN s.customers" in rel_hint
    assert "payments" not in rel_hint  # 收窄:召回集合外不进
    assert "复购率" in met_hint
    # 结构化命中与文字段同源(标识/名称/口径/修订时间)
    assert len(hits) == 1
    assert hits[0].metric_name == "复购率"
    assert hits[0].sql_template == "count(distinct c)/count(distinct m)"
    assert hits[0].metric_id
    # 单表召回无关系段
    rel2, _, _ = await svc.enrich(
        tenant_id=tid, datasource_key="default", question="多少订单", tables=["s.orders"]
    )
    assert rel2 == ""


# ============ 级联删除与权限 ============


async def test_cascade_delete_by_datasource(session: AsyncSession) -> None:
    from kongming.semantic.repository import SqlSemanticRepo

    tid = str(uuid.uuid4())
    repo = SqlSemanticRepo(session)
    await repo.add_relation(
        tenant_id=tid,
        datasource_key="ds_x",
        rel={
            "source_table": "a.b",
            "target_table": "a.c",
            "join_keys": [{"left_col": "x", "right_col": "y"}],
        },
    )
    await repo.upsert_metric(
        tenant_id=tid,
        datasource_key="ds_x",
        metric={"metric_name": "m1", "sql_template": "t", "business_terms": [], "enabled": True},
    )
    await session.commit()
    await repo.delete_by_datasource(tenant_id=tid, datasource_key="ds_x")
    await session.commit()
    n = (
        await session.execute(
            text(
                "SELECT (SELECT count(*) FROM rag.table_relations WHERE datasource_key='ds_x')"
                " + (SELECT count(*) FROM rag.metric_definitions WHERE datasource_key='ds_x')"
            )
        )
    ).scalar_one()
    assert n == 0


async def test_semantic_forbidden(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    await seed_all(session, get_settings())
    uid = await make_user("sem_plain")
    tid = (
        await session.execute(text("SELECT id FROM public.sys_tenant WHERE tenant_key='default'"))
    ).scalar_one()
    await _bind(session, "sys_user_tenant", uid, str(tid))
    token = await _login(client, "sem_plain")
    r = await client.get(
        "/system/semantic/relations",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403


# ============ 语义定义行为 ============


async def test_r1_h1_multi_column_fk_paired_correctly(
    client: AsyncClient, session: AsyncSession
) -> None:
    """多列 FK 按序配对(不笛卡尔积)。"""
    async with get_engine().begin() as conn:
        await conn.execute(
            text(
                f'CREATE TABLE IF NOT EXISTS "{SCHEMA}".parent_mc ('
                " a int, b int, PRIMARY KEY (a, b))"
            )
        )
        await conn.execute(text(f'DROP TABLE IF EXISTS "{SCHEMA}"."child_mc"'))
        await conn.execute(
            text(
                f'CREATE TABLE "{SCHEMA}"."child_mc" ('
                " x int, y int,"
                f' FOREIGN KEY (x, y) REFERENCES "{SCHEMA}".parent_mc(a, b))'
            )
        )
    token = await _admin(client, session)
    await client.post(
        "/system/semantic/relations/introspect",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": "sem"},
        json={"datasource_key": "default"},
    )
    # Row 成员访问带 Unknown,cast Any 定型(运行时不变)
    row = cast(
        "Any",
        (
            await session.execute(
                text(
                    "SELECT join_keys FROM rag.table_relations WHERE source_table LIKE '%child_mc'"
                )
            )
        ).one(),
    )
    # join_keys 文本/list 双形态,cast 定型元素为映射(运行时不变)
    keys = cast(
        "list[dict[str, Any]]",
        row.join_keys  # pyright: ignore[reportUnknownMemberType]
        if isinstance(row.join_keys, list)
        else __import__("json").loads(row.join_keys),
    )
    pairs = {(k["left_col"], k["right_col"]) for k in keys}
    assert pairs == {("x", "a"), ("y", "b")}
    async with get_engine().begin() as conn:
        await conn.execute(text(f'DROP TABLE IF EXISTS "{SCHEMA}"."child_mc"'))
        await conn.execute(text(f'DROP TABLE IF EXISTS "{SCHEMA}".parent_mc'))


async def test_r1_h2_introspect_rejects_non_default(
    client: AsyncClient, session: AsyncSession
) -> None:
    """introspect 显式拒非 default 源(v1);伪造 key 同形 400。"""
    token = await _admin(client, session)
    r = await client.post(
        "/system/semantic/relations/introspect",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": "sem"},
        json={"datasource_key": "ghost_ds"},
    )
    assert r.status_code == 400
    r2 = await client.post(
        "/system/semantic/relations",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": "sem"},
        json={
            "datasource_key": "ghost_ds",
            "source_table": "a.b",
            "target_table": "a.c",
            "join_keys": [{"left_col": "x", "right_col": "y"}],
        },
    )
    assert r2.status_code == 400


async def test_r1_m1_manual_takeover_flips_source(
    client: AsyncClient, session: AsyncSession
) -> None:
    """手工 upsert 命中 introspection 行翻转 source=manual,重扫不还原。"""
    token = await _admin(client, session)
    headers = {"Authorization": f"Bearer {token}", "X-Tenant-Id": "sem"}
    await client.post(
        "/system/semantic/relations/introspect",
        headers=headers,
        json={"datasource_key": "default"},
    )
    r = await client.post(
        "/system/semantic/relations",
        headers=headers,
        json={
            "datasource_key": "default",
            "source_table": "tenant_sem.orders",
            "target_table": "tenant_sem.customers",
            "join_keys": [{"left_col": "customer_id", "right_col": "id"}],
        },
    )
    real_id = r.json()["id"]
    row = (
        await session.execute(
            text(
                "SELECT id, source FROM rag.table_relations"
                " WHERE source_table LIKE '%orders' AND target_table LIKE '%customers'"
            )
        )
    ).one()
    assert row.source == "manual"
    assert str(row.id) == real_id
    await client.post(
        "/system/semantic/relations/introspect",
        headers=headers,
        json={"datasource_key": "default"},
    )
    row2 = (
        await session.execute(
            text(
                "SELECT source FROM rag.table_relations"
                " WHERE source_table LIKE '%orders' AND target_table LIKE '%customers'"
            )
        )
    ).one()
    assert row2.source == "manual"


# ============ 手工关系存在性闸 ============


async def test_manual_relation_unknown_table_400(
    client: AsyncClient, session: AsyncSession
) -> None:
    """引用知识条目外的表:400 RELATION_TABLE_UNKNOWN,不落库(写边界闸)。

    拼错的表(schema.table)在召回 relations_hint 中永不命中,是静默死配置;
    闸后错误在创建期即暴露。"""
    token = await _admin(client, session)
    headers = {"Authorization": f"Bearer {token}", "X-Tenant-Id": "sem"}
    r = await client.post(
        "/system/semantic/relations",
        headers=headers,
        json={
            "datasource_key": "default",
            "source_table": f"{SCHEMA}.orders",
            "target_table": f"{SCHEMA}.oders",  # 拼写缺 s:无知识条目
            "join_keys": [{"left_col": "customer_id", "right_col": "id"}],
        },
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "RELATION_TABLE_UNKNOWN"
    n = (
        await session.execute(
            text("SELECT count(*) FROM rag.table_relations WHERE target_table LIKE '%oders'")
        )
    ).scalar_one()
    assert n == 0
