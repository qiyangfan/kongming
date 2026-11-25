## Purpose

问答链路流式响应契约:用户提问后实时看到 Agent 执行阶段、SQL 提前展示、
总结打字机式呈现;终态与同步问答完全一致;断线后后端继续执行至终态并落库,
主动切换上下文时在途流不污染新上下文。

## Requirements

### Requirement: SSE 事件协议

`POST /chat/query/stream` SHALL 以 UTF-8 `text/event-stream` 返回问答事件。业务帧仅用 SSE `data:` 发送形如 `{"type":"…","data":{…}}` 的 JSON 对象，`type` 和 `data` 均必填。公开类型为 `stage`、`sql`、`summary`、`tool_call`、`result`、`error`。Agent 的 `trace` 诊断消息 SHALL 限于内部流。消费方 SHALL 忽略 SSE 注释行。

校验通过的 SQL SHALL 先于终态发送，自愈重试可多次下发，以最后一次为准。总结增量 SHALL 在终态之前发送。连接可写时，API SHALL 发送恰好一个结尾事件 `result` 或 `error`，发送后结束响应。成功、业务拒绝、业务失败和已取消的问答结果 SHALL 使用 `result`。传输或输出失败 SHALL 使用 `error`。

#### Scenario: 事件序列完整形态

- GIVEN 一次普通问答成功，SQL 校验通过且查询有结果
- WHEN API 发送完整事件流
- THEN 节点 `stage` 的 `running` 和 `done` 按执行进度发送，通过校验的 `sql` 先于 `summary` 和结尾的 `result`
- AND 每条业务帧的 `type` 都位于 JSON 中，`result` 是唯一的结尾事件

#### Scenario: 业务拒绝仍走 result 终态

- GIVEN 生成的 SQL 被安全策略拦截
- WHEN 流式响应结束
- THEN 唯一的结尾事件为 `result`，其 `data.success` 为 `false` 且包含友好说明
- AND 流中没有被拦截的 `sql` 事件或 `error` 事件

#### Scenario: 直接得出问答结果或问答已取消

- GIVEN API 已得到无需调用 Agent 的问答结果，或本轮收敛为已取消
- WHEN API 发送本轮结尾事件
- THEN 该事件使用与普通问答相同的 `result` 格式
- AND 已取消结果的 `data.verdict` 为 `CANCELLED`

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

流式问答进行中前端脱离接收(网络中断、刷新页面、切换会话/租户/数据源或
离开页面;不含用户经显式停止发起的取消)时,控制面 SHALL 继续消费
Agent 面流至终态,assistant 消息/成功回写/审计 SHALL 照常落库;用户重进
会话可见完整结果。断线后 SHALL NOT 重发、不补偿(重进会话走历史回显)。
用户经显式停止发起的取消不走本要求,见「取消的执行效果与终态」。

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
- GIVEN 用户在流式问答进行中离开页面或切换会话/租户/数据源(未经停止按钮)
- WHEN 前端中止连接
- THEN 不发起取消,Agent 面图执行与落库不受影响(照常跑完)

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
(守卫按流的发起上下文丢弃过期写入)。此类中止仅为脱离接收,SHALL NOT
发起取消。

**显式停止**:流式进行中前端 SHALL 提供显式的停止入口,点击后
SHALL 中止在途读取并立即释放输入(用户可马上发起下一问),并向控制面
发起本轮取消。提示文案 SHALL 以取消结果为准:取消成功→说明本轮已取消,
且已开始执行的操作不会被撤回;取消请求未能送达→说明本轮仍在后台完成、
可稍后回看;本轮已结束→如实说明已结束。前端 SHALL NOT 在收到取消成功
结果前声称本轮已取消。

#### Scenario: 切换上下文取消在途流
- GIVEN 流式问答进行中
- WHEN 用户切换会话或租户或数据源或新建会话或离开页面
- THEN 前端中止在途流,旧流事件不写入新上下文,且不发起取消

#### Scenario: 流正常结束但无终态事件
- GIVEN 连接关闭时未收到 result/error 终态
- WHEN 分帧循环结束
- THEN 前端按失败呈现(不悬挂在等待态),问句回填可重试

#### Scenario: 显式停止不谎称取消执行(078)
- GIVEN 流式问答进行中且本轮标识已下发
- WHEN 用户点击停止且控制面返回取消成功
- THEN 在途读取中止、输入框立即可用,提示说明本轮已取消、已开始执行的操作
  不会被撤回
- AND 回查历史可见本轮的已取消说明,不出现完整应答

#### Scenario: 取消请求未能送达时不谎称已取消
- GIVEN 流式问答进行中且本轮标识已下发
- WHEN 用户点击停止但取消请求未能送达控制面
- THEN 提示说明本轮仍在后台完成、可稍后回看,不声称已取消

#### Scenario: 本轮已结束
- GIVEN 用户点击停止时控制面返回 outcome=COMPLETED
- WHEN 提示呈现
- THEN 如实说明本轮已结束,回查历史可见完整应答

### Requirement: 取消端点与归属校验

系统 SHALL 提供取消端点(路径含轮次标识):JWT 认证 + 权限点
`chat:execute` + 归属校验,仅本轮发起用户可取消。他人的轮次、不存在的
轮次,以及已结束且无落库结果的轮次,SHALL 统一返回 404(不区分「不存在」
「无权」「已结束」)。取消结果 SHALL 以 outcome 如实返回:`CANCELLED`
(本轮已取消)或 `COMPLETED`(取消到达前本轮已正常结束),并 SHALL 在有限
时间内返回(上限为部署常量)。

在途轮登记 SHALL 跨实例共享:取消请求落到任一实例都应生效——本实例未
命中在途登记时,凭共享登记经内部通道把取消转给持有该轮的实例执行,结果
原样回传。共享登记不可用、持有实例不可达,或登记指向本实例而本实例并无
该轮(陈旧登记)时,系统 SHALL 按落库结果回答:该轮已落库→`COMPLETED`
并携带消息标识;未落库→404。任何路径下 SHALL NOT 伪造终态,亦 SHALL NOT
把他人轮次取消掉。

#### Scenario: 发起用户取消在途轮
- GIVEN 用户 A 的一轮流式问答正在执行
- WHEN 用户 A 请求取消该轮
- THEN 返回 outcome=CANCELLED,该轮执行停止

#### Scenario: 他人轮次 404 同形
- GIVEN 轮次 R 属于用户 A 且正在执行
- WHEN 用户 B 请求取消 R
- THEN 返回 404,响应不区分「不存在」与「无权」,R 的执行不受影响

#### Scenario: 取消与完成竞争
- GIVEN 本轮在取消请求到达前已正常结束并落库
- WHEN 发起用户请求取消
- THEN 返回 outcome=COMPLETED 并携带该轮落库消息标识,历史不出现已取消消息

#### Scenario: 无权限或已结束未落库
- GIVEN 调用者不含 chat:execute,或轮次已结束且无落库结果
- WHEN 请求取消
- THEN 分别返回 403 / 404

#### Scenario: 取消请求落在非持有轮次的实例
- GIVEN 轮次 R 属于用户 A,正在实例 A 上执行,取消请求到达实例 B
- WHEN 用户 A 请求取消该轮
- THEN B 凭共享登记把取消转给 A,A 执行取消编排
- AND 返回 outcome=CANCELLED,R 的执行停止,历史只落一条已取消消息

#### Scenario: 共享登记不可用或持有实例不可达
- GIVEN 跨实例登记不可用(如存储故障),或持有该轮的实例不可达
- WHEN 发起用户请求取消
- THEN 按落库结果回答:已落库→COMPLETED 并携带消息标识;未落库→404
- AND 不伪造终态,不在存储里写已取消行

#### Scenario: 陈旧登记不自我转发
- GIVEN 共享登记指向本实例,但本实例并无该轮在途(实例重启一类的残留登记)
- WHEN 发起用户请求取消
- THEN 不向本实例自我转发(避免自环),按落库结果回答

### Requirement: 取消的执行效果与终态

取消生效后,控制面 SHALL 通知 Agent 面停止该轮执行;Agent 面 SHALL 停止
该轮图执行,此后 SHALL NOT 再发起模型调用与工具调用。已经发出的工具调用
(含 SQL 执行)SHALL NOT 被撤回。控制面 SHALL 为该轮收敛为唯一的已取消
终态(persist=true 时为 status=CANCELLED 的 assistant 消息),此后该轮
SHALL NOT 再产生其他落库结果;同一轮至多存在一条 assistant 应答消息。

#### Scenario: 取消后不再调用模型
- GIVEN 一轮流式问答正在执行模型调用
- WHEN 取消生效
- THEN Agent 面图执行停止,取消后该轮不再发起新的模型调用与工具调用
- AND 该轮终态为已取消(persist=true 时落 status=CANCELLED 的 assistant 消息)

#### Scenario: 已发出的工具调用不撤回
- GIVEN 该轮有一次工具调用已在执行
- WHEN 取消生效
- THEN 该工具调用不在中途被撤回,其结果不进入本轮落库

#### Scenario: persist=false 的轮次
- GIVEN 一轮 persist=false 的流式问答
- WHEN 取消生效
- THEN 该轮执行停止,不写入任何消息,取消结果的落库消息标识为空

#### Scenario: 取消与正常终态落库几乎同时
- GIVEN 本轮的正常应答已落库,取消请求随后到达
- WHEN 取消处理
- THEN 不再写已取消行,结果按「已正常结束」返回

#### Scenario: 取消与失败收尾几乎同时
- GIVEN 本轮流式传输已失败,失败收尾正在进行(FAILED 的 assistant 消息已提交或正在提交)
- WHEN 取消请求到达,含硬取消落在收尾段
- THEN 收尾照常做完,本轮只有一条 FAILED 的 assistant 消息与一个 error 终态事件
- AND 取消结果按「已结束」返回并携带该 FAILED 消息标识,不写已取消消息

### Requirement: 轮次标识下发

流式问答端点的响应 SHALL 以响应头 `X-Trace-Id` 下发本轮标识;该标识
SHALL 与该轮模型用量、审计所用的 trace_id 同值,并作为取消端点的轮次
标识。响应头返回前(流尚未开始)前端 SHALL NOT 发起取消,此时的停止按
「断线续存」处理。

#### Scenario: 从响应头取得轮次标识
- GIVEN 用户发起流式问答
- WHEN 响应头到达
- THEN 响应头 X-Trace-Id 为本轮标识,其后以该标识取消可命中本轮

#### Scenario: 流开始前停止
- GIVEN 流式问答请求已发出但响应头尚未返回
- WHEN 用户点击停止
- THEN 前端中止在途读取且不发起取消,按「断线续存」如实说明(本轮若已开始会在后台完成)

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

### Requirement: 公开事件字段与接口说明

API SHALL 按 `type` 校验每条公开事件的数据。`stage` 的 `node` 为字符串，`status` 为 `running` 或 `done`，`retry_count` 为非负整数。`sql.data.sql` 和 `summary.data.delta` 各为字符串。`tool_call.data` 包含字符串 `tool_name`、`status` 和可选的字符串 `args_digest`、`result_digest`。`result.data` 与同步问答响应逐字段一致。`error.data` 包含友好字符串 `error` 及必填的 `reason_code`。接口说明 SHALL 声明 200 成功响应为事件流，并按 `type` 列出对应数据结构。

#### Scenario: 事件类型与数据必须匹配

- GIVEN API 准备发送 `type=stage` 的事件
- WHEN `data` 只含 `sql`，或 `retry_count` 为字符串 `"1"`
- THEN 该数据不能以 `stage` 发送

#### Scenario: 接口说明与实际响应一致

- GIVEN 流式问答端点生成的接口说明
- WHEN 查阅其 200 响应及逐条事件的数据定义
- THEN 成功响应只声明 `text/event-stream`
- AND 六种公开事件各有一个由 `type` 选定的数据结构，`result.data` 引用同步问答的响应定义

### Requirement: 无效的 Agent 过程事件跳过

API SHALL 在转发前检查 Agent 过程消息。未知事件、无法解析的数据以及与已知事件不匹配的数据 SHALL 被跳过，后续有效事件照常处理。只有通过检查的 `stage` 事件 SHALL 进入阶段记录。API SHALL 能用轮次标识和失败类别定位坏消息，且 SHALL NOT 记录或对外返回其原始内容。

#### Scenario: 坏过程事件夹在有效事件中

- GIVEN Agent 依次发送有效的 `stage`、字段错误的 `stage`、内部的 `trace` 和有效的 `result`
- WHEN API 转发并保存结果
- THEN 公开事件流中只有有效的 `stage` 和 `result`
- AND 阶段记录只包含有效的 `stage`，问答结果正常保存

### Requirement: Agent 流结束异常

API SHALL 采用 Agent 第一条有效的 `result` 或 `error`，并忽略其后的事件。两种事件的数据须为 JSON 对象，`result` 须含布尔型 `success`，`error` 须含字符串 `error`。如果先收到有效的 Agent `error`，或 Agent 流在有效结尾出现前中断或结束，API SHALL 发送 `reason_code=AGENT_UNAVAILABLE` 的友好 `error`。该轮尚未收敛且要求保存时，SHALL 按问答失败保存一次。显式取消与问答收尾的竞争按现有取消规则处理。

#### Scenario: Agent 正常关流但结果无效

- GIVEN 已开始流式响应，Agent 只提供了 `success` 为字符串的结果并结束
- WHEN API 完成本轮处理
- THEN 只发送一次 `AGENT_UNAVAILABLE` 错误结尾
- AND `persist=true` 时只保存一条失败应答，`persist=false` 时不保存消息

#### Scenario: 重复结尾或连接尾部异常

- GIVEN API 已收到有效 Agent `result` 并完成本轮结果处理
- WHEN Agent 再发过程事件、重复结尾或读流尾部发生连接异常
- THEN 网页只收到已确定的 `result` 作为结尾，且该轮只保存一次

### Requirement: 公开结尾事件无法发送

流开始后，公开结尾事件在转换、检查或编码时失败，或输出结束时没有结尾事件且连接仍可写，API SHALL 改为发送唯一的 `reason_code=STREAM_OUTPUT_FAILED` 的 `error`。事件 SHALL 使用预定的友好文案，且不可包含内部错误详情。该发送错误 SHALL NOT 改写已经保存的状态或触发第二次保存。

#### Scenario: 已保存的成功结果无法编码

- GIVEN 问答结果已经保存，准备发送的 `result.data` 含有不能编码的值
- WHEN API 尝试生成结尾事件
- THEN 网页收到一次合法的 `STREAM_OUTPUT_FAILED` 错误结尾
- AND 原成功结果仍可从历史查看，数据库没有新增失败应答

### Requirement: 网页只分发检查通过的事件

网页 SHALL 在运行时按接口定义检查收齐的每条 JSON 事件。未知或无效事件 SHALL 被跳过。收到第一条合法的 `result` 或 `error` 后，网页 SHALL 停止分发该请求的后续事件。未主动中止且连接关闭时仍无合法结尾，网页 SHALL 显示答案未完整返回。

#### Scenario: 空对象不能冒充结果

- GIVEN 网页收到 `{"type":"result","data":{}}`，随后收到合法的 `result`
- WHEN 网页处理这两条消息
- THEN 只把后一个合法结果交给页面，前一个结果不结束接收

#### Scenario: 一个网络分块内有多个结尾

- GIVEN 一个网络分块同时包含一条合法的结尾事件和后续的其他事件
- WHEN 网页处理该分块
- THEN 只分发到第一条合法结尾，后续事件不触发页面更新

#### Scenario: 跨网络分块的事件及心跳

- GIVEN 中文字符、换行或空行分隔符跨网络分块，且消息之间有心跳注释
- WHEN 网页读取流
- THEN 按 SSE 规则恢复每条完整的数据消息，注释不触发页面更新
