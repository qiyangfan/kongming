# 当前用户与功能权限查询设计

## Context

路由对当前身份的读取集中在用户 ID。以流式问答为例，请求先经过 `require_perm("chat:execute")`，路由再把用户 ID 交给聊天服务，聊天服务根据用户 ID 和请求头检查租户。`/auth/me` 另有自己的用途，前端依靠它显示用户资料、筛选菜单和切换租户。 [流式问答](../../../apps/api/kongming/chat/router.py) · [用户资料接口](../../../apps/api/kongming/auth/router.py) · [提案](proposal.md)

## Goals / Non-Goals

同一次请求可以让路由和权限检查共享已验证的用户 ID。功能权限检查只加载作出决定所需的账号状态和有效权限。账号禁用检查的覆盖范围按提案划定，已有租户、资源归属和数据范围检查各自执行。 [提案范围](proposal.md#non-goals)

## Decisions

### 在认证依赖里把令牌字段转换成当前用户

- 在 `auth/models.py` 定义 `AuthenticatedUser`，用 `@dataclass(frozen=True)` 声明 `user_id: str`。它表示访问令牌已通过验证且取到了用户 ID。账号状态由相应的数据库检查确认。
- `auth/deps.py` 中的 `current_user()` 沿用现有请求头读取、验签和错误响应。从验证后的 `sub` 取字符串并构造 `AuthenticatedUser`。用户 ID 按原值传递，兼容当前签发的令牌。
- 统一导出 `CurrentUser = Annotated[AuthenticatedUser, Depends(current_user)]`。路由写 `user: CurrentUser`，业务服务继续接收原有的 `user_id: str`。路由和 `require_perm()` 使用同一个依赖函数，沿用 FastAPI 在单次请求内复用依赖结果的行为。

保留字典并换成 `TypedDict` 可以让类型检查器认识 `sub`，业务代码仍要知道令牌字段名。构造 `AuthenticatedUser` 后，字段转换集中在认证依赖，查资料时仍可使用现有的 `User` 和 `MeInfo`。 [认证依赖](../../../apps/api/kongming/auth/deps.py) · [项目已有的依赖别名](../../../apps/api/kongming/auth/dependencies.py) · [现有用户模型](../../../apps/api/kongming/auth/models.py)

### 让功能权限检查读取专用的用户权限对象

- 在 `auth/models.py` 定义 `RbacUser`，包含 `user_id: str` 和 `perm_codes: frozenset[str]`。`perm_codes` 是由启用角色授予的有效权限集合。已绑定角色、用户资料和租户列表由 `MeInfo` 表达。
- 由 `AuthService.get_rbac_user(user_id)` 读取当前账号和有效权限。与 `AuthService.me()` 共用账号存在、启用且未软删的检查。两条路径都复用 `PermRepo.list_codes()`，由现有查询排除停用角色并合并多角色授予的权限。`me()` 保留现有列表字段和排序。
- `require_perm()` 用当前身份的 `user_id` 请求 `RbacUser`，检查声明的权限。账号不可用沿用 `401`。权限不足沿用 `403` 和拒绝记录，记录写入失败时仍返回 `403`。用户权限每次检查时从数据库读取，同一访问令牌的下一次请求就能反映已提交的角色或权限变更。

当前通过 `me()` 查到的四项数据中，权限检查只需要确认账号有效、取出权限集合。采用专用查询后，单次功能权限检查的正常读取由四次降为两次，分别读取账号和权限。这个数字只计算该项检查，租户、资源归属和拒绝记录各自执行。直接使用 `MeInfo` 会继续读取角色名和租户列表。把角色或权限写进令牌会使下一次权限检查依赖令牌里的旧值。 [现有查询](../../../apps/api/kongming/auth/service.py) · [有效权限来源](../../../apps/api/kongming/auth/repository.py) · [授权规格](../../specs/rbac/spec.md)

### 兼容边界以现行规格和接口为准

`/auth/me` 继续返回现有的身份、角色、权限和租户字段。刷新令牌仍由刷新流程读取，Keycloak 返回的身份字段仍在登录流程解析。认证类型是 API 进程内部对象，验收时应对照改造前的 OpenAPI、前端生成类型和实际响应。 [本地授权规格](../../specs/auth/spec.md) · [接口生成约定](../../../docs/adr/005-contract-pipeline.md)

现行规格要求禁用账号在用户资料、刷新和受功能权限保护的入口被拒绝。ADR-006 的“逐请求拦截”表述范围更宽，现有个人偏好入口只验证访问令牌和租户绑定。统一这些入口的禁用行为需要另行决定。 [账号启停规格](../../specs/rbac/spec.md#requirement-用户启用与禁用权限点-systemusermanage) · [ADR-006](../../../docs/adr/006-federation-connectors.md) · [个人偏好入口](../../../apps/api/kongming/memory/router.py)

## Risks / Trade-offs

- 路由和测试对身份字典的读取是内部调用约定。切换时应一起迁移所有调用者，并以类型检查和路由测试发现遗漏。
- 账号状态、角色变更和拒绝记录是权限检查的重要结果。优先复用原有查询和错误处理，用既有令牌测试撤销和禁用，覆盖拒绝记录及写入失败的分支。
- 查询次数是这次改造的验收指标。接口总耗时还取决于业务查询，不能根据读取次数推算整体响应提速。

## Migration Plan

1. 从实施时的工作区确认所有调用点和现行接口产物，先补针对权限读取与兼容性的测试。
2. 一次切换认证依赖、功能权限检查和所有调用点。用既有访问令牌运行改造后的 API，比较改造前后的公开接口产物。
3. 按正常 API 发布流程部署。回退时恢复上一版 API，现有数据、令牌和前端仍可使用。
