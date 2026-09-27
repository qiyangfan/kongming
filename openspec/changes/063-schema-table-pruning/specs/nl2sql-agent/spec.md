# nl2sql-agent spec delta(063-schema-table-pruning)

## ADDED Requirements

### Requirement: 相关表预筛

生成环节注入的「可用表结构」上下文 SHALL 可经由问题驱动的表级预筛收敛:
预筛 SHALL 以改写后问题、术语库(term/aliases)、人工黄金示例、语义层指标
为输入,对表名、列名与表说明加权打分,产出上限可配的 top-N 表集合
(`N` 缺省 12),SHALL NOT 需要对目标库发起任何额外调用。

预筛 SHALL 可回退:存在开关可在不回滚代码的前提下退回全量注入(基线行为)。

预筛结果 SHALL 可解释:入选表与命中来源(术语/指标/列名)、被裁表数 SHALL
随 trace 下发,使「为何未选中某表」可排查;SHALL NOT 为不可归因的黑盒。

**零命中约束**:问题与全部表零命中时,系统 SHALL 维持既有哨兵路径
(`NO_RELEVANT_TABLE` 如实短路),SHALL NOT 以硬凑 top-N 顶替——把判别职责
从「模型如实说明未覆盖」推给「打分器随意选取」属质量倒退。

**不劣化约束**:预筛开启时的表召回与执行匹配 SHALL NOT 低于全量注入基线
(以评估集对拍为准);退化超过回归红线时 SHALL 可通过开关回退并记录。

#### Scenario: 大 schema 租户收敛注入

- GIVEN 某租户可用表结构含 40 张表,问题仅涉及其中 3 张
- WHEN 走预筛路径生成
- THEN 注入的可用表结构收敛至 top-N(≤12)且包含问题所需表
- AND trace 中可见被裁表数与各入选表的命中来源

#### Scenario: 零命中维持哨兵

- GIVEN 问题与任何表名、列名、术语、指标均零命中
- WHEN 预筛执行
- THEN 不生成硬凑的 top-N,链路按既有哨兵路径如实短路,
  不进入本地预检与执行

#### Scenario: 开关回退

- GIVEN 预筛选开关被关闭
- WHEN 发起问答
- THEN 注入全量可用表结构,行为与引入预筛前逐字一致

#### Scenario: 预筛零新增调用

- GIVEN 预筛开启
- WHEN 执行一次问答
- THEN 相较全量注入,对目标库与外部模型的调用次数不增加

#### Scenario: 不劣化有据

- GIVEN 评估集在预筛开启与关闭两种形态下的 Recall@K 与 Execution Match
- WHEN 对比
- THEN 预筛开启形态不劣于全量基线;若劣化超红线则记录并回退开关
