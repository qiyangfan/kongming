# rbac 能力域 delta(007:管理端点行为面)

既有 Requirements(用户/角色 CRUD 基础语义、权限点清单、覆盖式绑定、多角色
语义、种子数据)本次为履约实现,不在 delta 重复立约;以下为新增行为面。

## ADDED Requirements

### Requirement: 用户列表查询(权限点 system:user:manage)

分页返回未软删用户;支持关键字对用户名/昵称不区分大小写包含匹配;
列表项聚合展示角色键列表、部门名、状态、创建时间。

#### Scenario: 分页与关键字过滤
- GIVEN 库中存在用户若干,其中用户昵称含「孔明」
- WHEN 查询用户列表 page=1 size=10 keyword=孔明
- THEN 仅返回昵称或用户名含关键字(不区分大小写)的未软删用户
- AND 响应含总条数与当前页数据,按创建时间倒序

#### Scenario: 无权限查询被拒
- GIVEN 当前用户角色不含 system:user:manage
- WHEN 查询用户列表
- THEN 返回 403,且该次拒绝落操作审计

### Requirement: 用户启用与禁用(权限点 system:user:manage)

用户编辑支持状态切换(ACTIVE/DISABLED);禁用后该用户登录被拒,
且既有访问令牌在下一次权限点校验时被拒。

#### Scenario: 禁用用户后令牌立即失效
- GIVEN 用户持有有效访问令牌
- WHEN 管理员将其状态改为 DISABLED
- THEN 该用户再登录被拒
- AND 以既有令牌访问受权限点保护的接口返回 401/403

#### Scenario: 禁用自己被拒
- GIVEN 管理员编辑自己的资料
- WHEN 将自己的状态改为 DISABLED
- THEN 返回 409,状态不变

#### Scenario: 管理员改密踢下线
- GIVEN 用户持有有效 refresh token
- WHEN 管理员重置其密码,或编辑时提交新密码
- THEN 该用户全部 refresh 家族被吊销,旧 refresh token 不可再换新访问令牌
- AND 重置路径同时复位首登改密标志,编辑改密路径不动该标志

### Requirement: 部门管理 CRUD(权限点 system:user:manage)

部门为用户管理与行级 data_scope 的支撑数据:树查询(父子层级)、创建
(名称/父部门/租户/排序)、编辑、删除;删除有前置校验。

#### Scenario: 部门树查询
- GIVEN 部门层级 A→B→C
- WHEN 查询部门树
- THEN 返回以根部门为顶的嵌套结构,子节点含于父节点

#### Scenario: 创建与编辑部门
- GIVEN 当前用户有 system:user:manage
- WHEN 创建部门(名称/父部门/排序)或编辑既有部门
- THEN 落库成功;编辑时父部门不可选自身或其后代(防成环)

#### Scenario: 删除部门的前置校验
- GIVEN 部门 X 存在子部门,或存在未软删用户绑定 X
- WHEN 删除 X
- THEN 返回 409,部门保留

### Requirement: 内置数据保护(权限点不豁免)

管理端点对内置数据有一致性保护:内置角色(SUPER_ADMIN/DEVELOPER)禁删;
任何角色的 role_key 创建后不可改;超管账号禁删;用户禁删自己。
保护规则按角色键与用户名判定(种子契约),与权限点校验独立。

#### Scenario: 删除内置角色被拒
- GIVEN 角色 role_key=SUPER_ADMIN(或 DEVELOPER)
- WHEN 删除该角色
- THEN 返回 409,角色保留

#### Scenario: 修改 role_key 被拒
- GIVEN 任意角色已存在
- WHEN 编辑时提交与原值不同的 role_key
- THEN 返回 400,role_key 不变

#### Scenario: 删除超管账号被拒
- GIVEN 超管账号(admin)存在
- WHEN 删除该账号
- THEN 返回 409,账号保留

#### Scenario: 删除自己被拒
- GIVEN 管理员已登录
- WHEN 删除自己的账号
- THEN 返回 409,账号保留

### Requirement: 角色删除前置校验(权限点 system:role:manage)

删除角色前校验不存在未软删用户的绑定;存在则拒绝,防止悬空授权语义。

#### Scenario: 有用户绑定的角色不可删
- GIVEN 角色 R 绑定了未软删用户 U
- WHEN 删除 R
- THEN 返回 409,角色与绑定保留

#### Scenario: 仅软删用户绑定的角色可删
- GIVEN 角色 R 仅绑定了已软删用户
- WHEN 删除 R
- THEN 删除成功

### Requirement: 租户只读清单(权限点 system:user:manage)

创建/编辑用户时需选择租户;提供全部租户的只读清单(id/键/名称),
租户的新建与编辑属 multitenant 域,不在本能力面。

#### Scenario: 查询租户清单
- GIVEN 当前用户有 system:user:manage,库中有租户若干
- WHEN 查询租户清单
- THEN 返回全部租户(id/tenant_key/tenant_name)
