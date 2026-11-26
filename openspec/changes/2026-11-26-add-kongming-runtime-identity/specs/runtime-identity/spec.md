# Spec Delta

## Purpose

孔明的运行时身份执行：以锦官租户/成员/粗角色快照为共享控制来源，本地执行租户解析、data_scope 与细粒度 RBAC；服务内权限码/角色/部门/团队仍归 kongming。

## ADDED Requirements

### Requirement: 身份快照本地执行

kongming SHALL 以锦官租户/成员/粗角色快照为唯一来源，在本地执行租户解析、data_scope 与细粒度 RBAC；服务内权限码/角色/部门/团队 SHALL 由 kongming 维护；跨租户访问 SHALL 404 同形；安全类快照缺失或非法 SHALL fail-closed。

#### Scenario: 租户隔离

- GIVEN 用户属于 t1
- WHEN 访问 t2 资源
- THEN 404，不泄露存在性

#### Scenario: 快照缺失

- GIVEN 无有效租户/成员快照
- WHEN 受保护请求到达
- THEN 拒绝

### Requirement: 移除共享控制写入

kongming SHALL NOT 提供租户/成员/粗角色创建或修改入口；本地角色/权限码/部门/团队 SHALL 仍可作为服务域配置管理；用户页 SHALL 只读身份视图；共享控制变更 SHALL 只发生在 jinguan；服务域配置只经本地 admin API 写入，锦官只读展示。

#### Scenario: 本地建租户被拒

- WHEN 调用本地租户/成员创建
- THEN 端点不存在或拒绝

#### Scenario: 服务域角色仍可管理

- WHEN ADMIN 编辑本地角色/权限
- THEN 允许，服务落库并审计

#### Scenario: 用户页只读

- WHEN 打开用户页
- THEN 只能查看 Keycloak subject/租户成员，不能编辑账户
