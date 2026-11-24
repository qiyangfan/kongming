# Spec Delta(nl2sql)

## ADDED Requirements

### Requirement: 在途问答中止(Agent 面)

Agent 面 SHALL 提供在途问答中止端点,以内部令牌认证(与既有问答端点同一
道闸):按 trace_id 中止该轮图执行,覆盖常规问答图与工具调用内核两类
流式入口。中止 SHALL 幂等——trace_id 未知或该轮已结束时返回「未在执行」
且不报错。中止生效后 Agent 面 SHALL NOT 再为该轮发起模型调用与工具
调用;已发出的工具调用结果 SHALL 被丢弃。

#### Scenario: 中止在途轮
- GIVEN 某轮流式问答正在执行
- WHEN 控制面携正确内部令牌请求中止该 trace_id
- THEN 该轮图执行被取消,其后不再进入新的图节点,
  不再为该轮发起新的模型调用与工具调用

#### Scenario: 幂等
- GIVEN trace_id 未知,或对应轮次已结束
- WHEN 请求中止
- THEN 返回「未在执行」,不报错,不影响其他在途轮次

#### Scenario: 缺失或错误令牌
- WHEN 请求中止但无令牌头或值不匹配
- THEN 返回 403,不中止任何轮次

#### Scenario: 同一 trace 的在途执行全部中止
- GIVEN 同一 trace_id 下有多处在途流式执行
- WHEN 请求中止该 trace_id
- THEN 这些执行均被中止
