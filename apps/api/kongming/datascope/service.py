"""data_scope 意图计算:纯逻辑,零框架依赖(宪法 Article II)。

沿用上游「领域只产意图」分层:输入有效 scope 上下文 + 目标列存在性,
输出 ScopeFilter;改写与探测分别在 rewrite/repository。
安全权衡(spec 立约):无列/未绑定部门 → SKIP 安全降级;
CUSTOM 空集合与未知值 → UNSUPPORTED fail-closed,禁 fail-open。
"""

from collections.abc import Iterable

from kongming.datascope.models import (
    COL_CREATE_BY,
    COL_DEPT_ID,
    PERMISSIVENESS,
    FilterType,
    ScopeContext,
    ScopeFilter,
)


def most_permissive(scopes: Iterable[str]) -> str:
    """多角色取最宽松(rbac spec):按 PERMISSIVENESS 序取最小;未知值视为最严兜底。

    未知值(库 CHECK 之外的脏数据)不静默放过——排最末使其仅在全是未知值时
    当选,随后 resolve 按 UNSUPPORTED fail-closed。
    """
    return min(scopes, key=lambda s: PERMISSIVENESS.get(s, len(PERMISSIVENESS)))


def unsupported_reason(ctx: ScopeContext) -> str | None:
    """无需列信息即可判定的 fail-closed 原因(CUSTOM 空集合 / 未知值);None=可继续。

    单点判据:applier 在「主表不可定位→降级」之前先问它——CUSTOM 空集合若走到
    降级分支就漂成了不过滤(fail-open,spec 明令禁止)。
    """
    scope = ctx.data_scope
    if scope not in PERMISSIVENESS:
        return f"未知 data_scope 值: {scope}"
    if scope == "CUSTOM" and not ctx.dept_ids:
        return "data_scope=CUSTOM(自定义)但角色未配置部门集合,fail-closed 拒绝"
    return None


def resolve(ctx: ScopeContext, column_exists: bool) -> ScopeFilter:
    """计算行级过滤意图。

    column_exists=目标表是否含过滤列(DEPT 系与 CUSTOM 探 dept_id,SELF 探 create_by)。
    """
    scope = ctx.data_scope
    if scope == "ALL":
        return ScopeFilter(type=FilterType.SKIP)
    reason = unsupported_reason(ctx)
    if reason is not None:
        return ScopeFilter(type=FilterType.UNSUPPORTED, skip_reason=reason)
    if scope == "CUSTOM":
        if not column_exists:
            return ScopeFilter(
                type=FilterType.SKIP,
                skip_reason=f"目标表无 {COL_DEPT_ID} 列,data_scope=CUSTOM 降级为不过滤",
            )
        # 与 DEPT 系同源 IN 语义:等价于「部门 IN (角色显式集合)」,不递归子部门
        return ScopeFilter(type=FilterType.DEPT_IN, column=COL_DEPT_ID, values=ctx.dept_ids)
    if scope in ("DEPT", "DEPT_AND_BELOW"):
        if not column_exists:
            return ScopeFilter(
                type=FilterType.SKIP,
                skip_reason=f"目标表无 {COL_DEPT_ID} 列,data_scope={scope} 降级为不过滤",
            )
        if not ctx.dept_ids:
            return ScopeFilter(
                type=FilterType.SKIP,
                skip_reason=f"用户未绑定部门,data_scope={scope} 降级为不过滤",
            )
        return ScopeFilter(type=FilterType.DEPT_IN, column=COL_DEPT_ID, values=ctx.dept_ids)
    if scope == "SELF":
        if not column_exists:
            return ScopeFilter(
                type=FilterType.SKIP,
                skip_reason=f"目标表无 {COL_CREATE_BY} 列,data_scope=SELF 降级为不过滤",
            )
        return ScopeFilter(type=FilterType.USER_EQ, column=COL_CREATE_BY, values=[ctx.user_id])
    return ScopeFilter(type=FilterType.UNSUPPORTED, skip_reason=f"未知 data_scope 值: {scope}")
