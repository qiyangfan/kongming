# query-streaming spec delta(078-chat-stream-interaction-fixes)

## MODIFIED Requirements

### Requirement: 断线续存

流式问答进行中前端断开(网络中断或主动取消)时,控制面 SHALL 继续消费
Agent 面流至终态,assistant 消息/成功回写/审计 SHALL 照常落库;用户重进
会话可见完整结果。断线后 SHALL NOT 重发、不补偿(重进会话走历史回显)。

**断线后的终态可见性(069)**:用户在同一路由上停留在该会话(含刷新后回到
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

#### Scenario: 刷新后本轮终态自行出现(069)
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
(守卫按流的发起上下文丢弃过期写入)。

**显式停止(069)**:流式进行中前端 SHALL 提供显式的停止入口,点击后
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

#### Scenario: 显式停止不谎称取消执行(069)
- GIVEN 流式问答进行中
- WHEN 用户点击停止
- THEN 在途读取中止、输入框立即可用,呈现说明该轮仍在后台完成可稍后回看
- AND 该轮随后照常落库,回查历史可见完整应答
