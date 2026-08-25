## ADDED Requirements

### Requirement: 记忆段注入 generate 提示

Agent 面问答请求 SHALL 纯增可选字段 memory_hint(召回的用户已知信息段,控制面注入);generate 节点在拼接用户提示时 SHALL 将非空 memory_hint 作为独立「关于这位用户的已知信息」段置于 schema 提示之前(系统提示模板资产不变);memory_hint 缺省或为空时提示形态与注入前逐字一致。ReAct 内核 SHALL 在首轮 user 消息拼接同一字段(同一拼接语义)。

#### Scenario: 记忆段拼接

- GIVEN 请求携带 memory_hint「偏好月度口径」
- WHEN generate 节点拼接用户提示
- THEN 用户提示含「关于这位用户的已知信息」段且位于表结构之前,系统提示不变

#### Scenario: 无记忆零回归

- GIVEN 请求不携带 memory_hint
- WHEN generate 节点执行
- THEN 提示形态与 022 之前逐字一致
