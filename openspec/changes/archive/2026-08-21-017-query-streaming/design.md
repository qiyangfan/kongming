# 017 design — 问答链路流式响应(SSE 端到端)

上游 nl2sql-data-platform 017 变更(archive/2026-08-15-017-query-streaming)
的行为契约与传输选型直接取材;实现形态按 Kongming 全 async 双进程架构
重设计(上游 Java SseEmitter 中继 → Kongming FastAPI 后台任务中继)。

## 1. 传输选型:SSE(沿上游裁决)

| 方案 | 结论 | 理由 |
|---|---|---|
| **SSE** | 选 | 一次性请求-响应;普通 HTTP,Bearer/X-Tenant-Id 头/反代语义与现栈一致 |
| WebSocket | 弃 | 双向通道过剩;连接管理/鉴权握手/保活一整套新机制 |
| 轮询 taskId | 弃 | 需任务状态存储+定时器,延迟复杂度双高 |

EventSource 不支持 POST/自定义头 → 前端 fetch+ReadableStream 手写分帧
(约 30 行,复用 api.ts 的 doFetch 鉴权头/401 单飞刷新语义)。

## 2. 事件契约(三层一套;字段进 OpenAPI)

| event | data | 产生方 | 说明 |
|---|---|---|---|
| `stage` | `{node,status,retryCount}` | agent | 节点粒度;node ∈ 7 节点名 |
| `sql` | `{sql}` | agent | validate 通过后立即下发 |
| `summary` | `{delta}` | agent | 单条完整总结(逐段 token 流=远期) |
| `result` | 终态(=ChatQueryOut) | **api 富化** | 落库后构造;与同步响应逐字段一致 |
| `error` | `{error,reasonCode?}` | api | 仅传输层/意外失败;业务三态走 result |

时序:stage×N →(sql)→(summary)→ 恰好一个终态。
心跳=SSE 注释行 `: ping`(消费方忽略,15s)。

**优于上游的一处裁定**:上游 REJECTED(success=false)改发 error 事件,
混淆业务拒绝与传输失败;Kongming 业务终态恒走 result(前端按同步同构
渲染 REJECTED/FAILED 分态),error 仅传输层。事件 data schema
(Stage/Sql/Summary/Error 四模型)以 responses 声明进 OpenAPI
(text/event-stream),前端类型走 ADR-005 生成管线。

## 3. agent 侧:节点 wrapper(nodes.py 零侵入)

- `POST /query/stream`:async 端点(Kongming 全 async,无上游同步环回
  死锁问题),`StreamingResponse(media_type="text/event-stream")`,
  头 Cache-Control no-cache + X-Accel-Buffering no。
- `get_graph().astream(initial, stream_mode=["updates","custom"])`:
  custom 通道=节点事件(get_stream_writer;langgraph 1.2.11 已核可用);
  updates 通道=逐节点返回 dict,端点累积得终态(与 ainvoke 的最终 state
  同构,TypedDict 无 reducer 直接覆盖)。
- **事件发射点=build.py 的 wrapper**(包住节点函数):running/done
  stage 对发;validate wrapper 在节点返回后校验 validation.passed 发
  sql(state 传入 wrapper 时已含 generated_sql);summarize wrapper 在
  返回后发 summary(delta=返回值 summary)。selfheal 的 retryCount=
  返回值 retry_count(本次序号),其余节点=state 当前值。
  **prompt 资产纪律(HANDOFF)**:nodes.py/prompts.py 一行不动,
  wrapper 层零业务侵入;非流式调用 get_stream_writer 抛 RuntimeError,
  wrapper 静默跳过(上游同款)→ 同步 /query 与 016 评估链路零影响。
- 终态构造抽公共函数(同步 /query 与流式共用,防字段漂移);
  LLM 故障(LlmError)同同步口径收敛 success=false result;
  图意外异常 → error 终态。
- 落选备选:改 nodes.py 逐节点插桩——侵入 prompt 资产邻接面,
  同步路径引入发射死代码,弃。

## 4. api 侧:后台任务 + Queue 中继(断线续存的关键)

**Starlette 与 Java SseEmitter 的语义差异(本设计核心)**:Starlette
StreamingResponse 在客户端断开时会**取消**响应生成器(listen_for_disconnect);
生成器内联消费 agent 流的形态下,断线=agent 流也被取消+落库丢失,
违反断线续存契约。因此:

```
POST /chat/query/stream
  ├─ 前段(与同步共用):租户→数据源预检→会话→history→trace_id→USER 落库
  ├─ 后台 task(_consume_agent):httpx stream 消费 agent SSE → 解析帧
  │    ├─ stage/sql/summary 帧 → queue.put(转发文本)
  │    ├─ result 帧 → 独立会话落库(assistant+touch+回写+标题任务触发)
  │    │              → 富化(chart/message_id/session_id)→ queue.put(result 帧)
  │    ├─ 传输失败 → FAILED 落库 → queue.put(error 帧)
  │    └ finally → queue.put(None) 终止标记
  └─ SSE 生成器:loop { wait_for(queue.get(), 15s) 超时发心跳;None→break }
```

- 断线时 SSE 生成器被取消,**后台 task 不受影响**继续消费至终态+落库。
- **落库必须用独立数据库会话**:请求作用域 session 随响应取消而关闭
  (FastAPI 依赖 teardown),后台 task 自建短会话(sessionmaker factory
  注入,010 title_tasks 同款模式);USER 消息落库在前段(请求作用域,
  流开始前完成,无此问题)。
- 心跳=生成器 wait_for 超时分支(不引入定时器线程)。
- result 帧在落库后发出:前端收到 result 时消息已落库,invalidate
  历史即一致;断线场景用户重进会话走历史回显(不重发不补偿)。
- 落选备选:①内联生成器+shield——被取消的协程不能继续 await,落库
  虽可 shield 但「继续消费至终态」不满足,弃;②asyncio.shield 整个
  消费循环——语义等价但结构绕,Queue 形态可测性更好(单测直测后台
  task,不经 HTTP 断线模拟)。

## 5. 终态一致性保障

- 前段共用:流式方法复用 service.query 的前段(租户/数据源/会话/
  history/USER 落库)——抽私有段共用,禁复制第二套口径(上游铁律)。
- 终态落库共用:三态映射/result_json 构造/chart 建议/writeback/标题
  任务触发条件,全部走同一私有方法(在线与断线两路同构——都在后台
  task 里落库,天然只有一套)。
- result 事件 data 构造=ChatQueryOut 同一映射函数(_out 复用)。

## 6. 前端

- api.ts:`streamChatQuery(body, handlers, signal)`——doFetch 复用
  (鉴权头/租户头/401 单飞重放);resp.ok=false 走 toStatusError 同款
  (流开始前错误=常规 JSON);ReadableStream 手写分帧(\n\n 边界,
  event:/data: 行,: 注释忽略;JSON.parse 失败静默跳帧)。
- ChatPage:提问改流式;流式状态={stages:[],sql,summaryText,phase};
  守卫=流发起时的上下文(session/tenant/datasource)存 ref,回调比对
  当前值不一致即丢弃(防迟到写入新上下文;上游 _loadSeq 同位防线);
  AbortController 在切会话/新建/切租户/卸载触发(挂 navigate/effects)。
- StreamCard 组件:阶段指示(running=Loader2/primary,done=Check
  muted;selfheal 显示「第 N 次自愈重试」)+SQL 折叠(复用 Collapsible
  形态)+打字机文本区;终态后卸载,由 AssistantCard(实时构造
  SessionMessageOut 形态)+invalidateQueries 接管。
- 图标 lucide-react(禁 emoji);全部 shadcn 组件+Tailwind 原子类
  (前端样式铁律)。
- 流异常终止(无终态/网络错):按现有 ask.isError 形态呈现+问句回填。

## 7. 纵深防御(不动任何一层)

JWT → chat:execute → 租户白名单 → 数据源预检 → SQL 防火墙 → data_scope
→ 只读池——流式端点复用同一防线链(前段在流开始前完成判定);
SSE 传输形态不改变任一层判定位置与口径。agent 面零凭证不变(ADR-002)。

## 8. 风险与权衡

| 风险 | 缓解 |
|---|---|
| 后台 task 泄漏(agent 流挂死) | httpx 流式 timeout=agent_timeout_sec 语义为**读间隔**超时(非整链路总时长;字节间歇 <120s 可累计更久)——agent 挂死无字节时 120s 收敛 error+FAILED;消费循环 finally 必发终止标记(任何异常路径经 except Exception 收敛,评审 R1-S1 直证补齐) |
| 断线续存「用户以为没问上但落库了」 | 与同步中断语义一致(后端照跑完);会话历史回显即解释(上游同款取舍) |
| 阶段事件与真实执行粒度错位 | 事件在 wrapper(节点边界)发,粒度=图拓扑,不虚构子步骤 |
| summary 单条非 token 流 | 契约按「逐事件追加」立约,实现单条;token 流为纯实现演进零契约变化 |
| 016 基线回归 | 同步端点+同步 ainvoke 零行为变化;收工跑 evals smoke 复验基线 |
| queue 无界积压(前端慢) | 事件量小(每问答 ≤20 帧),帧文本 KB 级;不做背压(上游同款) |
