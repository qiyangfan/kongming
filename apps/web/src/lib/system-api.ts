/* system 管理面端点封装(007)。类型一律取生成契约(ADR-005,禁手写)。 */

import type { components } from "./api-types.gen";
import { api } from "./api";

export type UserPageOut = components["schemas"]["UserPageOut"];
export type UserItemOut = components["schemas"]["UserItemOut"];
export type UserCreateIn = components["schemas"]["UserCreateIn"];
export type UserUpdateIn = components["schemas"]["UserUpdateIn"];
export type RoleOut = components["schemas"]["RoleOut"];
export type RoleCreateIn = components["schemas"]["RoleCreateIn"];
export type RoleUpdateIn = components["schemas"]["RoleUpdateIn"];
export type PermOut = components["schemas"]["PermOut"];
export type DeptNodeOut = components["schemas"]["DeptNodeOut"];
export type DeptCreateIn = components["schemas"]["DeptCreateIn"];
export type DeptUpdateIn = components["schemas"]["DeptUpdateIn"];
export type TenantOut = components["schemas"]["TenantOut"];

export type UserStatusValue = components["schemas"]["UserCreateIn"]["status"];
export type DataScopeValue = components["schemas"]["RoleCreateIn"]["data_scope"];
export type SqlScopeValue = components["schemas"]["RoleCreateIn"]["sql_scope"];

export type CreatedId = { id: string };

export const systemApi = {
  listUsers: (page: number, size: number, keyword: string) =>
    api.get<UserPageOut>(`/system/users?page=${page}&size=${size}&keyword=${encodeURIComponent(keyword)}`),
  createUser: (body: UserCreateIn) => api.post<CreatedId>("/system/users", body),
  updateUser: (id: string, body: UserUpdateIn) => api.put<void>(`/system/users/${id}`, body),
  resetPassword: (id: string, newPassword: string) =>
    api.post<void>(`/system/users/${id}/reset-password`, { new_password: newPassword }),
  deleteUser: (id: string) => api.delete<void>(`/system/users/${id}`),

  listRoles: () => api.get<RoleOut[]>("/system/roles"),
  createRole: (body: RoleCreateIn) => api.post<CreatedId>("/system/roles", body),
  updateRole: (id: string, body: RoleUpdateIn) => api.put<void>(`/system/roles/${id}`, body),
  deleteRole: (id: string) => api.delete<void>(`/system/roles/${id}`),
  bindRolePerms: (id: string, permIds: string[]) =>
    api.post<void>(`/system/roles/${id}/permissions`, { perm_ids: permIds }),
  listPerms: () => api.get<PermOut[]>("/system/permissions"),

  listDepts: () => api.get<DeptNodeOut[]>("/system/depts"),
  createDept: (body: DeptCreateIn) => api.post<CreatedId>("/system/depts", body),
  updateDept: (id: string, body: DeptUpdateIn) => api.put<void>(`/system/depts/${id}`, body),
  deleteDept: (id: string) => api.delete<void>(`/system/depts/${id}`),

  listTenants: () => api.get<TenantOut[]>("/system/tenants"),
};
