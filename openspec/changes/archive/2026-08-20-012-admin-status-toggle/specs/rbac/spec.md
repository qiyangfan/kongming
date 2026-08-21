# rbac 能力域 delta(012 管理台状态启停范式)

## MODIFIED Requirements

### Requirement: 用户启用与禁用(权限点 system:user:manage)

用户编辑支持状态切换(ACTIVE/DISABLED);禁用后该用户登录被拒,
且既有访问令牌在下一次权限点校验时被拒。系统 SHALL 提供专用启停端点
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
