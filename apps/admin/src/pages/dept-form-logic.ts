/* 部门表单的纯逻辑:空表单、父部门候选(排除自身与后代)与提交前校验。 */

import { flattenDeptTree } from "@kongming/api-client/dept-tree";
import type { DeptNodeOut } from "@kongming/api-client/system-api";

import type { DeptForm } from "./dept-dialogs";

export const EMPTY_DEPT_FORM: DeptForm = { parentId: "", deptName: "", sort: 0, tenantId: "" };

/**
 * 父部门候选:编辑时排除自身与后代(端点亦校验,前端先行避免明显环)。
 */
export function parentCandidatesOf(
  tree: DeptNodeOut[] | undefined,
  editing: DeptNodeOut | null,
): { id: string; label: string; depth: number }[] {
  const all = flattenDeptTree(tree);
  if (!editing) return all;
  const excluded = new Set<string>([editing.id]);
  const collect = (nodes?: DeptNodeOut[]) =>
    (nodes ?? []).forEach((n) => {
      excluded.add(n.id);
      collect(n.children);
    });
  collect(editing.children);
  return all.filter((d) => !excluded.has(d.id));
}

/** 校验部门表单名称;通过返回空串。 */
export function deptFormError(form: DeptForm): string {
  return form.deptName.trim() ? "" : "部门名称不可为空";
}
