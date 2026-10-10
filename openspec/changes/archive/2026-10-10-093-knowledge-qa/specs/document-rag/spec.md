# Spec Delta

## MODIFIED Requirements

### Requirement: 知识分流(最小拓扑)

问答图 SHALL 在 retrieve 之后按 knowledge-qa 域三态意图路由分流:知识态
且文档通道命中时,走 doc_answer 节点——LLM 直接按文档内容生成文本回答
(不经 generate/validate/execute,无 SQL 执行面),引用经 citation 标注;
schema 通道是否命中不影响分流判定;融合态走融合问答路径(knowledge-qa
域);数据态走既有 SQL 链路。路由判定来源(LLM 三态 + 词表兜底)以
knowledge-qa 域口径为准。既有 8 节点路径 SHALL 逐字不变(零回归);误分流
SHALL 只影响答法,不产生任何数据副作用。

#### Scenario: 知识型问题分流

- GIVEN 问「退货政策是什么」且路由判定 knowledge、文档库有退货政策
- WHEN 问答链路执行
- THEN 直接按文档内容文本回答(带引用),不生成 SQL,消息 status=SUCCESS

#### Scenario: 数据问题不分流

- GIVEN 问「有多少部电影」且路由判定 data、schema 召回命中
- WHEN 执行
- THEN 走既有 SQL 链路,与 093 之前一致
