## MODIFIED Requirements

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
