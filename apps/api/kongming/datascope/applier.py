"""data_scope 应用编排:上下文汇聚(角色/部门/集合/列探测)→ 意图 → 改写。

对应上游 DataScopeApplier 的职责落点(infrastructure 性质):
- 用户不存在/无启用角色 → fail-closed 抛 DataScopeUnsupported(086:主体解析不出可见面时,
  「不过滤」等价于把未知折算成最大权限,不再沿用上游的降级裁定);
- ALL 提前退出(最常见路径,不做列探测);
- DEPT 系先解有效部门集(DEPT 单部门 / DEPT_AND_BELOW 递归 CTE);
- CUSTOM 先解角色自定义部门集合(启用角色并集,不递归)→ 与 DEPT 系同一 IN 改写;
  集合为空则在任何降级分支之前 fail-closed 抛 DataScopeUnsupported(spec 铁律);
- 绑定值按探测列类型绑 UUID/str(PG 无 uuid=text 算子)。
"""

import logging
import uuid
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from kongming.datascope import rewrite, service
from kongming.datascope.models import (
    COL_CREATE_BY,
    COL_DEPT_ID,
    AppliedSQL,
    DataScopeUnsupported,
    FilterType,
    ScopeContext,
)
from kongming.datascope.repository import SqlDataScopeRepo, probe_column_type

if TYPE_CHECKING:
    from kongming.sqlfw.guard import Guarded

log = logging.getLogger(__name__)


async def apply(
    session: AsyncSession,
    biz_conn: AsyncConnection,
    *,
    user_id: str,
    tenant_schemas: list[str],
    sql: str,
    guarded: "Guarded | None" = None,
) -> AppliedSQL:
    """按 user_id 的有效 data_scope 改写 sql;降级带 warning,fail-closed 抛异常。

    guarded(053 单遍复用):调用方已对该 SQL 建立的单遍解析产物;缺省时
    内部独立解析(行为不变)。
    """
    repo = SqlDataScopeRepo(session)

    user_exists, dept_id = await repo.user_dept_id(user_id)
    if not user_exists:
        # fail-closed:身份解析不出来就无法确定可见面。「降级为不过滤」等于
        # 把一个查不到主体的 user_id 当超管使——086 收口(与 CUSTOM 空集合同一判据方向)
        log.warning("data_scope: 用户不存在或已删除,fail-closed 拒绝 (user_id=%s)", user_id)
        raise DataScopeUnsupported(
            "data_scope: 用户不存在或已删除,无法确定数据范围,fail-closed 拒绝"
        )

    scopes = await repo.user_role_scopes(user_id)
    if not scopes:
        # fail-closed:无启用角色=没有任何一行数据被授权可见。功能权限面已按角色
        # status 同闸(086),正常链路走不到这里;留着降级分支就是留一条「治理动作
        # 反向放宽可见面」的暗道(禁用角色曾使 scopes 变空而权限仍在)
        log.warning("data_scope: 用户无启用角色,fail-closed 拒绝 (user_id=%s)", user_id)
        raise DataScopeUnsupported("data_scope: 用户无启用角色,无法确定数据范围,fail-closed 拒绝")

    scope = service.most_permissive(scopes)
    if scope == "ALL":
        return AppliedSQL(sql=sql)

    # DEPT 系解有效部门集(CUSTOM 解角色集合,SELF 无需部门)
    dept_ids: list[str] = []
    if scope == "CUSTOM":
        dept_ids = await repo.custom_dept_ids(user_id)
    elif scope == "DEPT" and dept_id is not None:
        dept_ids = [dept_id]
    elif scope == "DEPT_AND_BELOW" and dept_id is not None:
        dept_ids = await repo.dept_and_below(dept_id)

    ctx = ScopeContext(data_scope=scope, user_id=user_id, dept_ids=dept_ids)
    # fail-closed 判据先于列探测与「主表不可定位→降级」:CUSTOM 空集合禁漂成不过滤
    reason = service.unsupported_reason(ctx)
    if reason is not None:
        raise DataScopeUnsupported(reason)

    # 列探测(DEPT 系与 CUSTOM 探 dept_id,SELF 探 create_by)
    dept_family = scope in ("DEPT", "DEPT_AND_BELOW", "CUSTOM")
    data_type: str | None = None
    if dept_family or scope == "SELF":
        target_column = COL_DEPT_ID if dept_family else COL_CREATE_BY
        ref = (
            guarded.main_table_ref() if guarded is not None else rewrite.extract_main_table_ref(sql)
        )
        if ref is None:
            warn = f"data_scope={scope}: 无法提取主表名(UNION/子查询 FROM),降级为不过滤"
            log.warning("%s (user_id=%s)", warn, user_id)
            return AppliedSQL(sql=sql, warning=warn)
        table, explicit_schema = ref
        # 显式 schema 限定的表只探该 schema(防跨 schema 同名表不同列集的误判)
        probe_schemas = [explicit_schema] if explicit_schema else tenant_schemas
        data_type = await probe_column_type(
            biz_conn, schemas=probe_schemas, table=table, column=target_column
        )

    flt = service.resolve(ctx, column_exists=data_type is not None)
    if flt.type is FilterType.UNSUPPORTED:
        # 纵深防线(构造上已被上方 unsupported_reason 拦死):意图若为 UNSUPPORTED
        # 却走到这里,绝不允许继续改写——改写会退化为「不过滤」即 fail-open
        raise DataScopeUnsupported(flt.skip_reason or "DATA_SCOPE_UNSUPPORTED")
    if flt.type is FilterType.SKIP:
        if flt.skip_reason:
            log.warning("%s (user_id=%s)", flt.skip_reason, user_id)
        return AppliedSQL(sql=sql, warning=flt.skip_reason)

    if guarded is not None:
        new_sql, params = guarded.apply_filter(flt.type.value, flt.column, flt.values)
    else:
        new_sql, params = rewrite.apply_filter(sql, flt)
    # uuid 列绑 UUID 对象,其余列绑 str(探测 data_type 保证类型匹配)
    out_params: dict[str, object] = (
        {k: uuid.UUID(str(v)) for k, v in params.items()} if data_type == "uuid" else params
    )
    return AppliedSQL(sql=new_sql, params=out_params)
