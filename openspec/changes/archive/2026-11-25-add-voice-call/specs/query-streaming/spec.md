# Spec Delta(query-streaming)

## ADDED Requirements

### Requirement: 系统发起的取消

系统 SHALL 能在服务端直接取消一轮在途流式问答(不经用户面的取消端点):
执行效果、终态落库与幂等语义 SHALL 与用户显式取消一致——停止 Agent 面执行、
此后不再发起模型调用与工具调用、落 status=CANCELLED 的 assistant 消息、
同一轮至多一条 assistant 应答。

#### Scenario: 系统取消在途轮
- GIVEN 一轮流式问答正在执行
- WHEN 系统对其发起取消
- THEN 该轮收敛为已取消(persist=true 时落 status=CANCELLED 的 assistant 消息)
- AND 取消后该轮不再发起新的模型调用

#### Scenario: 系统取消与用户取消等效
- GIVEN 同一轮先后被系统与用户发起取消
- WHEN 处理完成
- THEN 该轮终态为已取消,会话内至多一条 assistant 应答

#### Scenario: 已结束的轮次
- GIVEN 该轮在系统发起取消前已正常结束并落库
- WHEN 系统对其发起取消
- THEN 不改变已有结果,调用方得知该轮已结束
