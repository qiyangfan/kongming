# Spec Delta: 工具网关

## MODIFIED Requirements

### Requirement: 统一出站通道

所有域的外部调用 SHALL 收口于控制面统一出站通道：鉴权材料装配、超时控制、审计切面和失败分类由通道承担，域执行器只做协议适配。通道 SHALL 支持按域选择执行器；未声明专属执行器的域 SHALL 回落通道级执行器。Agent 面 SHALL 经既有 `/internal/tool/execute` 回调触发域工具，MUST NOT 直连域端点或持有域凭证。通道超时 SHALL 按域清单配置；连接超时按策略最多重试一次，读超时不重试。

#### Scenario: 按域执行器路由

- **GIVEN** 网关注册了 websearch 域并声明专属执行器
- **WHEN** 执行该域工具
- **THEN** 该域出站由其专属执行器承担(请求形态/鉴权头按域适配),未声明专属执行器的域仍走通道级执行器,既有域行为逐字段不变

#### Scenario: 域凭证只在控制面

- **GIVEN** 某域需要 API key
- **WHEN** LLM 经回调调用该域工具
- **THEN** key 仅由控制面出站通道装配使用,Agent 面请求体与观察中不含任何凭证形态

#### Scenario: 超时可配且生效

- **GIVEN** 域清单配置超时 5s,端点 8s 未响应
- **WHEN** 出站调用
- **THEN** 通道按超时中断,失败分类按超时分型:连接超时归连接类(按策略器重试语义至多重试一次),读超时归响应类(不重试)

### Requirement: web 检索域配额与授权

`websearch` 域限流配额 SHALL 独立配置，与其它已注册域的配额互不挤占。工具权限点 `tool:websearch:search` SHALL 显式授予制，不进入 DEVELOPER 推荐集，由 SUPER_ADMIN 种子自动覆盖；域治理沿用既有 `system:tool:manage` 口径，websearch 域自动出现在治理清单。

#### Scenario: 配额独立分桶

- **GIVEN** 同一用户同一分钟内调满 websearch 域配额
- **WHEN** 该用户调用另一个已注册域的工具
- **THEN** 另一域按自身配额独立判定，不因 websearch 域配额用尽而限流

#### Scenario: 显式授予制

- **GIVEN** 默认角色用户未显式授予 `tool:websearch:search`
- **WHEN** 发起问答
- **THEN** `web_search` 不在其授权注入集；显式授予后可见可用

### Requirement: 既有域零回归

HTTP 连接器域接入 MUST NOT 改变既有域（websearch）与内置工具的行为：注入、授权、执行路径和审计形态逐字段一致。未注册连接器的租户 MUST 与“能力从未存在”不可区分。

#### Scenario: 既有域行为一致

- **GIVEN** 任意既有域工具的任一场景
- **WHEN** HTTP 连接器域引入后执行
- **THEN** 行为与引入前逐字段一致

## REMOVED Requirements

### Requirement: 迁移与零回归

该要求只记录 089 将历史 `knowledge_search` 收编为网关首域的迁移过程，不再是当前系统行为契约。
