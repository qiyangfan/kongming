# 017 query-streaming — 问答链路流式输出(SSE 端到端)

## Why

问答链路当前为一次性同步响应:用户提问后面对静态「军师运筹中…」,链路全程
(understand→retrieve→generate→validate→execute→summarize,典型 3-10s,慢查询更久)
无任何过程反馈,SQL 要等总结完成才能看到,体验与主流 LLM 应用差距明显。
016 评估基线已立(回归红线),流式重构是既定下一棒(HANDOFF);上游
nl2sql-data-platform 017 变更(7 节点图与 Kongming 8 节点图同构)已验证过
完整方案,本变更取其行为契约与关键设计,适配 FastAPI 双进程架构。

## What Changes

- **agent 面**新增 `POST /query/stream`(内部令牌,SSE):LangGraph
  `astream(stream_mode=["updates","custom"])` 驱动,节点经 wrapper 在 custom
  通道发 `stage` 事件(节点拓扑即阶段序列);validate 通过后发 `sql` 事件
  (SQL 提前展示);summarize 完成后发单条完整 `summary` delta;端点从
  updates 累积终态发 `result` 事件(与同步 /query 响应同构);
  意外异常发 `error` 终态。**nodes.py 零侵入**(prompt 资产纪律),
  事件发射点全部在 build.py 的节点 wrapper;同步 ainvoke 路径行为零变化。
- **api 面**新增 `POST /chat/query/stream`(chat:execute 权限+租户白名单+
  数据源预检,与同步端点同构):后台 task 消费 agent SSE 流经
  asyncio.Queue 中继转发;**断线续存**=前端断开后后台 task 继续消费至
  终态,assistant 消息/回写/审计照常落库(独立会话,010 标题任务同款
  模式);15s 心跳注释行防中间层空闲断连;业务终态恒走 `result` 事件
  (三态映射后与同步响应逐字段一致,含 message_id),`error` 事件仅用于
  传输层/意外失败。
- **web 面**:提问改走流式(fetch+ReadableStream 手写 SSE 分帧,EventSource
  不支持 POST/Bearer/X-Tenant-Id);流式卡片=阶段指示逐个点亮+自愈重试
  次数+SQL 折叠卡提前渲染+summary 打字机追加;终态回归现有 AssistantCard
  渲染;AbortController 在切会话/新建/切租户/卸载时取消在途流+序号守卫
  防迟到事件写入新上下文。
- **契约**:事件 data schema(Stage/Sql/Summary/Error 事件模型)进
  OpenAPI(make contracts),前端类型生成(ADR-005);result 事件 data 复用
  ChatQueryOut schema(终态一致性的直接表达)。

## Non-goals

- **真 token 级流式**:summary 事件为单条完整 delta(逐事件追加=打字机语义),
  token 流留远期(上游同款挂账,Phase 5);generate 的 SQL 不做 token 流。
- **限流基建**:Kongming 无限流体系(上游 Sentinel 无对应物),不虚构;
  流式端点权限/租户/数据源口径与同步端点同构即为本变更边界。
- **WebSocket/轮询**:一次性请求-响应场景 SSE 足够(上游 017 design 已裁决)。
- **取消传导**:主动取消=前端停止渲染+断开连接;agent 图与落库照常跑完
  (断线续存语义),不实现「取消即中止图执行」。
- **评估器切换**:evals 走同步 /chat/query persist=false 不变(016 基线
  回归红线零影响,收工复验)。
- **前端回滚开关**:同步端点保留(评估器/兼容面),前端提问直接切流式,
  不加 USE_STREAM 开关(回滚=代码 revert)。

## Capabilities

### New Capabilities

- `query-streaming`: 问答链路 SSE 流式契约——事件协议(stage/sql/summary/
  result/error 五类+时序)、阶段进度可见、SQL 提前展示、总结打字机、
  终态一致性(result 与同步逐字段一致)、断线续存与主动取消。

### Modified Capabilities

- `nl2sql-agent`: 图节点新增流式事件发射语义(custom 通道 wrapper 发
  stage;validate 通过发 sql;summarize 后发 summary;非流式调用零变化)。
- `chat`: 新增流式问答端点(前段编排与同步共用;断线续存落库;心跳;
  错误收敛;persist 语义)。
- `web-nav`: 对话区流式渲染(阶段指示/SQL 折叠/打字机/取消触发点/
  防串上下文守卫)。

## Impact

- **apps/agent**(零凭证面不变,ADR-002):main.py 新增 /query/stream 端点
  (StreamingResponse+事件构造复用同步逻辑);graph/build.py 节点 wrapper
  (get_stream_writer,langgraph 1.2.11 已核可用);同步 /query 一行不动。
- **apps/api**:chat 域 agent_client 加流式回调(AgentClient.query_stream);
  service 加流式编排方法(前段复用,断线续存独立会话落库——sessionmaker
  factory 经组合根注入,010 title_tasks 同款);router 加端点;SSE 帧
  解析纯函数;import-linter 契约零变化(新增代码在同一分层内)。
- **apps/web**:api.ts 加流式请求封装(复用 doFetch 鉴权头/401 单飞刷新
  语义);ChatPage 流式状态管理;新增 StreamCard 组件(shadcn 组件纪律)。
- **契约**:openapi.json +1 端点+4 事件 schema,make contracts 重生成。
- **依赖**:零新依赖(fetch/StreamingResponse/asyncio 原生能力)。
- **016 评估基线**:同步端点与 agent 图拓扑零行为变化,收工跑 evals
  smoke 复验基线不劣化(回归红线)。
