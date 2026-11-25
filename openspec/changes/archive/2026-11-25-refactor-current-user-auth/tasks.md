# 当前用户和权限查询改造任务

按任务运行对应检查，实施时记录结果。权限相关用例先确认能发现问题，再修改实现。

## 1. 迁移当前用户身份

- [x] 1.1 复查 `Depends(current_user)`、`UserClaims`、直接调用和测试替换点，记录迁移清单。用同一份改造前代码生成 OpenAPI 和前端类型作为对照，保留正在编辑的文件。
      ——apply 记录:2026-11-25 盘点得 184 处参数声明、234 处 `user["sub"]` 取值、26 处导入;
      另有 billing 的 `_` 参数 9 处与 datasource/system 的 `UserClaims` 别名两处形态;
      对照基线=HEAD 已提交的 `contracts/openapi.json` 与前端类型。
- [x] 1.2 为当前用户依赖补测试。验证现有方式签发的访问令牌能得到原样的 `user_id`，缺失、无效或用途错误的令牌仍返回 `401`。先确认返回身份对象的断言能发现旧实现的字典返回。
      ——apply 记录:2026-11-25 `test_auth_current_user.py` 5 例先红(模块无 `AuthenticatedUser`)后绿。
- [x] 1.3 增加 `AuthenticatedUser` 和 `CurrentUser`，迁移各路由、权限依赖和涉及的测试。运行认证相关用例及 `mypy`、`pyright`，检查迁移清单上的调用点都通过具名字段取用户 ID。
      ——apply 记录:2026-11-25 26 个路由文件全部迁移;辅助函数(`_tenant_or_403` 等 8 文件)签名
      改收 `AuthenticatedUser`;mypy/pyright 双 0 错;`test_route_auth_coverage` 的守卫按依赖
      函数名探测,不受返回类型变化影响。

## 2. 精简功能权限检查

- [x] 2.1 先补权限查询测试，覆盖账号有效时只查询账号和有效权限、多角色权限取启用角色的并集、账号禁用或软删时拒绝。确认查询范围断言能发现旧路径多读的角色和租户。
      ——apply 记录:2026-11-25 `test_auth_rbac_user.py` 先红(`get_rbac_user` 不存在)后绿;
      查询范围由替身调用记录断言,另含真实库的多角色并集集成例(停用角色不贡献)。
- [x] 2.2 实现 `RbacUser` 和 `AuthService.get_rbac_user()`，让 `require_perm()` 使用它。复用账号有效性检查和现有权限查询。运行上一步用例，核对同一访问令牌在撤权后的下一次请求返回 `403`，权限拒绝仍写记录，记录写入报错时仍拒绝。
      ——apply 记录:2026-11-25 实现完成;账号不可用沿用 `TokenInvalid`→401(与 `me()` 同一
      检查同一异常,首版误用 `AccountDisabled` 被 `test_disabled_user_token_rejected` 拦下);
      撤权/拒绝审计/写失败分支由既有 `test_system_admin` 用例守护。
- [x] 2.3 运行 `/auth/me` 和令牌刷新用例，比较用户资料、角色、权限和租户字段，覆盖账号停用与软删、刷新轮换及登出。
      ——apply 记录:2026-11-25 `test_auth_me/test_auth_tokens/test_auth_keycloak/test_auth_seed`
      54 例全过,响应字段零变化。

## 3. 验收 API 行为与对外接口

- [x] 3.1 运行路由认证覆盖、同步问答、流式问答、租户隔离和权限管理用例。记录已授权请求放行、已认证但无功能权限返回 `403`、有功能权限仍不能访问未绑定租户或他人会话的结果。执行 `make api-check`，记录相关检查和冒烟结果。
      ——apply 记录:2026-11-25 定向 8 文件 171 例过;`make api-check` 全绿
      (1806 passed/10 skipped,覆盖率 77.34%)。附带收敛 `stream_runner.consume`
      局部变量超标(too-many-locals 16/15):在途登记产物改挂 `_StreamRun.registration`
      可变容器(frozen run 内放可变容器,与既有 `terminal` 字段同范式),59 例流式回归零红。
- [x] 3.2 运行 `make contracts` 重新生成 `contracts/openapi.json` 和 `apps/web/src/lib/api-types.gen.ts`，与任务 1.1 的对照逐项比较。存在并行改动时，在验证副本按相同代码基线比较，验收标准是这项重构没有产生接口差异。
      ——apply 记录:2026-11-25 重生成后与 HEAD 逐字节零差异,对外接口未变。
