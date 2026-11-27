/* 角色数据范围与自定义部门集合的出站口径(纯函数,web 测试网覆盖 src/lib/**)。
   与服务端 system/service 的搭配规则同构:让用户不必等一个 400 才知道写错了。 */

import type { DataScopeValue } from "@kongming/api-client/system-api";

const CUSTOM_SCOPE: DataScopeValue = "CUSTOM";

/** 仅 CUSTOM 携带部门集合字段;非 CUSTOM 不发(服务端对「非 CUSTOM 带集合」显式拒绝)。 */
export function deptIdsForScope(
  scope: DataScopeValue,
  deptIds: string[],
): string[] | undefined {
  return scope === CUSTOM_SCOPE ? deptIds : undefined;
}

/** 提交前的搭配校验:返回错误文案,null 表示可提交。 */
export function deptScopeError(
  scope: DataScopeValue,
  deptIds: string[],
): string | null {
  if (scope === CUSTOM_SCOPE && deptIds.length === 0) {
    return "自定义范围需至少选择一个部门";
  }
  return null;
}
