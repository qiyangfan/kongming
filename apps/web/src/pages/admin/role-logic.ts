/* 角色的表单与展示纯逻辑:范围选项、回显文案、权限点分组与表单校验。 */

import type { DataScopeValue, PermOut, RoleOut, SqlScopeValue } from "@/lib/system-api";

export const DATA_SCOPES: { value: DataScopeValue; label: string }[] = [
  { value: "ALL", label: "全部数据" },
  { value: "CUSTOM", label: "自定义部门" },
  { value: "DEPT_AND_BELOW", label: "本部门及以下" },
  { value: "DEPT", label: "仅本部门" },
  { value: "SELF", label: "仅本人" },
];

export const SQL_SCOPES: { value: SqlScopeValue; label: string }[] = [
  { value: "SELECT_ONLY", label: "只读查询" },
  { value: "READ_DDL", label: "只读+可改表结构" },
  { value: "DML_DDL", label: "可读写+改表结构" },
];

export type RoleForm = {
  roleKey: string;
  roleName: string;
  dataScope: DataScopeValue;
  sqlScope: SqlScopeValue;
  sort: number;
  /** dataScope=CUSTOM 的自定义部门集合(其余范围不显示、不发) */
  deptIds: string[];
};

export const EMPTY_ROLE_FORM: RoleForm = {
  roleKey: "",
  roleName: "",
  dataScope: "SELF",
  sqlScope: "SELECT_ONLY",
  sort: 0,
  deptIds: [],
};

/** 数据范围列回显:CUSTOM 带上集合规模(未配置的坏数据也一眼可见) */
export function scopeLabel(row: RoleOut): string {
  const base = DATA_SCOPES.find((d) => d.value === row.data_scope)?.label ?? row.data_scope;
  if (row.data_scope !== "CUSTOM") return base;
  return `${base} · ${(row.data_scope_dept_ids ?? []).length} 部门`;
}

/** 权限点按 permCode 首段(命名空间)分组(rbac spec「权限点清单查询」) */
export function groupPerms(perms: PermOut[]): [string, PermOut[]][] {
  const groups = new Map<string, PermOut[]>();
  for (const p of perms) {
    const ns = p.perm_code.split(":")[0] ?? "other";
    groups.set(ns, [...(groups.get(ns) ?? []), p]);
  }
  return [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0]));
}

/** 角色表单转接口负载:自定义部门仅在 CUSTOM 范围下携带。 */
export function rolePayload(
  form: RoleForm,
  deptIdsForScope: (scope: DataScopeValue, ids: string[]) => string[] | undefined,
) {
  return {
    role_key: form.roleKey,
    role_name: form.roleName,
    data_scope: form.dataScope,
    sql_scope: form.sqlScope,
    sort: form.sort,
    data_scope_dept_ids: deptIdsForScope(form.dataScope, form.deptIds),
  };
}

/** 提交前校验:返回首条错误文案,通过返回空串。 */
export function roleFormError(
  form: RoleForm,
  creating: boolean,
  deptScopeError: (scope: DataScopeValue, ids: string[]) => string | null,
): string {
  if (creating && !form.roleKey.trim()) return "角色键不可为空";
  if (!form.roleName.trim()) return "角色名称不可为空";
  return deptScopeError(form.dataScope, form.deptIds) ?? "";
}

/** 角色行回填为表单(未知范围值按原值透出,由服务端校验)。 */
export function roleToForm(r: RoleOut): RoleForm {
  return {
    roleKey: r.role_key,
    roleName: r.role_name,
    dataScope: r.data_scope as DataScopeValue,
    sqlScope: r.sql_scope as SqlScopeValue,
    sort: r.sort,
    deptIds: r.data_scope_dept_ids ?? [],
  };
}
