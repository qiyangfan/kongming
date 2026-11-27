# Spec Delta: web-admin

## REMOVED Requirements

### Requirement: 管理面路由与页面守卫

**Reason**: 管理面迁出用户 web，改为独立 `apps/admin` SPA + 独立 origin/session/CSP；旧 `/admin/*` 只做 308。

**Migration**: 用户 web 删除 admin 路由；反向代理按 `${KM_ADMIN_PUBLIC_ORIGIN}` 将 `/admin/*` 308 到管理台同路径。

### Requirement: 用户管理页

**Reason**: 用户页改为只读身份视图；账户写入归 Keycloak/锦官。

**Migration**: 管理台用户页仅展示用户名/昵称/状态/角色/部门/租户；写操作入口移除。

### Requirement: 状态列行内启停范式

**Reason**: 行内启停是用户账户写路径，随用户 CRUD 退役。

**Migration**: 无替代入口；账户状态变更在 Keycloak 完成。
