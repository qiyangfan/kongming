"""指标定义可见性按 data_scope 过滤:SQL 同判/解析器/enrich 缺省/管理校验。

判据(semantic-layer spec「修订」):调用者有效范围=启用角色最宽松档;ALL=全量;
DEPT/DEPT_AND_BELOW/CUSTOM=ALL 档 ∪ 部门集合内 DEPT 档;SELF/无身份=仅 ALL 档;
CUSTOM 空集合读面降级(仅 ALL 档,不阻断)。管理面全量视角不过滤。
"""

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.semantic.models import CallerScope
from kongming.semantic.repository import SqlSemanticRepo
from kongming.semantic.service import SemanticService

# ============ 仓储 SQL 同判(list_metrics 可见性过滤) ============


async def _seed_depts(session: AsyncSession, tid: str, count: int = 2) -> list[str]:
    """造 count 个同租户部门(DEPT 档 FK 目标),返回 id 列表。先补租户行。"""
    await session.execute(
        text(
            "INSERT INTO public.sys_tenant (id, tenant_key, tenant_name)"
            " VALUES (:t, :k, 'mv') ON CONFLICT (id) DO NOTHING"
        ),
        {"t": tid, "k": f"mv_{uuid.uuid4().hex[:12]}"},
    )
    ids = [str(uuid.uuid4()) for _ in range(count)]
    for i, did in enumerate(ids):
        await session.execute(
            text(
                "INSERT INTO public.sys_dept (id, dept_name, tenant_id, status)"
                " VALUES (:d, :n, :t, 'ACTIVE')"
            ),
            {"d": did, "n": f"mv_dept_{i}_{uuid.uuid4().hex[:6]}", "t": tid},
        )
    return ids


async def test_list_metrics_visibility_filter(session: AsyncSession) -> None:
    """None=全量(管理面);部门集合=ALL 档∪命中部门;空集合=仅 ALL 档。"""
    repo = SqlSemanticRepo(session)
    tid = str(uuid.uuid4())
    dept_a, dept_b = await _seed_depts(session, tid)
    for name, vs, dp in [
        ("公共指标", "ALL", None),
        ("A部门口径", "DEPT", dept_a),
        ("B部门口径", "DEPT", dept_b),
    ]:
        await repo.upsert_metric(
            tenant_id=tid,
            datasource_key="default",
            metric={
                "metric_name": name,
                "sql_template": "tpl",
                "business_terms": [name],
                "enabled": True,
                "visible_scope": vs,
                "dept_id": dp,
            },
        )
    await session.commit()

    unfiltered = await repo.list_metrics(tenant_id=tid, datasource_key="default")
    assert len(unfiltered) == 3  # 管理面全量视角
    in_a = await repo.list_metrics(
        tenant_id=tid, datasource_key="default", visible_dept_ids=[dept_a]
    )
    assert {str(r["metric_name"]) for r in in_a} == {"公共指标", "A部门口径"}
    none_scope = await repo.list_metrics(
        tenant_id=tid, datasource_key="default", visible_dept_ids=[]
    )
    assert [str(r["metric_name"]) for r in none_scope] == ["公共指标"]
    # upsert 更新路径:DEPT 档改回 ALL 后 dept_id 不残留
    await repo.upsert_metric(
        tenant_id=tid,
        datasource_key="default",
        metric={
            "metric_name": "A部门口径",
            "sql_template": "tpl",
            "business_terms": [],
            "enabled": True,
            "visible_scope": "ALL",
            "dept_id": None,
        },
    )
    await session.commit()
    rows = {
        str(r["metric_name"]): r
        for r in await repo.list_metrics(tenant_id=tid, datasource_key="default")
    }
    assert str(rows["A部门口径"]["visible_scope"]) == "ALL"
    assert rows["A部门口径"]["dept_id"] is None


async def test_list_metrics_tenant_isolation_unchanged(session: AsyncSession) -> None:
    """可见性参数不放松租户/数据源隔离(既有双过滤叠加)。"""
    repo = SqlSemanticRepo(session)
    tid = str(uuid.uuid4())
    await repo.upsert_metric(
        tenant_id=tid,
        datasource_key="default",
        metric={"metric_name": "T", "sql_template": "x", "business_terms": [], "enabled": True},
    )
    await session.commit()
    assert await repo.list_metrics(tenant_id=str(uuid.uuid4()), datasource_key="default") == []
    assert await repo.list_metrics(tenant_id=tid, datasource_key="other") == []


# ============ CallerDeptScopeResolver 适配器(datascope 原语组合) ============


async def _seed_scope_world(session: AsyncSession) -> dict[str, str]:
    """部门树 A(子 A1)/B + 六种角色形态的用户,返回 id 表。"""
    tid = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO public.sys_tenant (id, tenant_key, tenant_name)"
            " VALUES (:t, :k, 'mv') ON CONFLICT (id) DO NOTHING"
        ),
        {"t": tid, "k": f"mv_{uuid.uuid4().hex[:12]}"},
    )
    dept_a, dept_a1, dept_b = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    for did, name, parent in [(dept_a, "A", None), (dept_a1, "A1", dept_a), (dept_b, "B", None)]:
        await session.execute(
            text(
                "INSERT INTO public.sys_dept (id, dept_name, tenant_id, parent_id, status)"
                " VALUES (:d, :n, :t, :p, 'ACTIVE')"
            ),
            {"d": did, "n": name, "t": tid, "p": parent},
        )
    users: dict[str, str] = {}
    for key, scope, dept, custom_depts in [
        ("u_all", "ALL", None, []),
        ("u_dept", "DEPT", dept_a, []),
        ("u_dab", "DEPT_AND_BELOW", dept_a, []),  # A → 递归含子 A1
        ("u_custom", "CUSTOM", None, [dept_b]),
        ("u_custom_empty", "CUSTOM", None, []),
        ("u_self", "SELF", None, []),
        ("u_none", None, None, []),  # 无启用角色
    ]:
        uid = str(uuid.uuid4())
        await session.execute(
            text("INSERT INTO public.sys_user (id, username, status) VALUES (:u, :n, 'ACTIVE')"),
            {"u": uid, "n": f"mv_{key}_{uuid.uuid4().hex[:8]}"},
        )
        if dept is not None:
            await session.execute(
                text("UPDATE public.sys_user SET dept_id=:d WHERE id=:u"),
                {"d": dept, "u": uid},
            )
        if scope is not None:
            rid = str(uuid.uuid4())
            await session.execute(
                text(
                    "INSERT INTO public.sys_role (id, role_key, role_name, data_scope, status)"
                    " VALUES (:r, :rk, :rk, :ds, 'ACTIVE')"
                ),
                {"r": rid, "rk": f"mv_{key}", "ds": scope},
            )
            await session.execute(
                text("INSERT INTO public.sys_user_role (user_id, role_id) VALUES (:u, :r)"),
                {"u": uid, "r": rid},
            )
            for cd in custom_depts:
                await session.execute(
                    text("INSERT INTO public.sys_role_dept (role_id, dept_id) VALUES (:r, :d)"),
                    {"r": rid, "d": cd},
                )
        users[key] = uid
    await session.commit()
    return {"tenant": tid, "dept_a": dept_a, "dept_a1": dept_a1, "dept_b": dept_b, **users}


async def test_resolver_scope_shapes(session: AsyncSession) -> None:
    """ALL→universal;DEPT/CUSTOM→集合;DEPT_AND_BELOW→递归;SELF/CUSTOM 空/无角色→空。"""
    from kongming.semantic.adapters import CallerDeptScopeAdapter

    w = await _seed_scope_world(session)
    ad = CallerDeptScopeAdapter(session)
    assert await ad.resolve(user_id=w["u_all"], tenant_id=w["tenant"]) == CallerScope(
        universal=True
    )
    assert await ad.resolve(user_id=w["u_dept"], tenant_id=w["tenant"]) == CallerScope(
        universal=False, dept_ids=(w["dept_a"],)
    )
    dab = await ad.resolve(user_id=w["u_dab"], tenant_id=w["tenant"])
    assert set(dab.dept_ids) == {w["dept_a"], w["dept_a1"]}  # 递归含子
    assert await ad.resolve(user_id=w["u_custom"], tenant_id=w["tenant"]) == CallerScope(
        universal=False, dept_ids=(w["dept_b"],)
    )
    for key in ("u_custom_empty", "u_self", "u_none"):
        assert await ad.resolve(user_id=w[key], tenant_id=w["tenant"]) == CallerScope(
            universal=False, dept_ids=()
        ), key
    assert await ad.resolve(user_id=str(uuid.uuid4()), tenant_id=w["tenant"]) == CallerScope(
        universal=False, dept_ids=()
    )


# ============ enrich 可见性缺省与注入(同源回归) ============


class _StubResolver:
    def __init__(self, scope: CallerScope) -> None:
        self._scope = scope

    async def resolve(self, *, user_id: str, tenant_id: str) -> CallerScope:
        return self._scope


async def test_enrich_visibility_paths(session: AsyncSession) -> None:
    """None 身份=仅 ALL 档;universal=全量;部门集合过滤;派生不破。"""
    repo = SqlSemanticRepo(session)
    tid = str(uuid.uuid4())
    dept_a = (await _seed_depts(session, tid))[0]
    for name, vs, dp, terms in [
        ("公共指标", "ALL", None, ["公共"]),
        ("受限口径", "DEPT", dept_a, ["受限"]),
    ]:
        await repo.upsert_metric(
            tenant_id=tid,
            datasource_key="default",
            metric={
                "metric_name": name,
                "sql_template": f"{name}模板",
                "business_terms": terms,
                "enabled": True,
                "visible_scope": vs,
                "dept_id": dp,
            },
        )
    await session.commit()
    svc = SemanticService(repo)

    # 无身份(缺省)→ 仅 ALL 档;受限口径不进命中,文字段不含
    _, hint_none, hits_none = await svc.enrich(
        tenant_id=tid, datasource_key="default", question="公共 受限", tables=[]
    )
    assert "受限口径" not in hint_none and "公共指标" in hint_none
    assert [h.metric_name for h in hits_none] == ["公共指标"]

    # universal(ALL 角色)→ 全量;文字段仍由结构化命中派生(同源)
    svc_all = SemanticService(
        repo, caller_scope_resolver=_StubResolver(CallerScope(universal=True))
    )
    _, hint_all, hits_all = await svc_all.enrich(
        tenant_id=tid, datasource_key="default", question="公共 受限", tables=[], caller_user_id="u"
    )
    assert "受限口径" in hint_all and "公共指标" in hint_all
    assert [h.metric_name for h in hits_all] == ["公共指标", "受限口径"]

    # 命中部门集合 → 两档都见;未命中部门 → 仅 ALL 档
    svc_a = SemanticService(
        repo, caller_scope_resolver=_StubResolver(CallerScope(universal=False, dept_ids=(dept_a,)))
    )
    _, hint_a, _ = await svc_a.enrich(
        tenant_id=tid, datasource_key="default", question="公共 受限", tables=[], caller_user_id="u"
    )
    assert "受限口径" in hint_a
    svc_b = SemanticService(
        repo, caller_scope_resolver=_StubResolver(CallerScope(universal=False, dept_ids=()))
    )
    _, hint_b, hits_b = await svc_b.enrich(
        tenant_id=tid, datasource_key="default", question="公共 受限", tables=[], caller_user_id="u"
    )
    assert "受限口径" not in hint_b
    assert [h.metric_name for h in hits_b] == ["公共指标"]


# ============ 管理端契约校验(MetricIn) ============


def test_metric_in_validation() -> None:
    """DEPT 档缺 dept_id 拒;ALL 档 dept_id 允许为 None;非法档位拒。"""
    import pytest as _pytest
    from pydantic import ValidationError

    from kongming.semantic.router import MetricIn

    base: dict[str, Any] = {
        "datasource_key": "default",
        "metric_name": "m",
        "sql_template": "t",
    }
    with _pytest.raises(ValidationError):
        MetricIn(**base, visible_scope="DEPT", dept_id=None)
    ok_all = MetricIn(**base)
    assert ok_all.visible_scope == "ALL" and ok_all.dept_id is None
    ok_dept = MetricIn(**base, visible_scope="DEPT", dept_id=uuid.uuid4())
    assert ok_dept.dept_id is not None
    with _pytest.raises(ValidationError):
        MetricIn(**base, visible_scope="TEAM")
