## Purpose

问答链路流式响应契约:用户提问后实时看到 Agent 执行阶段、SQL 提前展示、
总结打字机式呈现;终态与同步问答完全一致;断线后后端继续执行至终态并落库,
主动切换上下文时在途流不污染新上下文。


## Requirements

### Requirement: SSE 事件协议

系统 SHALL 以 Server-Sent Events 形态传输问答过程,事件五类:
`stage`(`{node, status, retry_count}`,节点粒度进度,status ∈ running/done)、
`sql`(`{sql}`,防火墙校验通过的 SQL 提前下发)、`summary`(`{delta}`,
总结文本增量)、`result`(终态,data 与同步问答响应逐字段同构)、
`error`(`{error, reason_code}`,传输层/意外失败终态)。时序 SHALL 满足:
stage×N →(校验通过时 sql;自愈重试可多次下发,以最后一次为准)→
(summarize 完成后 summary×N)→ 恰好一个终态事件(result 或 error)。
业务三态(成功/REJECTED/FAILED)SHALL 一律走 result
事件,SHALL NOT 走 error 事件(error 仅传输层失败与意外异常);
SSE 注释行(`:` 开头,心跳)消费方 SHALL 忽略。

#### Scenario: 事件序列完整形态
- GIVEN 一次成功问答(校验通过、有结果)
- WHEN 流式响应进行
- THEN 事件序列为各节点 stage(running/done 成对)→ sql → summary → result
- AND result 事件是唯一终态

#### Scenario: 业务拒绝仍走 result 终态
- GIVEN 生成的 SQL 被安全策略拦截(verdict=BLOCKED)
- WHEN 流式响应进行
- THEN 终态为 result 事件(success=false,含友好 error)
- AND 不下发 error 事件

### Requirement: 阶段进度可见

用户提问后 SHALL 实时看到 Agent 执行到哪个阶段,而不是静态等待指示。
阶段序列 SHALL 与图节点拓扑一致(理解问题→检索表结构→生成 SQL→校验→
执行→自愈→总结);触发自愈时 SHALL 显示重试次数(「第 N 次自愈重试」,
N=当前重试序号)。

#### Scenario: 节点完成顺序逐个点亮阶段指示
- GIVEN 用户提交一个自然语言问题
- WHEN Agent 依次执行 understand→retrieve→generate→validate→execute→summarize
- THEN 前端按节点完成顺序逐个点亮阶段指示

#### Scenario: 触发自愈显示重试次数
- GIVEN 触发自愈重试
- WHEN 自愈节点执行
- THEN 阶段指示显示「第 N 次自愈重试」(N=本次重试序号)

### Requirement: SQL 提前展示

SQL 生成并通过校验后 SHALL 立即下发(sql 事件),不必等执行与总结完成;
下发内容 SHALL 与终态落库 SQL 一致。

#### Scenario: 校验通过即渲染折叠卡片
- GIVEN generate 节点产出 SQL 且 validate 通过
- WHEN execute/summarize 仍在进行
- THEN 前端已渲染 SQL 折叠卡片
- AND 内容与最终落库 SQL 一致

### Requirement: 总结打字机

总结文字 SHALL 逐事件追加呈现(打字机语义);当前实现为单条完整 delta
一次性下发(逐段 token 流为远期演进,不在本契约内);无结果或降级兜底
文案 SHALL 同样以 summary 事件形态下发。

#### Scenario: 总结逐段追加显示
- GIVEN execute 成功进入总结阶段
- WHEN summary 事件到达
- THEN 前端打字机式追加显示总结文本,终态后被完整结果卡片接管

#### Scenario: 查询无结果的总结
- GIVEN 执行成功但结果集为空
- WHEN 总结阶段完成
- THEN 总结兜底文案以 summary 事件下发

### Requirement: 终态一致性

流式问答的终态 result 事件 data SHALL 与同步问答响应逐字段一致
(session_id/success/verdict/summary/insights/stats/columns/rows/sql/chart/
retry_count/error/message_id/datasource_key/metric_hits);历史会话回显(落库结果反序列化
路径)SHALL 零改动、零差异;流式与同步 SHALL 共用同一套前段编排(租户/数据源/
会话/history/USER 消息落库)与同一套终态落库(assistant 消息三态映射/回写/
标题任务触发),SHALL NOT 复制出第二套口径。

#### Scenario: 终态字段逐字段一致
- GIVEN 同一问题分别走同步与流式端点
- WHEN 两个请求都完成
- THEN result 事件 data 与同步响应逐字段一致(含 message_id 与数据源快照)
- AND 历史回显与同步问答的落库结果无差异

#### Scenario: persist 语义一致
- GIVEN persist=false 的流式问答
- WHEN 流完成
- THEN 不建会话、不落消息、不回写历史,result 事件 message_id=null

### Requirement: 断线续存

流式问答进行中前端断开(网络中断或主动取消)时,控制面 SHALL 继续消费
Agent 面流至终态,assistant 消息/成功回写/审计 SHALL 照常落库;用户重进
会话可见完整结果。断线后 SHALL NOT 重发、不补偿(重进会话走历史回显)。

**断线后的终态可见性(078)**:用户在同一路由上停留在该会话(含刷新后回到
同一会话)时,若该会话末条为 user 问句且其后无 assistant 应答,前端 SHALL
以**有界**间隔回查消息历史直至该轮终态出现,超时后 SHALL 停止回查
(SHALL NOT 无限轮询)。该回查 SHALL 只依赖既有历史端点,SHALL NOT
为此新增流端点或续传机制。

#### Scenario: 前端断开后落库照常
- GIVEN 流式问答进行中
- WHEN 前端断开连接(非后端故障)
- THEN 控制面继续消费至终态,assistant 消息照常落库
- AND 用户重进会话可见完整结果

#### Scenario: 主动取消不等同于后端中止
- GIVEN 用户在流式问答进行中主动离开
- WHEN 前端中止连接
- THEN Agent 面图执行与落库不受影响(照常跑完)

#### Scenario: 刷新后本轮终态自行出现(078)
- GIVEN 用户在流式进行中刷新页面,停留在同一会话,该会话末条为 user 问句
- WHEN 前端回查消息历史
- THEN 服务端该轮落库完成后,assistant 应答在有限次回查内自行出现,
  无需用户切走再回

#### Scenario: 回查有界
- GIVEN 末条为 user 问句且回查已达上限仍无 assistant 应答
- WHEN 上限耗尽
- THEN 停止回查并按既有失败/待重试口径呈现,不持续发起请求

### Requirement: 心跳保活

流式响应 SHALL 周期性发送 SSE 注释行心跳(缺省间隔 15s),防慢查询期间
中间层/浏览器空闲断连;心跳 SHALL NOT 影响事件语义(注释行被消费方忽略)。

#### Scenario: 慢查询期间心跳下发
- GIVEN execute 阶段耗时超过心跳间隔
- WHEN 无业务事件可发
- THEN 控制面按间隔发送 SSE 注释行心跳

### Requirement: 流式端点防线同构

流式端点 SHALL 与同步问答端点走同一防线链:JWT 认证 + 权限点
`chat:execute` + 租户白名单 + 数据源入口预检;前段校验失败(401/403/404/
400)SHALL 在流开始前以常规 JSON 错误响应(非 SSE);流开始后的传输层
失败 SHALL 收敛为 error 终态事件+FAILED 落库,不裸 500。

#### Scenario: 前段校验失败常规响应
- GIVEN 请求未携带有效 JWT 或无权限或租户未绑定或数据源非法
- WHEN 提交流式问答
- THEN 返回常规 JSON 错误响应(与同步端点同码同 detail),流未开始

#### Scenario: Agent 面不可达收敛 error 终态
- GIVEN Agent 面进程宕机
- WHEN 流式问答已开始
- THEN 下发 error 终态事件(友好文案),assistant 消息 status=FAILED
- AND 不裸 500

### Requirement: 前端主动取消与上下文守卫

用户主动切会话/新建会话/切租户/切数据源/离开页面时,前端 SHALL 中止在途流
(AbortController);迟到的流事件 SHALL NOT 写入新上下文
(守卫按流的发起上下文丢弃过期写入)。

**显式停止(078)**:流式进行中前端 SHALL 提供显式的停止入口,点击后
SHALL 中止在途读取并立即释放输入(用户可马上发起下一问),且 SHALL 以
与"断线续存"一致的口径说明:该轮仍在后台完成,稍后可在历史中回看。
文案 SHALL NOT 暗示服务端执行已被中止(服务端不中止是既有契约),
也 SHALL NOT 声称答案已被取消。

#### Scenario: 切换上下文取消在途流
- GIVEN 流式问答进行中
- WHEN 用户切换会话或租户或数据源或新建会话或离开页面
- THEN 前端中止在途流,旧流事件不写入新上下文

#### Scenario: 流正常结束但无终态事件
- GIVEN 连接关闭时未收到 result/error 终态
- WHEN 分帧循环结束
- THEN 前端按失败呈现(不悬挂在等待态),问句回填可重试

#### Scenario: 显式停止不谎称取消执行(078)
- GIVEN 流式问答进行中
- WHEN 用户点击停止
- THEN 在途读取中止、输入框立即可用,呈现说明该轮仍在后台完成可稍后回看
- AND 该轮随后照常落库,回查历史可见完整应答
