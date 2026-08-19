# 设计:RBAC 管理面(007)

## Context

存储底座齐备(0001 baseline 的 sys_user/sys_role/sys_permission/sys_user_role/
sys_role_permission/sys_user_tenant + 0003 的 sys_dept 与 sys_user.dept_id),
权限点守卫(require_perm,实时联查)随 002/005 已验证;缺管理端点与界面。
上游参考(SystemController)提供了端点形态基线,但其缺失的保护规则
(内置角色防删/自删保护/删除前置校验/启停)按 Kongming spec delta 补齐,
属新增设计而非照搬。

## Goals / Non-goals

- Goals:管理面端到端可用(建号→分角色→绑部门→重置密码→禁用/删除全链路);
  安全规则测试先行;前端类型全生成(ADR-005)。
- Non-goals(设计层):不引入缓存/失效广播——权限校验本就实时联查,
  绑定变更天然立即生效;不做乐观锁/审计 diff 明细(操作日志记动作+目标即可)。

## Decisions

### D1:新域包 `kongming/system/`,复用 auth 域模型与守卫(而非扩 auth 域)

- 结构:router(薄适配+Pydantic 模型)→ service(用例+领域错误)→
  models(纯 dataclass,零框架)→ repository(SQLAlchemy 显式 SQL,同 auth
  域约定「SQL 即文档」)。
- 依据:auth 域已 1100+ 行且职责是「认证流程」;管理 CRUD 是独立用例族。
  宪法 Article II 分层照旧;import-linter 补 system.models 零框架契约。
- 备选:扩 auth 域(否——单一域膨胀,评审面变大)。
- 共享:current_user/require_perm 从 auth.router import(chat 域同款先例);
  领域 dataclass(User/Role)直接复用 auth.models,不复制。

### D2:端点形态(上游基线 + Kongming 化)

| 端点 | 权限点 | 说明 |
|---|---|---|
| GET /system/users?page&size&keyword | system:user:manage | 分页+关键字(用户名/昵称 ILIKE,过滤软删),创建时间倒序;列表项聚合 role_keys/dept_name/status |
| POST /system/users | 同上 | username/password/nickname/email/status/dept_id/tenant_id/role_ids |
| PUT /system/users/{id} | 同上 | 同上字段;username 忽略不改(上游先例);密码可选,不传不改 |
| POST /system/users/{id}/reset-password | 同上 | 复位首登改密标志 |
| DELETE /system/users/{id} | 同上 | 软删;admin/自身 409 |
| GET /system/roles | system:role:manage | 全量不分页(角色量小,上游同款) |
| POST /system/roles / PUT /system/roles/{id} | 同上 | role_key 编辑忽略不改(400 语义用忽略实现,见 D4) |
| DELETE /system/roles/{id} | 同上 | 有未软删用户绑定 409;内置 409 |
| POST /system/roles/{id}/permissions | 同上 | {perm_ids} 覆盖式;键缺失 400(上游防呆先例) |
| GET /system/permissions | system:role:manage | 平铺,sort 升序 |
| GET /system/depts | system:user:manage | 树(仓库递归组装,datascope 已有递归 CTE 先例) |
| POST /system/depts / PUT /system/depts/{id} / DELETE /system/depts/{id} | 同上 | 删除前置校验 409 |
| GET /system/tenants | system:user:manage | 只读清单 |

- 用户-角色绑定嵌在创建/编辑(上游形态),不设独立绑定端点——spec
  「给用户分配角色」以提交绑定履约。
- 备选:REST 全嵌套资源 /users/{id}/roles(否——多一次往返,管理面
  表单是整单提交,嵌套字段更贴合)。

### D3:软删与唯一性沿用现有约定

用户名唯一校验=查询过滤 deleted_at(软删名可复用,spec 已立约);
角色 role_key 唯一:角色不做软删(上游 sys_role 有 del_flag,本仓
baseline 无 deleted_at 列,且角色删除已有前置校验挡悬空绑定)——
**角色/部门/权限点物理删除,仅用户软删**。备选:角色也软删
(否——无列可依,需新迁移;且软删角色仍参与绑定联查,语义更浑浊)。

### D4:保护规则的错误语义

- 删除类保护(内置角色/超管/自删/角色有绑定/部门有子或有人)→ 409 +
  语义 detail;编辑 role_key 不同→ 400(表单字段级错误,前端提示定位)。
- 判内置=SUPER_ADMIN/DEVELOPER role_key 集合(代码常量,种子契约);
  判超管=Settings.admin_username(与种子同源,不引入新列)。
- 备选:sys_role 加 built_in 列(否——role_key 集合即契约,加列是
  冗余真理源;改名保护已有 role_key 不可改兜底)。

### D5:禁用用户的令牌即时失效

require_perm→svc.me 实时查库;me() 对 DISABLED/软删用户抛 TokenInvalid
(现有登录链路已拒,me 侧补同一判定),效果=既有 access token 在下一次
受保护请求即 401。不引入 token 黑名单(过度设计)。
需核实 me 现状:若已拒则零改动,只在测试锁定。

### D6:require_perm 403 审计

_dep 拒绝路径补 operlog.add(action="perm_denied", target=perm_code,
detail={path});失败不阻断拒绝本身。审计写入沿用 OperLogRepo 短事务
语义(auth 域同款)。

### D7:web 管理面结构

- /admin 布局组件(管理子导航:用户/角色/部门,数据驱动同 nav.ts 模式,
  页面权限点守卫复用 PermRoute);三页各自 react-query 数据获取。
- 表格:shadcn table + 现有 badge/button/dialog/input 等组件;分页控件
  自组合(上一页/下一页+页码显示)。新经 CLI 引入的组件必须核对
  index.css 语义类(HANDOFF 006 经验:v4 基线三件套)。
- 表单对话框:受控表单+提交中禁用;错误信息展示在对话框顶部。
- 备选:DataTable 三方库(否——技术栈锁定,shadcn 官方 table 足够)。

### D8:测试策略(安全类先行)

- 先写失败测试:保护规则(内置/自删/前置校验/role_key 不可改/403 审计/
  禁用令牌失效)→ 再实现。
- 履约测试:CRUD 正向路径+覆盖式绑定+唯一性+分页过滤。
- 复用 conftest 真实 PG+Redis 夹具;权限点守卫用例沿用 chat 域套路
  (无权限角色用户→403)。

## Risks / Trade-offs

- [管理面写操作无二级确认误操作] → 删除/重置密码走确认对话框(前端),
  端点侧保护规则兜底不可逆破坏(自删/超管删)。
- [角色物理删除丢历史] → 删除前置校验+操作日志留痕;绑定表级联清理
  (role 删除时同步删 sys_user_role/sys_role_permission 行,事务内)。
- [用户列表聚合查询 N+1] → 单 SQL 聚合(LEFT JOIN + json_agg 或二次
  批查,实现取简,列表页 size≤100 量级)。
- [禁用用户在线体验] → 下次请求 401 跳登录;无主动踢下线(Non-goal 已声明)。

## Migration Plan

**实现期修正(0004 迁移)**:提案初判「无数据库迁移」,实现时测试实证
baseline 的 username UNIQUE 是全表硬约束——软删记录仍占名,spec
「用户名唯一性(软删过滤)」在 schema 层不可达。新增 0004 迁移:硬约束
换部分唯一索引(`WHERE deleted_at IS NULL`);auth 仓储 get_by_username
同步补软删过滤(同名双行曾会炸 one_or_none)。

部署=代码发布+0004 迁移;回滚=回退镜像+downgrade(约束形态还原,数据无损)。
启动种子不变(权限点零新增)。

## Open Questions

- 部门树查询租户过滤:当前全量返回(部门带 tenant_id 列);管理面
  是否按租户过滤部门树,随 multitenant 管理变更再定(不改变本变更端点)。
