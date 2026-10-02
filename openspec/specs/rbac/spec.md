# rbac 能力域

## Purpose

用户/角色/权限的模型与管理:角色来源真实化、用户/角色/权限点 CRUD、权限点校验机制、多角色语义、部门支撑、OAuth 账号一致性、种子数据。
认证流程本身(JWT 签发/登录/GitHub 联邦)属 auth 域(ADR-003 自建认证,与实现同步编写)。

data_scope 枚举见 datascope 域;sql_scope 枚举: SELECT_ONLY / READ_DDL / DML_DDL。

## Requirements

### Requirement: 角色来源真实化

权限判定 SHALL 基于 sys_user_role 真实绑定(SHALL NOT 用户名硬编码);认证与授权分离。

#### Scenario: 登录后令牌携带真实角色
- GIVEN sys_user_role 存在绑定,角色 role_key='DEVELOPER'
- WHEN 用户登录
- THEN 权限判定基于真实角色绑定
- AND 后续权限点校验基于此角色

#### Scenario: 用户无任何角色绑定
- GIVEN 用户无角色绑定
- WHEN 登录
- THEN 登录本身成功(认证与授权分离)
- AND 访问任何受权限点保护的接口返回 403

### Requirement: 权限点校验机制

接口按权限点保护(命名 `命名空间:资源:动作`);校验基于 角色→角色权限绑定→权限点 联查,且联查必须 (MUST) 只计入**启用**(`status='ACTIVE'`)角色——非启用角色不得授予任何权限点;绑定变更立即生效。此闸与 `datascope` 侧「取全部启用角色」的既有口径同轴:同一个 `sys_role.status` 禁止 (MUST NOT) 一半授权路径读、一半不读,否则「禁用角色」会让功能权限保留而行级数据范围解析为空(进而被降级为不过滤),即治理动作反向放宽可见面。

#### Scenario: 有权限放行
- GIVEN 接口声明权限点 nl2sql:execute:run,当前用户角色含此权限点
- WHEN 调用
- THEN 正常执行

#### Scenario: 无权限拒绝
- GIVEN 同上但用户角色不含该权限点
- WHEN 调用
- THEN 返回 403

#### Scenario: 撤销权限立即生效
- GIVEN 角色绑定了某权限点
- WHEN 管理员解绑
- THEN 该角色用户再调用对应接口返回 403,审计记录该 403

#### Scenario: 未启用角色不授予权限点
- GIVEN 用户唯一绑定的角色 `status='DISABLED'`,该角色仍绑有权限点
- WHEN 该用户读取 `/auth/me` 并请求受该权限点保护的接口
- THEN `perm_codes` 不含该权限点,接口返回 403

### Requirement: 权限点是随代码发布的契约数据

权限点 SHALL 与代码中的声明一一对应,SHALL 由应用启动时幂等播种(SHALL NOT 运行时 CRUD);运行时新增的权限点匹配不到任何声明是死数据,改删 perm_code 会静默破坏授权。

#### Scenario: 权限点清单查询
- GIVEN 权限点多条
- WHEN 查询权限点清单
- THEN 返回平铺列表(permCode/permName/sort),按 sort 升序,无层级
- AND 前端按 permCode 首段(命名空间)分组展示

#### Scenario: 绑定权限为全量同步(覆盖式)
- GIVEN 角色已绑定权限 A、B
- WHEN 提交绑定 permIds=[A]
- THEN 该角色绑定变为仅 A(事务内清空后写入目标集合)

### Requirement: 用户管理 CRUD(权限点 system:user:manage)

密码 SHALL 以 argon2id 哈希;用户名唯一性 SHALL 软删过滤;删除 SHALL 为软删;首登强制改密标志。

#### Scenario: 创建用户
- GIVEN 当前用户角色含 system:user:manage
- WHEN 创建用户(username/password/tenantId 等)
- THEN 密码 argon2id 哈希落库,首登改密标志初始为未改
- AND 返回新用户 id

#### Scenario: 无权限创建被拒
- GIVEN 角色不含 system:user:manage
- WHEN 创建
- THEN 返回 403,无新增记录

#### Scenario: 用户名唯一性(软删过滤)
- GIVEN 存在已软删的同名用户
- WHEN 创建同名新用户
- THEN 创建成功(软删记录不占用唯一约束)

#### Scenario: 编辑与重置密码
- GIVEN 用户已存在
- WHEN 编辑(不传密码则不改)或重置密码
- THEN 重置后首登改密标志复位,新密码 argon2id 哈希落库

#### Scenario: 删除用户(软删)
- GIVEN 用户存在
- WHEN 删除
- THEN 软删(置 deleted_at),该用户后续登录被拒

### Requirement: 角色管理 CRUD(权限点 system:role:manage)

角色 SHALL 含 dataScope/sqlScope;SHALL 支持角色-权限点绑定/撤销。

#### Scenario: 创建角色
- GIVEN 当前用户有 system:role:manage
- WHEN 创建角色(roleKey/roleName/dataScope/sqlScope)
- THEN 角色落库,data_scope 字段供行级改写读取

#### Scenario: data_scope 变更即时生效
- GIVEN 角色 data_scope=ALL
- WHEN 改为 SELF
- THEN 该角色用户后续查询被行级改写,只返回本人数据

### Requirement: 用户-角色绑定与多角色语义

一个用户多角色时:权限点 SHALL 取并集(任一角色授予即放行),data_scope SHALL 取最宽松(MIN 序:ALL 最宽)。

#### Scenario: 给用户分配角色
- GIVEN 用户与角色存在
- WHEN 提交角色绑定
- THEN 写入绑定,用户下次权限判定反映新角色

#### Scenario: 多角色 data_scope 取最宽松
- GIVEN 用户绑定角色 A(data_scope=ALL)与角色 B(data_scope=SELF)
- WHEN 计算有效 data_scope
- THEN 取 ALL(最宽松)

设计决策(沿用前系统架构师裁定):多角色取最宽松。依据:授权语义一致——权限点是 OR 关系,数据范围应一致「授予即兑现」;需要严格隔离时应使用不同账号;实现简单(JOIN 后取 MIN)。

### Requirement: 部门管理(支撑 data_scope)

部门管理 SHALL 简化实现,支撑 DEPT/DEPT_AND_BELOW 过滤;部门树 SHALL 用数据库递归 CTE 查询;用户 SHALL 绑定部门。

#### Scenario: 部门树递归查询
- GIVEN 部门层级 A→B→C
- WHEN 查询「A 及以下」
- THEN 返回 [A, B, C](递归 CTE)

#### Scenario: 用户绑定部门
- GIVEN 用户编辑界面
- WHEN 设置部门
- THEN 用户部门更新,后续 DEPT/DEPT_AND_BELOW 过滤基于此部门

### Requirement: 联邦账号权限一致

联邦登录的账号(首次自动建号并绑定默认角色与租户)在权限体系内 SHALL 与本地账号完全一致;
该契约对任意已配置 provider(GitHub/Google/企微/钉钉/飞书)一致成立(ADR-006 五路联邦)。

#### Scenario: OAuth 首次登录自动建号
- GIVEN 系统配置了任一联邦 provider(未配置时前端隐藏该入口,不影响账密登录)
- WHEN 用户经该 provider 登录回调
- THEN 无本地账号则自动创建(绑定默认角色与默认租户),已有则直接匹配(不重建)
- AND 操作日志记录注册/登录事件
- AND 该用户权限点校验、行级 data_scope、租户白名单与本地账密用户无差异

### Requirement: sql_scope 字段语义

角色 SHALL 含 sql_scope 字段;SQL 防火墙 SHALL 按当前用户角色的 sql_scope 校验语句类型。

#### Scenario: SELECT_ONLY 全局策略
- GIVEN 角色sql_scope=SELECT_ONLY
- WHEN 防火墙校验
- THEN 仅允许只读查询;该字段为更宽 SQL 权限预留扩展

### Requirement: 种子数据

启动时 SHALL 幂等播种最小可用数据:内置角色(SUPER_ADMIN/DEVELOPER)、权限点(与代码声明同源)、超管账号(从环境变量读初始密码)。

#### Scenario: 空库启动播种
- GIVEN 空库首次启动
- WHEN 应用初始化
- THEN 内置角色与权限点与代码声明一致;超管账号密码从环境变量读取并 argon2id 哈希落库
- AND 明文密码不入库、不入版本库(仅存 .env)
- AND 环境变量无值时用不安全默认值并强制首登改密
- AND 重复启动幂等(不重复播种、不覆盖已改数据)

### Requirement: 角色继承(可选,评估后定)

角色继承(子角色合并父角色权限)SHALL 标注可选,实现前评估复杂度(企业实践坑多,前系统亦未启用)。

#### Scenario: 父角色权限自动继承(若实现)
- GIVEN 角色 B 继承角色 A
- WHEN 用户绑定角色 B
- THEN 权限校验合并 A+B 权限(data_scope 取更严格)

### Requirement: 用户列表查询(权限点 system:user:manage)

SHALL 分页返回未软删用户;SHALL 支持关键字对用户名/昵称不区分大小写包含匹配;
列表项 SHALL 聚合展示角色键列表、部门名、状态、创建时间。

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

用户编辑 SHALL 支持状态切换(ACTIVE/DISABLED);禁用后该用户登录 SHALL 被拒,
且既有访问令牌 SHALL 在下一次权限点校验时被拒。系统 SHALL 提供专用启停端点
(enable/disable),保护规则与编辑路径同构;禁用 SHALL 同时吊销该用户
全部 refresh 家族(与改密踢下线对称,任一禁用路径生效)。

#### Scenario: 禁用用户后令牌立即失效
- GIVEN 用户持有有效访问令牌
- WHEN 管理员将其状态改为 DISABLED(编辑或专用禁用端点)
- THEN 该用户再登录被拒
- AND 以既有令牌访问受权限点保护的接口返回 401/403

#### Scenario: 禁用即吊销 refresh 家族
- GIVEN 用户持有有效 refresh token
- WHEN 管理员禁用该用户(任一路径)
- THEN 该用户全部 refresh 家族被吊销,旧 refresh token 不可再换新访问令牌
- AND 启用操作不涉及吊销

#### Scenario: 禁用自己被拒
- GIVEN 管理员编辑自己的资料或调用禁用端点指向自己
- WHEN 将自己的状态改为 DISABLED
- THEN 返回 409,状态不变

#### Scenario: 专用启停端点
- GIVEN 当前用户角色含 system:user:manage
- WHEN 调用专用 enable/disable 端点
- THEN 状态即变更并落启停审计;无权限 403、目标不存在 404、
  超管账号禁用 409(与编辑路径保护同构)

#### Scenario: 管理员改密踢下线
- GIVEN 用户持有有效 refresh token
- WHEN 管理员重置其密码,或编辑时提交新密码
- THEN 该用户全部 refresh 家族被吊销,旧 refresh token 不可再换新访问令牌
- AND 重置路径同时复位首登改密标志,编辑改密路径不动该标志

### Requirement: 部门管理 CRUD(权限点 system:user:manage)

部门为用户管理与行级 data_scope 的支撑数据:SHALL 提供树查询(父子层级)、创建
(名称/父部门/租户/排序)、编辑、删除;删除 SHALL 有前置校验。

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

管理端点对内置数据 SHALL 有一致性保护:内置角色(SUPER_ADMIN/DEVELOPER)SHALL 禁删;
任何角色的 role_key 创建后 SHALL 不可改;超管账号 SHALL 禁删;用户 SHALL 禁删自己。
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

删除角色前 SHALL 校验不存在未软删用户的绑定;存在 SHALL 拒绝,防止悬空授权语义。

#### Scenario: 有用户绑定的角色不可删
- GIVEN 角色 R 绑定了未软删用户 U
- WHEN 删除 R
- THEN 返回 409,角色与绑定保留

#### Scenario: 仅软删用户绑定的角色可删
- GIVEN 角色 R 仅绑定了已软删用户
- WHEN 删除 R
- THEN 删除成功

### Requirement: 租户只读清单(权限点 system:user:manage)

创建/编辑用户时 MUST 选择租户;SHALL 提供全部租户的只读清单(id/键/名称),
租户的新建与编辑属 multitenant 域,不在本能力面。

#### Scenario: 查询租户清单
- GIVEN 当前用户有 system:user:manage,库中有租户若干
- WHEN 查询租户清单
- THEN 返回全部租户(id/tenant_key/tenant_name)
