# Spec Delta: rbac

## REMOVED Requirements

### Requirement: 用户管理 CRUD(权限点 system:user:manage)

**Reason**: 租户/成员/粗角色由锦官拥有；账号由 Keycloak 管，本地不再创建或修改账户资料与授权。

**Migration**: `/system/users` 写端点返回 403 SHARED_CONTROL_READONLY；用户页只读。历史本地资料保留为 last-known 缓存，由快照收敛。

### Requirement: 用户启用与禁用(权限点 system:user:manage)

**Reason**: 账户状态归 Keycloak；本地启停会形成第二写者。

**Migration**: 启停端点返回 403；角色/部门等细粒度配置仍可在服务管理台维护。
