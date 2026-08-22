## ADDED Requirements

### Requirement: 流式问答端点

系统 SHALL 提供用户面流式问答端点 `POST /chat/query/stream`:鉴权与
前段防线(权限点 `chat:execute` + X-Tenant-Id 租户白名单 + 数据源入口
预检 + 会话处理 + history 补全 + USER 消息落库)SHALL 与同步问答端点
共用同一编排;响应形态为 SSE(text/event-stream,Cache-Control no-cache),
事件契约见 query-streaming spec。Agent 面流 SHALL 由控制面中继:阶段/
sql/summary 事件透传,result/error 终态由控制面在落库后构造下发
(result 事件在 assistant 消息落库之后发出,message_id 即落库消息 id)。
前端断开后控制面 SHALL 继续消费 Agent 面流至终态并照常落库(断线续存,
落库走独立数据库会话)。流式响应 SHALL 周期发送 SSE 注释行心跳
(缺省 15s)。传输层失败(Agent 面不可达/坏报文)SHALL 收敛 error 终态
事件+FAILED 消息落库(persist=true 时),不裸 500。首个成功轮的标题
自动改写触发条件 SHALL 与同步端点一致。

#### Scenario: 流式问答全链
- GIVEN 已登录用户角色含 chat:execute 且租户已绑定
- WHEN 以流式端点提交问题
- THEN 响应为 SSE 流:阶段事件逐个到达,校验通过后 sql 事件先于终态
- AND result 事件 data 与同步端点响应同构(含 message_id)
- AND user 与 assistant 消息照常落库

#### Scenario: 流开始前校验失败
- GIVEN 会话不存在或数据源非法或租户未绑定
- WHEN 以流式端点提交问题
- THEN 返回常规 JSON 错误响应(404/400/403,与同步端点同码同 detail)

#### Scenario: 断线续存落库
- GIVEN 流式问答进行中前端断开
- WHEN Agent 面流继续
- THEN 控制面消费至终态,assistant 消息/回写照常落库(独立会话)
- AND 重进会话历史回显完整结果

#### Scenario: Agent 面流失败收敛
- GIVEN Agent 面不可达或返回非 200 或报文不可解析
- WHEN 流式问答进行
- THEN 下发 error 终态事件(友好文案,不暴露内部地址)并落 FAILED 消息
