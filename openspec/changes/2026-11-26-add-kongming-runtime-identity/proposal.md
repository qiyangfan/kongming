# Proposal: 2026-11-26-add-kongming-runtime-identity（运行时身份执行（runtime-identity））

## Why

租户/成员/粗角色改由锦官拥有；kongming 保留服务域细粒度权限/角色、部门/团队与 data_scope/租户隔离执行。

## What Changes

- 以锦官租户/成员/粗角色快照为唯一来源。
- 本地执行租户解析、data_scope、细粒度 RBAC；权限码/角色/部门/团队仍是 kongming 服务域配置。
- 移除本地租户/成员/粗角色 CRUD；保留本地角色/权限/部门/团队管理 API 与页面。
- 用户页改为只读身份视图；锦官只读展示本地服务域配置。

## Non-goals

- 不在本地创建租户/成员/粗角色。
- 不把细粒度权限码/服务内角色/部门/团队上收锦官。
- 不把 data_scope 塞进 token。
- 不做 Keycloak 用户管理/SSO。

## Capabilities

### New Capabilities

- `runtime-identity`：控制面身份快照的本地执行与隔离

### Modified Capabilities

无

## Impact

system/tenant/rbac/datascope 执行层；删除本地控制端点；不写控制面。
