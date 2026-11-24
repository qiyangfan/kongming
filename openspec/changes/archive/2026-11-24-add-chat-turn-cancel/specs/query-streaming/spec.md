# Spec Delta(query-streaming)

## MODIFIED Requirements

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

## ADDED Requirements

### Requirement: 取消端点与归属校验

系统 SHALL 提供取消端点(路径含轮次标识):JWT 认证 + 权限点
`chat:execute` + 归属校验,仅本轮发起用户可取消。他人的轮次、不存在的
轮次,以及已结束且无落库结果的轮次,SHALL 统一返回 404(不区分「不存在」
「无权」「已结束」)。取消结果 SHALL 以 outcome 如实返回:`CANCELLED`
(本轮已取消)或 `COMPLETED`(取消到达前本轮已正常结束),并 SHALL 在有限
时间内返回(上限为部署常量)。

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
