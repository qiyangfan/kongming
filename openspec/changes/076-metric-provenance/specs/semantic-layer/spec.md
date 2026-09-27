# semantic-layer spec delta(076-metric-provenance)

## MODIFIED Requirements

### Requirement: 生成链路注入

RAG 召回响应 SHALL 纯增:relations_hint(仅召回表集合内的两两 JOIN path 文字描述;召回为空则不携带)与 metric_hints(问句命中 business_terms/metric_name 的启用指标,top-5,含 sql_template 文字);Agent 面 generate 节点 SHALL 在用户提示中拼接两段(系统提示模板不变),并遵循「JOIN 优先依据 relations_hint、口径优先依据 metric_hints」的引导;无数据时零注入,行为与引入前逐字一致。同一命中集 SHALL 另以结构化形态随召回响应下发(指标标识、名称、口径表达式、修订时间),供终态与前端展示本轮实际使用的口径依据;**metric_hints 文字段 SHALL 由该结构化命中派生**,SHALL NOT 存在第二套独立计算口径。

#### Scenario: 注入收窄到召回集合

- GIVEN 数据源有 20 张表的关系,本次召回 2 表
- WHEN 召回响应组装
- THEN relations_hint 只含该 2 表相关的关系,不灌全库

#### Scenario: 指标命中注入

- GIVEN 启用指标「复购率」含 sql_template
- WHEN 用户问「各品类复购率」
- THEN metric_hints 含该指标口径文字,generate 提示携带

#### Scenario: 结构化命中与文字段同源

- GIVEN 一次问句命中了 N 条启用指标
- WHEN 召回响应组装
- THEN 结构化命中列表含 N 条,每条带指标标识、名称、口径表达式与修订时间
- AND metric_hints 文字段恰为该列表的派生,两侧数量与内容一一对应

#### Scenario: 无命中零注入零附加

- GIVEN 问句未命中任何启用指标
- WHEN 召回响应组装
- THEN metric_hints 为空、结构化命中为空列表,注入行为与引入前逐字一致
