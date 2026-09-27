# query-streaming spec delta(076-metric-provenance)

## MODIFIED Requirements

### Requirement: 终态一致性

流式问答的终态 result 事件 data SHALL 与同步问答响应逐字段一致(session_id/success/verdict/summary/insights/stats/columns/rows/sql/chart/retry_count/error/message_id/datasource_key/metric_hits);历史会话回显(落库结果反序列化路径)SHALL 零改动、零差异;流式与同步 SHALL 共用同一套前段编排(租户/数据源/会话/history/USER 消息落库)与同一套终态落库(assistant 消息三态映射/回写/标题任务触发),SHALL NOT 复制出第二套口径。

#### Scenario: 终态字段逐字段一致

- GIVEN 同一问题分别走同步与流式端点,且两轮都命中了指标口径
- WHEN 两个请求都完成
- THEN result 事件 data 与同步响应逐字段一致(含 message_id、数据源快照与口径命中列表)
- AND 历史回显与同步问答的落库结果无差异

#### Scenario: persist 语义一致

- GIVEN persist=false 的流式问答
- WHEN 流完成
- THEN 不建会话、不落消息、不回写历史,result 事件 message_id=null
