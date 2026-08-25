## ADDED Requirements

### Requirement: 记忆召回与显式记忆挂接

问答查询端点的链路 SHALL 在回调 Agent 面前执行记忆召回(当前问句向量 top-3,user+tenant 双过滤),召回结果经 memory_hint 传给 Agent 面;召回服务失败 SHALL 降级无注入(问答主链路不受影响)。用户输入含「记住/请记住」意图词时 SHALL 走显式记忆形成路径(agent-memory spec):落库后答复确认,不再走数据问答链路。响应契约零变化(纯链路内增强)。

#### Scenario: 召回注入

- GIVEN 用户有非 dormant 记忆
- WHEN 提交问答
- THEN Agent 请求体含 memory_hint,问答正常进行

#### Scenario: 召回失败降级

- GIVEN 记忆召回服务异常(embedding 不可达等)
- WHEN 提交问答
- THEN 按无注入处理,问答正常完成,失败仅日志

#### Scenario: 显式记忆短路

- GIVEN 用户输入「请记住:我偏好月度口径」
- WHEN 提交问答
- THEN 落显式记忆,答复「已记住」,不产生 SQL 执行
