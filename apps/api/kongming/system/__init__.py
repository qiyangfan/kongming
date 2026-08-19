"""system 域(007 RBAC 管理面):用户/角色/权限点/部门/租户清单的管理用例。

与 auth 域分工:auth 管「认证流程」,system 管「身份治理 CRUD」;
守卫(current_user/require_perm)复用 auth.router,模型复用 auth.models 语义。
"""
