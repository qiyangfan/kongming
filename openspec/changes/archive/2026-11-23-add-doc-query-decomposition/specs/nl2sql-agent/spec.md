## ADDED Requirements

### Requirement: 文档多查询分解召回

知识态与融合态的复合问题,意图路由 SHALL 在同一次模型调用中给出 1-3 条
自含子查询;retrieve 节点 SHALL 先按原问句检索,再按子查询并行补检文档
通道,按引用键去重合并父块,原问句结果优先,合并总量上限 8 条;单主题
问题、解析失败或子查询检索失败时 SHALL 保持原问句单次检索行为。分解
SHALL 仅影响文档通道,schema、指标、历史 SQL 与 SQL 链路不变。

#### Scenario: 复合知识问题合并两路证据

- GIVEN 问句同时涉及两个可独立检索的文档主题,路由判定 knowledge 并给出两条子查询
- WHEN retrieve 执行
- THEN 原问句与两条子查询各检索一次文档通道,父块按引用键去重合并,原问句命中在前
- AND doc_answer 可引用合并后的父块

#### Scenario: 单主题问题零变化

- GIVEN 问句为单主题或路由未给出子查询
- WHEN retrieve 执行
- THEN 仅按原问句检索一次,doc_hints 与引入分解前一致

#### Scenario: 子查询检索失败降级

- GIVEN 子查询检索超时、异常或返回空
- WHEN retrieve 执行
- THEN 保留原问句命中,不新增失败终态
