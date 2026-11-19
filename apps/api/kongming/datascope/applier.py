"""数据范围应用：汇总角色、部门和列信息，计算过滤意图并改写 SQL。

- 用户缺失、无启用角色、CUSTOM 集合为空或范围值无效时拒绝执行。
- ALL 不做列探测；DEPT_AND_BELOW 包含本部门及其子部门；CUSTOM 合并启用角色配置的部门，不递归。
- 绑定参数类型与目标列一致，避免 PostgreSQL 的 UUID 与文本类型不匹配。"""

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
    ScopeFilter,
)
from kongming.datascope.repository import SqlDataScopeRepo, probe_column_type

if TYPE_CHECKING:
    from kongming.sqlfw.guard import Guarded

log = logging.getLogger(__name__)


async def _resolve_dept_ids(
    repo: SqlDataScopeRepo, scope: str, *, user_id: str, dept_id: str | None
) -> list[str]:
    """按范围类型解有效部门集(DEPT 系;SELF 无需部门)。"""
    if scope == "CUSTOM":
        return await repo.custom_dept_ids(user_id)
    if scope == "DEPT" and dept_id is not None:
        return [dept_id]
    if scope == "DEPT_AND_BELOW" and dept_id is not None:
        return await repo.dept_and_below(dept_id)
    return []


async def _probe_filter_type(
    biz_conn: AsyncConnection,
    scope: str,
    *,
    user_id: str,
    tenant_schemas: list[str],
    sql: str,
    guarded: "Guarded | None",
) -> str | None:
    """探测过滤目标列类型(uuid 等);主表不可定位时记降级告警并返回 None。"""
    # 列探测(DEPT 系与 CUSTOM 探 dept_id,SELF 探 create_by)
    dept_family = scope in ("DEPT", "DEPT_AND_BELOW", "CUSTOM")
    target_column = COL_DEPT_ID if dept_family else COL_CREATE_BY
    ref = guarded.main_table_ref() if guarded is not None else rewrite.extract_main_table_ref(sql)
    if ref is None:
        log.warning(
            "data_scope=%s: 无法提取主表名(UNION/子查询 FROM),降级为不过滤 (user_id=%s)",
            scope,
            user_id,
        )
        return None
    table, explicit_schema = ref
    # 显式 schema 限定的表只探该 schema(防跨 schema 同名表不同列集的误判)
    probe_schemas = [explicit_schema] if explicit_schema else tenant_schemas
    return await probe_column_type(
        biz_conn, schemas=probe_schemas, table=table, column=target_column
    )


async def _load_scope(session: AsyncSession, user_id: str) -> tuple[str, str | None]:
    """取用户有效范围(最宽松角色);主体或角色缺失即 fail-closed。"""
    repo = SqlDataScopeRepo(session)
    user_exists, dept_id = await repo.user_dept_id(user_id)
    if not user_exists:
        # fail-closed:身份解析不出来就无法确定可见面。「降级为不过滤」等于
        # 把一个查不到主体的 user_id 当超管使——收口(与 CUSTOM 空集合同一判据方向)
        log.warning("data_scope: 用户不存在或已删除,fail-closed 拒绝 (user_id=%s)", user_id)
        raise DataScopeUnsupported(
            "data_scope: 用户不存在或已删除,无法确定数据范围,fail-closed 拒绝"
        )
    scopes = await repo.user_role_scopes(user_id)
    if not scopes:
        # 功能权限和数据范围共用角色启用状态；无启用角色时仍拒绝访问，避免放宽数据可见范围。
        log.warning("data_scope: 用户无启用角色,fail-closed 拒绝 (user_id=%s)", user_id)
        raise DataScopeUnsupported("data_scope: 用户无启用角色,无法确定数据范围,fail-closed 拒绝")
    return service.most_permissive(scopes), dept_id


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

    guarded(单遍复用):调用方已对该 SQL 建立的单遍解析产物;缺省时
    内部独立解析(行为不变)。
    """
    repo = SqlDataScopeRepo(session)
    scope, dept_id = await _load_scope(session, user_id)
    if scope == "ALL":
        return AppliedSQL(sql=sql)

    dept_ids = await _resolve_dept_ids(repo, scope, user_id=user_id, dept_id=dept_id)
    ctx = ScopeContext(data_scope=scope, user_id=user_id, dept_ids=dept_ids)
    # fail-closed 判据先于列探测与「主表不可定位→降级」:CUSTOM 空集合禁漂成不过滤
    reason = service.unsupported_reason(ctx)
    if reason is not None:
        raise DataScopeUnsupported(reason)

    # 列探测(DEPT 系与 CUSTOM 探 dept_id,SELF 探 create_by)
    data_type: str | None = None
    if scope in ("DEPT", "DEPT_AND_BELOW", "CUSTOM", "SELF"):
        data_type = await _probe_filter_type(
            biz_conn,
            scope,
            user_id=user_id,
            tenant_schemas=tenant_schemas,
            sql=sql,
            guarded=guarded,
        )
        if data_type is None:
            warn = f"data_scope={scope}: 无法提取主表名(UNION/子查询 FROM),降级为不过滤"
            return AppliedSQL(sql=sql, warning=warn)

    flt = service.resolve(ctx, column_exists=data_type is not None)
    if flt.type is FilterType.UNSUPPORTED:
        # 纵深防线(构造上已被上方 unsupported_reason 拦死):意图若为 UNSUPPORTED
        # 却走到这里,绝不允许继续改写——改写会退化为「不过滤」即 fail-open
        raise DataScopeUnsupported(flt.skip_reason or "DATA_SCOPE_UNSUPPORTED")
    if flt.type is FilterType.SKIP:
        if flt.skip_reason:
            log.warning("%s (user_id=%s)", flt.skip_reason, user_id)
        return AppliedSQL(sql=sql, warning=flt.skip_reason)

    return _apply_filter_sql(sql, flt, guarded=guarded, data_type=data_type)


def _apply_filter_sql(
    sql: str, flt: ScopeFilter, *, guarded: "Guarded | None", data_type: str | None
) -> AppliedSQL:
    """按过滤意图改写 SQL 并把绑定参数转成目标列类型(uuid 列绑 UUID 对象,其余绑原值)。"""
    if guarded is not None:
        new_sql, params = guarded.apply_filter(flt.type.value, flt.column, flt.values)
    else:
        new_sql, params = rewrite.apply_filter(sql, flt)
    # uuid 列绑 UUID 对象,其余列绑 str(探测 data_type 保证类型匹配)
    out_params: dict[str, object] = (
        {k: uuid.UUID(str(v)) for k, v in params.items()} if data_type == "uuid" else params
    )
    return AppliedSQL(sql=new_sql, params=out_params)
