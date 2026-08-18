"""data_scope 应用编排:上下文汇聚(角色/部门/列探测)→ 意图 → 改写。

对应上游 DataScopeApplier 的职责落点(infrastructure 性质):
- 用户不存在/无启用角色 → 安全降级(沿用上游裁定,告警可观测);
- ALL 提前退出(最常见路径,不做列探测);
- DEPT 系先解有效部门集(DEPT 单部门 / DEPT_AND_BELOW 递归 CTE);
- CUSTOM/未知值 → DataScopeUnsupported(fail-closed,spec 铁律);
- 绑定值按探测列类型绑 UUID/str(PG 无 uuid=text 算子)。
"""

import logging
import uuid

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

log = logging.getLogger(__name__)


async def apply(
    session: AsyncSession,
    biz_conn: AsyncConnection,
    *,
    user_id: str,
    tenant_schemas: list[str],
    sql: str,
) -> AppliedSQL:
    """按 user_id 的有效 data_scope 改写 sql;降级带 warning,fail-closed 抛异常。"""
    repo = SqlDataScopeRepo(session)

    user_exists, dept_id = await repo.user_dept_id(user_id)
    if not user_exists:
        warn = "data_scope: 用户不存在或已删除,降级为不过滤"
        log.warning("%s (user_id=%s)", warn, user_id)
        return AppliedSQL(sql=sql, warning=warn)

    scopes = await repo.user_role_scopes(user_id)
    if not scopes:
        warn = "data_scope: 用户无启用角色,降级为不过滤"
        log.warning("%s (user_id=%s)", warn, user_id)
        return AppliedSQL(sql=sql, warning=warn)

    scope = service.most_permissive(scopes)
    if scope == "ALL":
        return AppliedSQL(sql=sql)

    # DEPT 系解有效部门集;SELF 无需部门
    dept_ids: list[str] = []
    if scope == "DEPT" and dept_id is not None:
        dept_ids = [dept_id]
    elif scope == "DEPT_AND_BELOW" and dept_id is not None:
        dept_ids = await repo.dept_and_below(dept_id)

    # 列探测(仅 DEPT 系/SELF 需要;CUSTOM/未知值直接交 resolve 判 UNSUPPORTED)
    data_type: str | None = None
    if scope in ("DEPT", "DEPT_AND_BELOW", "SELF"):
        target_column = COL_DEPT_ID if scope in ("DEPT", "DEPT_AND_BELOW") else COL_CREATE_BY
        ref = rewrite.extract_main_table_ref(sql)
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

    flt = service.resolve(
        ScopeContext(data_scope=scope, user_id=user_id, dept_ids=dept_ids),
        column_exists=data_type is not None,
    )
    if flt.type is FilterType.UNSUPPORTED:
        raise DataScopeUnsupported(flt.skip_reason or "DATA_SCOPE_UNSUPPORTED")
    if flt.type is FilterType.SKIP:
        if flt.skip_reason:
            log.warning("%s (user_id=%s)", flt.skip_reason, user_id)
        return AppliedSQL(sql=sql, warning=flt.skip_reason)

    new_sql, params = rewrite.apply_filter(sql, flt)
    if data_type == "uuid":
        # uuid 列绑 UUID 对象,其余列绑 str(探测 data_type 保证类型匹配)
        params = {k: uuid.UUID(str(v)) for k, v in params.items()}
    return AppliedSQL(sql=new_sql, params=params)
