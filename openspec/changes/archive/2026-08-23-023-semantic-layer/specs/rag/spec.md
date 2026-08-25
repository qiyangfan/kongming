## ADDED Requirements

### Requirement: 召回响应纯增语义段组装

/internal/rag/retrieve 在三路召回结果之外 SHALL 追加组装语义段(semantic-layer spec):relations_hint——仅取本次召回表集合(schema_hint 命中的表)两两之间的启用关系,按「左表 JOIN 右表 ON 键」文字描述;metric_hints——问句命中 business_terms 或 metric_name 的启用指标 top-5(含 metric_name 与 sql_template 文字)。两段拼入响应纯增字段;无命中时为空串,不改变既有 schema_hint/history_sql 的任何语义。

#### Scenario: 关系段只含召回集合

- GIVEN 数据源有 20 张表关系,本次召回命中 2 表
- WHEN 组装响应
- THEN relations_hint 仅含该 2 表之间的关系行

#### Scenario: 指标段按问句命中

- GIVEN 启用指标「复购率」business_terms 含「复购」
- WHEN 问句含「复购」
- THEN metric_hints 含该指标名与口径文字

#### Scenario: 无语义数据零影响

- GIVEN 该租户+数据源无关系与指标
- WHEN 召回
- THEN 响应 relations_hint/metric_hints 为空串,schema_hint/history_sql 语义不变
