# 提案:RBAC 管理面 CRUD(007)

## Why

006 主布局落地后 `/admin` 仍是占位页:RBAC 存储底座(sys_user/sys_role/sys_dept,
0001+0003 迁移)与权限点守卫(require_perm)齐备,但无任何管理通道——超管只能靠
种子数据与手写 SQL 维护用户/角色/部门,产品「管理面」承诺未兑现。本变更是路线图
梯队 1(HANDOFF「下一步」首项)。

## What Changes

- **api 新增 system 域(用户面 /system/*,JWT+权限点)**:
  - 用户管理(权限点 system:user:manage):分页列表(关键字过滤)、创建
    (argon2id+首登改密标志+角色/租户/部门绑定)、编辑(不传密码不改;支持
    status 启用/禁用)、重置密码(标志复位)、软删。
  - 角色管理(权限点 system:role:manage):全量列表、创建、编辑、删除、
    权限点绑定(覆盖式,显式空数组=清空)、权限点平铺清单(sort 升序)。
  - 部门管理(权限点 system:user:manage,用户管理辅助数据):树查询、创建、
    编辑、删除。
  - 租户只读清单(权限点 system:user:manage,创建用户时选择用)。
- **内置数据保护规则(安全类,测试先行)**:内置角色(SUPER_ADMIN/DEVELOPER)
  禁删;role_key 创建后不可改;超管账号(admin)禁删;用户禁删自己;删除角色前
  校验无用户绑定(有则 409);删除部门前校验无子部门且无用户绑定(有则 409)。
- **require_perm 403 落操作审计**(rbac spec「撤销权限立即生效」已立约,
  当前实现缺审计,本变更补齐)。
- **web 管理面**:`/admin` 下用户/角色/部门三页(表格+对话框表单+权限绑定),
  沿用应用壳与 RBAC 菜单守卫,替换占位页。
- 无数据库迁移(存储底座已齐:sys_dept 与 sys_user.dept_id 随 0003 就位)。

## Capabilities

### New Capabilities

- `web-admin`: 管理面界面行为——/admin 路由结构、页面级权限守卫、
  用户/角色/部门管理页的结构与交互契约。

### Modified Capabilities

- `rbac`: 增补管理端点行为面——用户列表查询形态、用户启停、部门 CRUD 及其
  删除前置校验、内置数据保护规则(内置角色防删/role_key 不可改/超管防删/
  自删保护)、角色删除前置校验。既有 Requirements(用户/角色 CRUD、权限点
  清单、覆盖式绑定、多角色语义)本次为履约实现,delta 不重复立约。

## Non-goals

- **角色继承**(主 spec 标注可选,评估前不实现)。
- **租户管理 CRUD**(multitenant 域,独立变更;本次仅只读清单)。
- **权限点 CRUD**(权限点=随代码发布的契约数据,仅清单查询)。
- **菜单管理**(无菜单实体,前端按权限点显隐,web-nav 已立约)。
- **批量操作与导入导出**(批量删/Excel 导入用户等)。
- **数据源管理端点**(梯队 2,sys_datasource 相关)。
- **会话管理面**(在线用户/踢下线;refresh 家族吊销机制已可用于自助改密)。

## Impact

- **代码**:
  - api:新增 `kongming/system/` 域包(router/service/models/repository,
    按宪法分层);`auth/seed.py` 无需改动(权限点不新增——沿用
    system:user:manage / system:role:manage);`auth/router.py` 的
    require_perm 补 403 审计落库。
  - web:新增 `/admin` 三页与共享管理布局;`lib/nav.ts` 不变(入口已就位);
    类型经 `make contracts` 重新生成。
- **契约**:openapi.json 新增 /system/* 端点族(ADR-005,产物必须同步提交)。
- **ADR**:认证语义沿 ADR-003(自建 JWT+权限点);schema 沿 ADR-004 已核对
  无新列需求;不涉信任边界(全部端点在控制面,ADR-002 无影响)。
- **依赖**:无新增第三方依赖(前后端均是)。
