/* 部门树展平(单源):DeptsPage 的父部门候选与 RolesPage 的自定义部门多选共用。 深度作缩进档位由调用方决定,本函数只给结构事实。 */

import type { DeptNodeOut } from "@kongming/api-client/system-api";

type FlatDept = { id: string; label: string; depth: number };

export function flattenDeptTree(
  nodes: DeptNodeOut[] | undefined,
  depth = 0,
): FlatDept[] {
  return (nodes ?? []).flatMap((n) => [
    { id: n.id, label: n.dept_name, depth },
    ...flattenDeptTree(n.children, depth + 1),
  ]);
}
