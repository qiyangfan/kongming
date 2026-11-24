# Spec Delta(chat)

## MODIFIED Requirements

### Requirement: 流式中继的断线续存与失败收敛

前端脱离接收(不含用户显式取消)后控制面 SHALL 继续消费 Agent 面流至终态
并照常落库(断线续存,落库走独立数据库会话);用户显式取消的轮次按
query-streaming spec「取消的执行效果与终态」收敛为已取消终态。流式响应
SHALL 周期发送 SSE 注释行心跳(缺省 15s)。传输层失败(Agent 面不可达/
坏报文)SHALL 收敛 error 终态事件+FAILED 消息落库(persist=true 时),
不裸 500。首个成功轮的标题自动改写触发条件 SHALL 与同步端点一致。

#### Scenario: 断线续存落库
- GIVEN 流式问答进行中前端断开
- WHEN Agent 面流继续
- THEN 控制面消费至终态,assistant 消息/回写照常落库(独立会话)
- AND 重进会话历史回显完整结果

#### Scenario: 显式取消收敛为已取消终态
- GIVEN 流式问答进行中(persist=true),用户显式取消
- WHEN 取消生效
- THEN 控制面落 status=CANCELLED 的 assistant 消息(独立会话),
  该轮不再写 FAILED 消息或完整结果

#### Scenario: Agent 面流失败收敛
- GIVEN Agent 面不可达或返回非 200 或报文不可解析
- WHEN 流式问答进行
- THEN 下发 error 终态事件(友好文案,不暴露内部地址)并落 FAILED 消息

#### Scenario: 标题改写触发条件与同步端点一致
- GIVEN 流式问答产生首个 assistant 状态 SUCCESS 的消息
- WHEN 该轮终态落库
- THEN 按与同步端点相同的条件触发一次标题改写

## ADDED Requirements

### Requirement: 消息状态第五态(CANCELLED)

`chat_message.status` SHALL 支持第五态 `CANCELLED`:被用户显式取消的流式
问答轮以该状态持久化(取代「取消后照常落完整结果」的旧行为)。该消息
SHALL 为裸终态行——`summary` 承载取消说明文案,不含 SQL、结果集、图表
与产物卡;SHALL 携带取消前已收集的节点轨迹。呈现 SHALL 为简洁的已取消
说明(非错误横幅、非结果卡),SHALL NOT 提供反馈、纠错、再执行与导出
操作。已取消消息 SHALL NOT 触发标题改写、成功回写、答案评分与隐式记忆
提取。流式终态事件携带 verdict=CANCELLED 时,web 实时映射与历史回显
SHALL 一致。既有四态的映射与呈现 SHALL 逐字不变。

#### Scenario: 取消轮落库与呈现
- GIVEN 一轮 persist=true 的流式问答在执行中被用户取消
- WHEN 取消生效
- THEN 会话内新增 status=CANCELLED 的 assistant 消息,summary 为取消说明,
  不含 SQL/结果集/图表/产物卡,携带取消前已收集的节点轨迹
- AND 历史回显以已取消说明呈现,无反馈/纠错/再执行/导出入口

#### Scenario: 取消轮不触发旁路任务
- GIVEN 一轮在总结阶段完成前被取消
- WHEN 终态落库
- THEN 不触发标题改写、成功回写、答案评分与隐式记忆提取

#### Scenario: 实时终态映射一致
- GIVEN 流式终态事件携带 verdict=CANCELLED
- WHEN 前端构造本轮终态快照
- THEN 快照状态为 CANCELLED,与历史回显同形

#### Scenario: 既有四态零回归
- GIVEN SUCCESS/FAILED/REJECTED/NO_DATA 四类既有终态
- WHEN 终态组装与落库
- THEN 映射与呈现行为与引入前逐字一致
