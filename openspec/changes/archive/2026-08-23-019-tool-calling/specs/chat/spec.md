## ADDED Requirements

### Requirement: Agent 模式选择(问答端点)

问答查询端点(同步与流式)的请求 SHALL 纯增可选字段 agent_mode ∈ {recipe, react},缺省 recipe(既有 8 节点链路,零回归);agent_mode=react 时控制面 SHALL 改调 Agent 面 ReAct 内核端点,并在请求中按调用方角色集注入授权工具 schema 子集。调用方无任何工具权限点时 SHALL 注入空工具集(ReAct 退化为纯文本问答,不报错)。响应结构与既有问答端点逐字段同构;trace_id 生成与审计贯穿语义不变。

#### Scenario: 缺省 recipe 零回归

- GIVEN 问答请求不携带 agent_mode
- WHEN 提交问题
- THEN 走既有 8 节点链路,响应与行为与 019 之前一致

#### Scenario: react 模式走 ReAct 内核

- GIVEN 已登录用户含工具权限点,请求 agent_mode=react
- WHEN 提交问题
- THEN 控制面调用 Agent 面 ReAct 端点并注入该用户授权的工具 schema 集,响应字段与 recipe 同构

#### Scenario: 无工具权限的 react 退化

- GIVEN 已登录用户角色不含任何工具权限点,请求 agent_mode=react
- WHEN 提交问题
- THEN 注入空工具集,LLM 无工具可用,退化为纯文本回答(成功终态,不报错)

#### Scenario: 非法 agent_mode 值拒绝

- GIVEN 请求 agent_mode 为枚举外取值
- WHEN 提交问题
- THEN 传输层校验拒绝(422),不回调 Agent 面

### Requirement: 写工具确认端点

系统 SHALL 提供写工具确认端点:JWT 认证 + 归属校验(仅待确认调用所属会话的发起人可确认/拒绝,他人操作拒绝且不暴露存在性,并落拒绝审计)。确认请求携带确认标识与决策(confirm/reject);确认时控制面 SHALL 按待确认记录重放该工具执行,防御全量复校(用户-租户当前绑定/工具权限点/参数 JSON Schema 三项,适用人工确认路径;限流不适用),执行结果落审计并以 assistant 消息形式落执行回执(persist 语义与所属会话一致);拒绝时落审计、不执行、回执消息说明已取消。待确认记录 SHALL 有过期时间,过期确认按拒绝语义处理并说明。同一确认标识重复提交 SHALL 幂等(返回首次决策结果含执行结果,不重复执行)。

#### Scenario: 确认后执行并回执

- GIVEN 当前用户会话内存在未过期待确认调用
- WHEN 提交 confirm 决策
- THEN 工具被执行,落 EXECUTED 审计,会话内新增 assistant 回执消息(含执行结果摘要)

#### Scenario: 拒绝执行

- GIVEN 当前用户会话内存在未过期待确认调用
- WHEN 提交 reject 决策
- THEN 工具不执行,落审计,回执消息说明该操作已取消

#### Scenario: 他人确认拒绝

- GIVEN 待确认调用属于用户 A 的会话
- WHEN 用户 B 携带该确认标识提交确认
- THEN 拒绝(404 同形,不暴露存在性),不执行

#### Scenario: 过期确认

- GIVEN 待确认记录已过期
- WHEN 提交 confirm 决策
- THEN 按拒绝语义处理(不执行),回执说明已过期
