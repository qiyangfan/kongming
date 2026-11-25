# 明确当前用户类型，精简功能权限查询

## Why

认证依赖把访问令牌的字段字典交给路由，业务代码遍布 `str(user["sub"])`，读不出当前用户究竟提供了哪些字段。功能权限检查还调用了 `/auth/me` 的资料查询，账号有效时读取账号、角色、权限和租户，最后只使用权限列表。

## What Changes

- 访问令牌通过验证后，在认证依赖中提取用户 ID。各路由统一通过 `CurrentUser` 接收当前身份，用 `user.user_id` 传给业务服务。
- 功能权限检查按当前请求读取账号状态和有效权限，组装只供该检查使用的 `RbacUser`。权限仍来自已启用角色，撤销授权在下一次权限检查时生效。
- 迁移所有依赖 `current_user()` 返回值的路由和测试调用点。用既有访问令牌、权限撤销、租户隔离和 `/auth/me` 回归用例核对兼容性。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

无。`auth` 和 `rbac` 已规定令牌使用、本地授权和权限撤销行为。这里调整的是后端传递身份和读取权限的方式，验收仍以现有规格为准。 [认证规格](../../../specs/auth/spec.md) · [权限规格](../../../specs/rbac/spec.md)

## Impact

- 改动集中在 API 的认证模块及引用该依赖的路由。目前检索到 25 个路由文件，实施时以当时的调用点为准。
- 客户端继续使用原有访问令牌和接口。验收时对比改动前后的 OpenAPI 和生成的前端类型。 [ADR-003 令牌签发](../../../../docs/adr/003-auth-selfhosted.md) · [ADR-005 接口生成](../../../../docs/adr/005-contract-pipeline.md)
- 数据库使用现有用户和授权表。API 继续负责认证和授权，Agent 继续通过现有内部接口调用 API。 [ADR-002 信任边界](../../../../docs/adr/002-trust-boundary.md)

## Non-goals

- 登录、刷新、登出、签名密钥、令牌字段和 Keycloak 接入沿用现有行为。 [ADR-003](../../../../docs/adr/003-auth-selfhosted.md) · [ADR-006](../../../../docs/adr/006-federation-connectors.md)
- 租户绑定、资源归属及 SQL 数据范围由各自现有检查负责。角色管理和用户资料仍使用现有接口。
- 把账号禁用检查扩展到当前仅验证访问令牌的入口，或让已开始的流式请求重新检查权限，属于另外的行为决策。
