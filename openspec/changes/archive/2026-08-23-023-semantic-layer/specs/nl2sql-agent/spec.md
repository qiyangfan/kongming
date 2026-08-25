## ADDED Requirements

### Requirement: 召回响应语义段

/internal/rag/retrieve 响应 SHALL 纯增可选字段 relations_hint(召回表集合内的 JOIN path 文字段)与 metric_hints(命中指标口径段);缺省/空时 Agent 面行为与引入前一致。两段由控制面组装(语义层域),召回服务失败时随整体回退实时内省(两段缺省为空)。

#### Scenario: 响应携带语义段

- GIVEN 语义层有相关关系与命中指标
- WHEN Agent 面调用召回
- THEN 响应含 relations_hint 与 metric_hints 文字段

### Requirement: 生成提示拼接语义段

generate 节点 SHALL 在用户提示中拼接 relations_hint/metric_hints 非空段(位于表结构提示之后、历史成功 SQL 之前;系统提示模板不变),并在拼接存在时遵循「JOIN 优先依据 relations_hint,口径优先依据 metric_hints」;两段皆空时提示形态与引入前逐字一致。ReAct 内核的 rag_retrieve 工具结果携带同名字段时,LLM 观察原样可见(内核零改动)。

#### Scenario: 拼接位置正确

- GIVEN 召回响应含 relations_hint 与 metric_hints
- WHEN generate 拼接用户提示
- THEN 两段位于表结构之后、历史成功 SQL 之前

#### Scenario: 零注入零回归

- GIVEN 语义层无数据
- WHEN generate 执行
- THEN 提示形态与 023 之前逐字一致
