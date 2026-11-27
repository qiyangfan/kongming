# Design: 运行时身份执行（runtime-identity）

## Context

现有 kongming 本地管理租户/角色/部门/团队；锦官将成为租户/成员/粗角色来源。细粒度权限码/角色/部门/团队是服务域配置，不能上收。

## Goals / Non-Goals

**Goals:**
- 租户/成员/粗角色用锦官快照。
- 隔离与细粒度 RBAC 仍在本地执行。
- 服务域身份配置仍可本地管理；锦官只读展示。

**Non-Goals:**
- 不把服务权限码/角色/部门/团队上收锦官。
- 不改 SQL 防火墙。
- 不改 Keycloak/SSO。

## Decisions

1. 本地缓存锦官租户/成员/粗角色快照；执行逻辑保留。
2. 删除/禁用本地租户/成员/粗角色 CRUD 端点；保留本地角色/权限/部门/团队管理 API。
3. 用户页只读 Keycloak subject/成员；账户状态由 Keycloak 管。
4. 跨租户请求仍 404 同形；安全类快照缺失 fail-closed。
5. 服务 admin API 是服务域身份配置写入口；锦官只读展示。

## Risks / Trade-offs

- 快照与本地执行不一致 → 版本收敛 + 负向测试。
- 粗角色/细权限混淆 → 边界表 + 契约测试。
- 旧本地租户数据残留 → greenfield 重置。

## Migration Plan

greenfield：删除本地租户/成员/粗角色控制数据；保留角色/权限/部门/团队；服务从快照启动。
