# rbac 能力域 delta(003)

## RENAMED Requirements

- FROM: `### Requirement: OAuth(GitHub)账号权限一致`
- TO: `### Requirement: 联邦账号权限一致`

## MODIFIED Requirements

### Requirement: 联邦账号权限一致

联邦登录的账号(首次自动建号并绑定默认角色与租户)在权限体系内 SHALL 与本地账号完全一致;
该契约对任意已配置 provider(GitHub/Google/企微/钉钉/飞书)一致成立(ADR-006 五路联邦)。

#### Scenario: OAuth 首次登录自动建号
- GIVEN 系统配置了任一联邦 provider(未配置时前端隐藏该入口,不影响账密登录)
- WHEN 用户经该 provider 登录回调
- THEN 无本地账号则自动创建(绑定默认角色与默认租户),已有则直接匹配(不重建)
- AND 操作日志记录注册/登录事件
- AND 该用户权限点校验、行级 data_scope、租户白名单与本地账密用户无差异
