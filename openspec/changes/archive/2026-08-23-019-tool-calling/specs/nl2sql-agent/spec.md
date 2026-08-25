## ADDED Requirements

### Requirement: ReAct 内核并存与终态等价

Agent 面 SHALL 在既有 8 节点 NL2SQL 问答图之外并存 ReAct 工具调用内核(独立端点),两内核的终态响应单一定义、逐字段同构(success/verdict/summary/insights/stats/columns/rows/sql/retry_count/error)。既有问答图的节点逻辑、路由语义、prompt 资产与流式事件契约 SHALL NOT 因 ReAct 内核引入而变化;ReAct 链路的流式事件在既有事件清单上纯增 tool_call 类型,老消费方不受影响。两内核 SHALL 共享同一套 LLM 客户端纪律(无自写重试,ReAct 循环本身即重试/决策机制)与同一套控制面回调鉴权口径(X-Internal-Token)。

#### Scenario: 双内核终态同构

- GIVEN 同一数据查询问题分别经 8 节点图与 ReAct 内核处理
- WHEN 两请求完成
- THEN 两者响应携带相同字段集合与语义(columns/rows 来自最终一次成功 SQL 执行,summary 为最终自然语言总结)

#### Scenario: 既有问答图零回归

- GIVEN 019 引入 ReAct 内核后
- WHEN 调用既有 /query 或 /query/stream
- THEN 节点链、事件序列与终态字段与引入前逐字段一致(016 评估基线不降)

#### Scenario: tool_call 事件与既有事件并列

- GIVEN ReAct 流式端点消费方
- WHEN 循环中发生工具调用
- THEN 事件流中 tool_call 事件与 stage/result/error 事件并存,既有事件的字段与语义不变
