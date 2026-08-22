## ADDED Requirements

### Requirement: 流式事件发射(自定义流通道)

Agent 面问答图节点 SHALL 支持在流式调用下发射过程事件:每个节点进入时
发射 stage(running)、完成时发射 stage(done);节点名 ∈ understand/
retrieve/generate/validate/execute/selfheal/summarize(与图拓扑一致)。
validate 通过时 SHALL 发射 sql 事件(内容=当前生成的 SQL);summarize
完成时 SHALL 发射 summary 事件(delta=完整总结文案,单条)。
selfheal 的 stage 事件 SHALL 携带重试序号(retryCount=本次重试序号),
其余节点 retryCount=当前已重试次数。事件发射点 SHALL NOT 改动节点
业务逻辑与 prompt 资产;非流式调用(invoke/单测直调)下事件发射 SHALL
静默跳过,节点行为零变化。

#### Scenario: 节点 stage 事件成对发射
- GIVEN 流式调用问答图
- WHEN 任一节点执行
- THEN 该节点 stage(running)与 stage(done)事件先后发射,data 携带节点名

#### Scenario: 校验通过发射 sql 事件
- GIVEN generate 产出 SQL 且 validate 通过
- WHEN validate 节点完成
- THEN 发射 sql 事件,内容与 state 中生成的 SQL 一致
- AND 校验不通过时 不发射 sql 事件

#### Scenario: 自愈重试序号随 stage 事件透出
- GIVEN 触发自愈(第 1 次重试)
- WHEN selfheal 节点完成
- THEN selfheal 的 stage 事件 retryCount=1

#### Scenario: 非流式调用零行为变化
- GIVEN 同步调用问答图(invoke)
- WHEN 节点执行
- THEN 无事件副作用,返回 state 与流式改造前逐字段一致

### Requirement: 流式问答端点(Agent 面)

Agent 面 SHALL 提供流式问答端点(内部令牌认证,ADR-002 信任边界):
以自定义流通道转发节点事件,以 updates 通道累积终态;终态 result 事件
data SHALL 与同步问答端点响应同构(单一定义防字段漂移);图执行意外
异常 SHALL 收敛为 error 终态事件(友好文案),不裸 500;LLM 故障
 SHALL 与同步端点同口径收敛(success=false 的 result 事件)。
流式消费方断开时 SHALL NOT 影响图执行完整性判定(终态由消费方语义
决定,Agent 面单次执行到终态为止)。

#### Scenario: 流式端点终态与同步同构
- GIVEN 同一输入分别调用同步与流式端点
- WHEN 两请求完成
- THEN 流式 result 事件 data 与同步响应逐字段一致

#### Scenario: 图执行异常收敛 error 终态
- GIVEN 图执行抛出意外异常
- WHEN 流式响应进行
- THEN 下发 error 终态事件(友好文案),不裸 500
