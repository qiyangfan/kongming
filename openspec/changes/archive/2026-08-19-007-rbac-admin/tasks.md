# 任务:RBAC 管理面(007)

## 1. api:system 域骨架与安全规则(测试先行)

- [x] 1.1 新建 `kongming/system/` 域包(models 零框架 dataclass +
  service 领域错误 + ports 不需要——直接 repository 注入,沿用 chat 域
  直连模式),pyproject import-linter 补 system.models 零框架契约
- [x] 1.2 失败测试先行:保护规则(test_system_admin.py)——内置角色禁删/
  role_key 不可改/超管禁删/自删禁(409;role_key→400)/角色有绑定禁删/
  部门有子或有人禁删/无权限访问全端点族 403
- [x] 1.3 失败测试先行:require_perm 403 落操作审计(perm_denied);
  禁用用户既有令牌下次受保护请求 401(先核实 me() 现状再补测试)
- [x] 1.4 实现 repository+service 使保护规则测试转绿

## 2. api:管理端点族

- [x] 2.1 用户端点:GET /system/users(分页+关键字+聚合角色/部门)+
  POST /system/users(argon2id+首登标志+绑定)——履约测试(创建/无权限
  403/软删名可复用)
- [x] 2.2 用户端点:PUT /system/users/{id}(可选密码/状态启停)+
  reset-password(标志复位)+ DELETE(软删)——履约测试
- [x] 2.3 角色端点:GET/POST/PUT/DELETE /system/roles + POST
  /{id}/permissions(覆盖式,键缺失 400)+ GET /system/permissions
  (平铺清单)——履约测试(含覆盖式绑定/创建冲突)
- [x] 2.4 部门端点:GET /system/depts(树)+ POST/PUT/DELETE——履约测试
  (树形/防成环/前置校验已在 1.2)
- [x] 2.5 GET /system/tenants 只读清单;main.py 注册 system 路由;
  ruff+mypy+import-lints 子集绿

## 3. 契约与 web

- [x] 3.1 `make contracts` 重导出 OpenAPI + 生成前端类型,tsc 通过
- [x] 3.2 web:管理布局(子导航数据驱动+页面级 PermRoute 守卫)+
  路由 /admin/users /admin/roles /admin/depts(403 守卫测试路径)
- [x] 3.3 web:用户管理页(表格/分页/搜索/创建编辑对话框/重置密码/
  删除确认/状态启停)
- [x] 3.4 web:角色管理页(表格/创建编辑/权限绑定面板按命名空间分组/
  内置角色删除禁用)
- [x] 3.5 web:部门管理页(树形/创建编辑删除/失败反馈);写操作反馈
  统一(成功刷新/失败错误条/提交中禁用)
- [x] 3.6 web 新增 shadcn 组件核对 index.css 语义类(006 经验:
  v4 基线三件套);tsc+eslint 绿

## 4. 收口

- [x] 4.1 `make check` 全绿(api+agent+web 全门禁)
- [x] 4.2 Playwright 冒烟:登录→管理面三页全链路(建用户→分角色→绑部门
  →重置密码→禁用→删除;角色权限覆盖式;部门树 CRUD;403 守卫)
- [x] 4.3 独立评审循环(安全面重点:保护规则绕过形态/越权/审计),
  发现项闭环
