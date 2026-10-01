"""datascope 改写规则测试(spec「单元测试覆盖」十条清单,宪法 Article III 测试先行)。

十条清单逐条对应 test_* 命名注释;另补多角色取最宽松、未绑定部门降级、
UNION/FROM 子查询降级、值绑定类型感知等实现边界。
DB 依赖用例走真实 PG(kongming_test),业务表建于专属 schema,模块级夹具收尾清理。
"""

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.datascope import applier, models, rewrite, service
from kongming.db import get_engine

BIZ_SCHEMA = "tenant_ds"


@pytest.fixture(scope="module", autouse=True)
async def _biz_schema() -> AsyncIterator[None]:  # pyright: ignore[reportUnusedFunction]
    """业务侧造数:带/不带过滤列的表各一,模块结束即毁。"""
    async with get_engine().begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{BIZ_SCHEMA}"'))
        await conn.execute(
            text(
                f'CREATE TABLE IF NOT EXISTS "{BIZ_SCHEMA}".biz_order ('
                " id uuid PRIMARY KEY, dept_id uuid, create_by varchar(64), amount int)"
            )
        )
        await conn.execute(
            text(
                f'CREATE TABLE IF NOT EXISTS "{BIZ_SCHEMA}".biz_order_uuid_creator ('
                " id uuid PRIMARY KEY, create_by uuid)"
            )
        )
        await conn.execute(
            text(
                f'CREATE TABLE IF NOT EXISTS "{BIZ_SCHEMA}".biz_ref ('
                " id uuid PRIMARY KEY, name varchar(16))"
            )
        )
        # 同名表跨 schema 不同列集:显式 schema 限定时探测不得串库
        await conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS public.km_dual"
                " (id uuid PRIMARY KEY, create_by varchar(64))"
            )
        )
        await conn.execute(
            text(f'CREATE TABLE IF NOT EXISTS "{BIZ_SCHEMA}".km_dual (id uuid PRIMARY KEY)')
        )
    yield
    async with get_engine().begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS public.km_dual"))
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{BIZ_SCHEMA}" CASCADE'))


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


async def _mk_dept(s: AsyncSession, tenant_id: str, name: str, parent_id: str | None) -> str:
    did = (
        await s.execute(
            text(
                "INSERT INTO public.sys_dept (id, parent_id, dept_name, tenant_id)"
                " VALUES (gen_random_uuid(), :p, :n, :t) RETURNING id"
            ),
            {"p": parent_id, "n": name, "t": tenant_id},
        )
    ).scalar_one()
    await s.commit()
    return str(did)


async def _mk_role(s: AsyncSession, key: str, scope: str) -> str:
    rid = (
        await s.execute(
            text(
                "INSERT INTO public.sys_role (id, role_key, role_name, data_scope)"
                " VALUES (gen_random_uuid(), :k, :k, :ds) RETURNING id"
            ),
            {"k": key, "ds": scope},
        )
    ).scalar_one()
    await s.commit()
    return str(rid)


async def _bind_role(s: AsyncSession, user_id: str, role_id: str) -> None:
    await s.execute(
        text("INSERT INTO public.sys_user_role (user_id, role_id) VALUES (:u, :r)"),
        {"u": user_id, "r": role_id},
    )
    await s.commit()


async def _set_role_depts(s: AsyncSession, role_id: str, dept_ids: list[str]) -> None:
    """CUSTOM 部门集合直插(整包替换语义与仓储实现同构,arrange 独立于被测)。"""
    await s.execute(text("DELETE FROM public.sys_role_dept WHERE role_id=:r"), {"r": role_id})
    for d in dept_ids:
        await s.execute(
            text("INSERT INTO public.sys_role_dept (role_id, dept_id) VALUES (:r, :d)"),
            {"r": role_id, "d": d},
        )
    await s.commit()


async def _mk_user_with_dept(s: AsyncSession, username: str, dept_id: str | None) -> str:
    uid = (
        await s.execute(
            text(
                "INSERT INTO public.sys_user (id, username, dept_id)"
                " VALUES (gen_random_uuid(), :u, :d) RETURNING id"
            ),
            {"u": username, "d": dept_id},
        )
    ).scalar_one()
    await s.commit()
    return str(uid)


# ============ 纯领域:service.resolve(十条之 1/3/7/9 的意图层) ============


def _ctx(scope: str, uid: str = "u1", dept_ids: list[str] | None = None) -> models.ScopeContext:
    return models.ScopeContext(data_scope=scope, user_id=uid, dept_ids=dept_ids or [])


def test_resolve_all_skip() -> None:  # 清单 1:ALL 不改写
    flt = service.resolve(_ctx("ALL"), column_exists=True)
    assert flt.type is models.FilterType.SKIP


def test_resolve_custom_without_depts_unsupported() -> None:  # 清单 9:CUSTOM 空集合 fail-closed
    flt = service.resolve(_ctx("CUSTOM"), column_exists=True)
    assert flt.type is models.FilterType.UNSUPPORTED


def test_resolve_custom_empty_beats_column_degrade() -> None:
    """空集合的 fail-closed 优先于「表无列降级」——禁把拒绝漂成不过滤(fail-open)。"""
    flt = service.resolve(_ctx("CUSTOM"), column_exists=False)
    assert flt.type is models.FilterType.UNSUPPORTED


def test_resolve_custom_with_depts_in_filter() -> None:  # 清单 11 意图层
    flt = service.resolve(_ctx("CUSTOM", dept_ids=["d1", "d2"]), column_exists=True)
    assert flt.type is models.FilterType.DEPT_IN
    assert flt.column == "dept_id"
    assert flt.values == ["d1", "d2"]


def test_resolve_custom_no_column_degrade() -> None:  # 清单 12 意图层(与 DEPT 同源降级)
    flt = service.resolve(_ctx("CUSTOM", dept_ids=["d1"]), column_exists=False)
    assert flt.type is models.FilterType.SKIP
    assert flt.skip_reason is not None


def test_resolve_unknown_scope_unsupported() -> None:
    flt = service.resolve(_ctx("BOGUS"), column_exists=True)
    assert flt.type is models.FilterType.UNSUPPORTED


def test_resolve_self_with_column() -> None:  # 清单 6 意图层
    flt = service.resolve(_ctx("SELF", uid="u-9"), column_exists=True)
    assert flt.type is models.FilterType.USER_EQ
    assert flt.column == "create_by"
    assert flt.values == ["u-9"]


def test_resolve_self_without_column_degrade() -> None:  # 清单 7 意图层
    flt = service.resolve(_ctx("SELF"), column_exists=False)
    assert flt.type is models.FilterType.SKIP
    assert flt.skip_reason is not None


def test_resolve_dept_without_column_degrade() -> None:  # 清单 3 意图层
    flt = service.resolve(_ctx("DEPT", dept_ids=["d1"]), column_exists=False)
    assert flt.type is models.FilterType.SKIP


def test_resolve_dept_unbound_degrade() -> None:
    flt = service.resolve(_ctx("DEPT", dept_ids=[]), column_exists=True)
    assert flt.type is models.FilterType.SKIP


def test_resolve_dept_in_values() -> None:  # 清单 2/4 意图层
    flt = service.resolve(_ctx("DEPT_AND_BELOW", dept_ids=["d1", "d2"]), column_exists=True)
    assert flt.type is models.FilterType.DEPT_IN
    assert flt.column == "dept_id"
    assert flt.values == ["d1", "d2"]


def test_most_permissive_ranking() -> None:
    assert service.most_permissive(["SELF", "ALL"]) == "ALL"
    assert service.most_permissive(["SELF", "DEPT"]) == "DEPT"
    assert service.most_permissive(["DEPT", "DEPT_AND_BELOW"]) == "DEPT_AND_BELOW"
    # CUSTOM 宽松序高于 DEPT 系(与上游数值序一致)——命中即 fail-closed
    assert service.most_permissive(["CUSTOM", "DEPT"]) == "CUSTOM"
    assert service.most_permissive(["ALL", "CUSTOM"]) == "ALL"


# ============ 纯改写:rewrite(十条之 8/10 的改写层 + 参数化断言) ============


def test_rewrite_user_eq_injects_outer_where() -> None:
    """谓词 AND 进最外层 WHERE,列限定主表;投影不含过滤列也必须成立
    (回归:包子查询方案在 SELECT mark 类投影下过滤列不可见,已被证伪)。"""
    flt = models.ScopeFilter(type=models.FilterType.USER_EQ, column="create_by", values=["u-1"])
    sql, params = rewrite.apply_filter("SELECT mark FROM customer", flt)
    assert "WHERE" in sql and "customer.create_by = :km_ds_0" in sql
    assert "mark" in sql  # 原投影不受损
    assert params == {"km_ds_0": "u-1"}
    assert "u-1" not in sql  # 值不出现在 SQL 文本(绑定参数非拼接)


def test_rewrite_existing_where_and_combined() -> None:
    flt = models.ScopeFilter(type=models.FilterType.USER_EQ, column="create_by", values=["u"])
    sql, _ = rewrite.apply_filter("SELECT * FROM customer c WHERE c.id > 1", flt)
    assert "c.id > 1 AND c.create_by = :km_ds_0" in sql


def test_rewrite_dept_in_params() -> None:
    flt = models.ScopeFilter(type=models.FilterType.DEPT_IN, column="dept_id", values=["d1", "d2"])
    sql, params = rewrite.apply_filter("SELECT * FROM customer", flt)
    assert "IN (:km_ds_0, :km_ds_1)" in sql
    assert params == {"km_ds_0": "d1", "km_ds_1": "d2"}


def test_rewrite_skip_returns_original() -> None:
    flt = models.ScopeFilter(type=models.FilterType.SKIP)
    sql, params = rewrite.apply_filter("SELECT * FROM customer", flt)
    assert sql == "SELECT * FROM customer"
    assert params == {}


def test_rewrite_complex_sql_valid() -> None:  # 清单 8:复杂 SQL 改写语法正确
    flt = models.ScopeFilter(type=models.FilterType.DEPT_IN, column="dept_id", values=["d1"])
    original = (
        "SELECT c.name, COUNT(r.id) FROM customer c "
        "JOIN rental r ON c.id = r.customer_id GROUP BY c.name"
    )
    sql, _ = rewrite.apply_filter(original, flt)
    import sqlglot

    parsed = sqlglot.parse_one(sql, dialect="postgres")
    assert parsed is not None


def test_rewrite_with_subquery_keeps_outer_predicate() -> None:
    """清单 8 子查询形态:WHERE 子查询保留,过滤谓词 AND 进最外层且限定主表别名。"""
    flt = models.ScopeFilter(type=models.FilterType.DEPT_IN, column="dept_id", values=["d1"])
    original = (
        "SELECT c.name FROM customer c WHERE c.id IN (SELECT customer_id FROM rental WHERE id > 10)"
    )
    sql, params = rewrite.apply_filter(original, flt)
    assert "IN (SELECT" in sql  # 原子查询原样保留
    assert "c.dept_id IN (:km_ds_0)" in sql  # 谓词限定主表别名,在最外层
    assert params == {"km_ds_0": "d1"}
    import sqlglot

    assert sqlglot.parse_one(sql, dialect="postgres") is not None


def test_rewritten_passes_firewall() -> None:  # 清单 10 前半:改写产物必过防火墙
    from kongming.sqlfw.firewall import check

    flt = models.ScopeFilter(type=models.FilterType.USER_EQ, column="create_by", values=["u"])
    sql, _ = rewrite.apply_filter("SELECT * FROM customer WHERE id > 1", flt)
    assert check(sql).passed


def test_extract_main_table() -> None:
    assert rewrite.extract_main_table("SELECT * FROM customer") == "customer"
    joined = "SELECT c.id FROM public.customer c JOIN x ON 1=1"
    assert rewrite.extract_main_table(joined) == "customer"
    assert rewrite.extract_main_table("SELECT * FROM (SELECT 1 FROM a) t") is None
    assert rewrite.extract_main_table("SELECT 1 UNION SELECT 2") is None


def test_extract_main_table_ref_carries_schema() -> None:
    assert rewrite.extract_main_table_ref("SELECT * FROM customer") == ("customer", None)
    assert rewrite.extract_main_table_ref("SELECT * FROM public.customer") == (
        "customer",
        "public",
    )
    assert rewrite.extract_main_table_ref("SELECT 1 UNION SELECT 2") is None


# ============ DB 全链路:applier.apply(清单 2/3/4/5/6/7 + 降级告警) ============


async def _apply(session: AsyncSession, user_id: str, sql: str) -> models.AppliedSQL:
    async with get_engine().connect() as biz:
        return await applier.apply(
            session, biz, user_id=user_id, tenant_schemas=[BIZ_SCHEMA], sql=sql
        )


async def test_apply_all_untouched(session: AsyncSession) -> None:  # 清单 1
    uid = await _mk_user_with_dept(session, "ds-all", None)
    await _bind_role(session, uid, await _mk_role(session, "R_ALL", "ALL"))
    out = await _apply(session, uid, "SELECT * FROM biz_ref")
    assert out.sql == "SELECT * FROM biz_ref"
    assert out.params == {}
    assert out.warning is None


async def test_apply_dept_rewrite(session: AsyncSession) -> None:  # 清单 2
    tid = await _mk_tenant(session, "ds-t2")
    dept = await _mk_dept(session, tid, "d", None)
    uid = await _mk_user_with_dept(session, "ds-dept", dept)
    await _bind_role(session, uid, await _mk_role(session, "R_DEPT", "DEPT"))
    out = await _apply(session, uid, "SELECT * FROM biz_order")
    assert "dept_id" in out.sql and out.params == {"km_ds_0": uuid.UUID(dept)}  # uuid 列类型感知
    assert out.warning is None


async def test_apply_dept_no_column_degrade(session: AsyncSession) -> None:  # 清单 3
    tid = await _mk_tenant(session, "ds-t3")
    dept = await _mk_dept(session, tid, "d", None)
    uid = await _mk_user_with_dept(session, "ds-dept-nc", dept)
    await _bind_role(session, uid, await _mk_role(session, "R_DEPT_NC", "DEPT"))
    out = await _apply(session, uid, "SELECT * FROM biz_ref")  # biz_ref 无 dept_id
    assert out.sql == "SELECT * FROM biz_ref"
    assert out.warning is not None  # 降级告警


async def test_apply_dept_and_below_recursive(session: AsyncSession) -> None:  # 清单 4
    tid = await _mk_tenant(session, "ds-t4")
    a = await _mk_dept(session, tid, "A", None)
    b = await _mk_dept(session, tid, "B", a)
    c = await _mk_dept(session, tid, "C", b)
    uid = await _mk_user_with_dept(session, "ds-below", a)
    await _bind_role(session, uid, await _mk_role(session, "R_BELOW", "DEPT_AND_BELOW"))
    out = await _apply(session, uid, "SELECT * FROM biz_order")
    assert set(out.params.values()) == {uuid.UUID(x) for x in (a, b, c)}  # IN 含自身与全部子孙


async def test_apply_dept_and_below_leaf(session: AsyncSession) -> None:  # 清单 5
    tid = await _mk_tenant(session, "ds-t5")
    a = await _mk_dept(session, tid, "A", None)
    uid = await _mk_user_with_dept(session, "ds-leaf", a)
    await _bind_role(session, uid, await _mk_role(session, "R_LEAF", "DEPT_AND_BELOW"))
    out = await _apply(session, uid, "SELECT * FROM biz_order")
    assert list(out.params.values()) == [uuid.UUID(a)]  # IN 仅含自己


async def test_apply_self_rewrite(session: AsyncSession) -> None:  # 清单 6
    uid = await _mk_user_with_dept(session, "ds-self", None)
    await _bind_role(session, uid, await _mk_role(session, "R_SELF", "SELF"))
    out = await _apply(session, uid, "SELECT * FROM biz_order")
    assert "create_by" in out.sql and out.params == {"km_ds_0": uid}


async def test_apply_self_no_column_degrade(session: AsyncSession) -> None:  # 清单 7
    uid = await _mk_user_with_dept(session, "ds-self-nc", None)
    await _bind_role(session, uid, await _mk_role(session, "R_SELF_NC", "SELF"))
    out = await _apply(session, uid, "SELECT * FROM biz_ref")
    assert out.sql == "SELECT * FROM biz_ref"
    assert out.warning is not None


async def test_dept_cycle_terminates(session: AsyncSession) -> None:
    """脏数据成环(A↔B):UNION 去重使递归达不动点终止,结果为环上部门集。"""
    tid = await _mk_tenant(session, "ds-cyc")
    a = await _mk_dept(session, tid, "A", None)
    b = await _mk_dept(session, tid, "B", a)
    await session.execute(
        text("UPDATE public.sys_dept SET parent_id = :b WHERE id = :a"), {"a": a, "b": b}
    )
    await session.commit()
    from kongming.datascope.repository import SqlDataScopeRepo

    ids = await SqlDataScopeRepo(session).dept_and_below(a)
    assert set(ids) == {a, b}


async def test_apply_custom_fail_closed(session: AsyncSession) -> None:  # 清单 9
    """CUSTOM 但角色未配置部门集合(历史数据/旁路写入)→ fail-closed 拒绝。"""
    uid = await _mk_user_with_dept(session, "ds-custom", None)
    await _bind_role(session, uid, await _mk_role(session, "R_CUSTOM", "CUSTOM"))
    with pytest.raises(models.DataScopeUnsupported):
        await _apply(session, uid, "SELECT * FROM biz_order")


async def test_apply_custom_two_depts_in_rewrite(session: AsyncSession) -> None:  # 清单 11
    """CUSTOM 配置两个部门 → 改写为 dept_id IN (集合),与 DEPT_AND_BELOW 同一 IN 语义。"""
    tid = await _mk_tenant(session, "ds-t11")
    d1 = await _mk_dept(session, tid, "d1", None)
    d2 = await _mk_dept(session, tid, "d2", None)
    uid = await _mk_user_with_dept(session, "ds-custom-2", None)  # 用户自身无部门也要生效
    rid = await _mk_role(session, "R_CUSTOM_2", "CUSTOM")
    await _bind_role(session, uid, rid)
    await _set_role_depts(session, rid, [d1, d2])
    out = await _apply(session, uid, "SELECT * FROM biz_order")
    assert "dept_id" in out.sql and out.warning is None
    assert set(out.params.values()) == {uuid.UUID(d1), uuid.UUID(d2)}


async def test_apply_custom_union_over_roles(session: AsyncSession) -> None:
    """多角色均为 CUSTOM 时部门集合取并集(与「权限点取并集」同授权语义)。"""
    tid = await _mk_tenant(session, "ds-t11u")
    d1 = await _mk_dept(session, tid, "d1", None)
    d2 = await _mk_dept(session, tid, "d2", None)
    uid = await _mk_user_with_dept(session, "ds-custom-union", None)
    r1 = await _mk_role(session, "R_CUSTOM_U1", "CUSTOM")
    r2 = await _mk_role(session, "R_CUSTOM_U2", "CUSTOM")
    await _bind_role(session, uid, r1)
    await _bind_role(session, uid, r2)
    await _set_role_depts(session, r1, [d1])
    await _set_role_depts(session, r2, [d2])
    out = await _apply(session, uid, "SELECT * FROM biz_order")
    assert set(out.params.values()) == {uuid.UUID(d1), uuid.UUID(d2)}


async def test_apply_custom_not_recursive(session: AsyncSession) -> None:  # 清单 13
    """CUSTOM 集合不递归子部门(递归是 DEPT_AND_BELOW 的语义,两者职责不混)。"""
    tid = await _mk_tenant(session, "ds-t13")
    parent = await _mk_dept(session, tid, "P", None)
    child = await _mk_dept(session, tid, "C", parent)
    grand = await _mk_dept(session, tid, "G", child)
    uid = await _mk_user_with_dept(session, "ds-custom-nr", None)
    rid = await _mk_role(session, "R_CUSTOM_NR", "CUSTOM")
    await _bind_role(session, uid, rid)
    await _set_role_depts(session, rid, [parent])
    out = await _apply(session, uid, "SELECT * FROM biz_order")
    assert list(out.params.values()) == [uuid.UUID(parent)]  # 子/孙部门不入集合
    assert uuid.UUID(child) not in set(out.params.values())
    assert uuid.UUID(grand) not in set(out.params.values())


async def test_apply_custom_no_column_degrade(session: AsyncSession) -> None:  # 清单 12
    """表无部门列时与 DEPT 同源安全降级(不改写 + 告警),不抛语法错。"""
    tid = await _mk_tenant(session, "ds-t12")
    d1 = await _mk_dept(session, tid, "d1", None)
    uid = await _mk_user_with_dept(session, "ds-custom-nc", None)
    rid = await _mk_role(session, "R_CUSTOM_NC", "CUSTOM")
    await _bind_role(session, uid, rid)
    await _set_role_depts(session, rid, [d1])
    out = await _apply(session, uid, "SELECT * FROM biz_ref")  # biz_ref 无 dept_id
    assert out.sql == "SELECT * FROM biz_ref"
    assert out.params == {}
    assert out.warning is not None


async def test_apply_custom_only_active_roles(session: AsyncSession) -> None:
    """禁用角色的部门集合不参与并集(与 user_role_scopes 的 ACTIVE 闸同口径)。"""
    tid = await _mk_tenant(session, "ds-t14")
    d1 = await _mk_dept(session, tid, "d1", None)
    d2 = await _mk_dept(session, tid, "d2", None)
    uid = await _mk_user_with_dept(session, "ds-custom-off", None)
    active = await _mk_role(session, "R_CUSTOM_ON", "CUSTOM")
    off = await _mk_role(session, "R_CUSTOM_OFF", "CUSTOM")
    await session.execute(
        text("UPDATE public.sys_role SET status='DISABLED' WHERE id=:i"), {"i": off}
    )
    await _bind_role(session, uid, active)
    await _bind_role(session, uid, off)
    await _set_role_depts(session, active, [d1])
    await _set_role_depts(session, off, [d2])
    await session.commit()
    out = await _apply(session, uid, "SELECT * FROM biz_order")
    assert list(out.params.values()) == [uuid.UUID(d1)]


async def test_apply_uuid_column_binds_uuid(session: AsyncSession) -> None:
    """列类型感知:create_by 为 uuid 列时绑定 UUID 对象(PG 无 uuid=text 算子)。"""
    uid = await _mk_user_with_dept(session, "ds-uuid", None)
    await _bind_role(session, uid, await _mk_role(session, "R_UUID", "SELF"))
    out = await _apply(session, uid, "SELECT * FROM biz_order_uuid_creator")
    assert list(out.params.values()) == [uuid.UUID(uid)]


async def test_apply_union_degrade(session: AsyncSession) -> None:
    """UNION 取不到主表 → 安全降级(与上游一致)。"""
    uid = await _mk_user_with_dept(session, "ds-union", None)
    await _bind_role(session, uid, await _mk_role(session, "R_UNION", "SELF"))
    out = await _apply(session, uid, "SELECT id FROM biz_ref UNION SELECT id FROM biz_order")
    assert "create_by" not in out.sql
    assert out.warning is not None


async def test_apply_custom_empty_rejects_before_union_degrade(
    session: AsyncSession,
) -> None:  # 清单 10(判据先于降级)
    """CUSTOM 空集合 + 主表不可定位:必须拒绝,不得落入「降级为不过滤」返回全量。

    与上一条对照才有意义——`test_apply_custom_fail_closed` 用的是可定位主表,
    即使把 fail-closed 判定挪到列探测/降级之后它照样绿;本用例钉的正是**顺序**。
    """
    uid = await _mk_user_with_dept(session, "ds-custom-union-empty", None)
    await _bind_role(session, uid, await _mk_role(session, "R_CUSTOM_UNION", "CUSTOM"))
    with pytest.raises(models.DataScopeUnsupported):
        await _apply(session, uid, "SELECT id FROM biz_ref UNION SELECT id FROM biz_order")


async def test_apply_explicit_schema_probe_not_crossed(session: AsyncSession) -> None:
    """显式 schema 限定:探测只看该 schema(public.km_dual 有 create_by,
    tenant_ds.km_dual 无——不限定会 LIMIT 1 随机命中,行为不确定)。"""
    uid = await _mk_user_with_dept(session, "ds-dual", None)
    await _bind_role(session, uid, await _mk_role(session, "R_DUAL", "SELF"))
    # public.km_dual 有 create_by → 必须改写
    out_pub = await _apply(session, uid, "SELECT id FROM public.km_dual")
    assert "create_by" in out_pub.sql and out_pub.warning is None
    # tenant_ds.km_dual 无 create_by → 必须降级(不被 public 同名表误导)
    dual_sql = f'SELECT id FROM "{BIZ_SCHEMA}".km_dual'  # noqa: S608  # 模块常量标识符
    out_ds = await _apply(session, uid, dual_sql)
    assert "create_by" not in out_ds.sql and out_ds.warning is not None
