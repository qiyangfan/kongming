# Design: add-task-watchdog

## Context

在途任务的既有防线全部是「事件到达时」的拉取式闸门(`begin_step` 逐步复核状态/代际/预算)或「进程重启时」的一次性清扫(`mark_interrupted_stale`),中间没有持续观察者。可复用的既有机制:任务事件账本(`events` JSONB 数组,`append_task_event` 每次写入同步 bump `updated_at`,repository.py:450)、派发代际(`bump_dispatch_epoch` 原子自增 + 全回调失配拦截)、条件状态迁移(`transition_task_system` 的 from/to/expect_epoch CAS)、agent 单次判读端点族(`/internal/score-answer` 等 + AgentClient 降级契约)。任务墙钟预算 900s、编排 HTTP 超时 960s(config.py:118-122)。

## Goals / Non-Goals

- Goals: 停滞任务在传输层超时**之前**被收口;误杀防线(判读闸)与完全可审计(每个动作落账本);零 schema 迁移,开关回滚即回到现状。
- Non-Goals(设计层补充,proposal 已含产品层): 不取消进程内旧编排协程(见 D4);不做跨副本选主;不让判读影响对话链路(独立短超时,循环永不阻塞在判读上)。

## Decisions

### D1: 巡检循环放 api 进程,并入 lifespan 常驻循环形态

api 持有 DB 会话与任务仓储,巡检本质是 DB 扫描 + 编排夺权,天然属于控制面;agent 零凭证无 DB(ADR-002),独立巡检进程则引入新部署单元,均不取。实现为 `main.py` 第五个 `asyncio.create_task` 常驻循环 + 收尾 cancel,形态对齐既有四循环与 `start_trigger_scheduler`。循环内逐任务处理且全部走 CAS/epoch,即便未来多副本部署,重复扫描也无害(重复判读窗口极小、收口单次生效),选主问题后置。

### D2: 心跳 = `updated_at`,不解析 JSONB、不加列

`append_task_event` 与状态迁移均已维护 `updated_at = now()`,直接以「RUNNING/PLANNING 且 `updated_at` 早于 now() − stale_sec」为候选谓词。备选:`events->-1->>'at'` 尾元素解析(查询与索引复杂)或新增 `last_event_at` 列(违反零迁移)。宽心跳的代价是「非进展性写也计为活动」,方向偏安全(少误杀),由 D3 判读闸补偿语义精度。

### D3: 两段式决策——单次判读 + 机械兜底,单任务判读上限一次

候选停滞 → 调 `/internal/judge-stagnation` 判读一次:「仍进展」落 `watchdog_judged` 事件(顺带 bump 心跳,窗自然重置);「停滞」走 D4 收口;判读不可用(降级契约返回 None)视同已判读,进入二次观察窗,二次超窗直接机械收口(原因注明「判读不可用」)。此后任务再无任何判读调用——单任务生命周期判读上限 1 次,成本上界确定。备选:每窗重复判读(成本无上界)与纯机械阈值收口(会误杀合法的长工具调用段)均不取。

### D4: 夺权 = epoch 自增 + CAS 迁移,不取消进程内旧协程

收口序列:`bump_dispatch_epoch` 原子自增 → `transition_task_system(from RUNNING, to INTERRUPTED, expect_epoch=新代数, fail_reason, event=watchdog_taken_over)`。旧循环的后续回调(`begin_step`/终态回写)因代际失配被既有闸门拒绝,延迟返回的 HTTP 调用最多再占用至 960s 传输层超时,内存占用有界。备选「维护 task_id→asyncio.Task 映射主动 cancel」不取:新增映射结构与取消竞态面,换来的只是提前释放内存。谓词未命中(已被他人迁移)即放弃并留 debug 日志,与 `begin_step` 既有防御同款。

### D5: 判读端点进 agent,api 不直连 LLM

`/internal/judge-stagnation`(X-Internal-Token 守门)与 `/internal/score-answer` 同构:输入目标、事件尾部若干条、滞留秒数,输出 `{verdict: stagnant|progressing, reason}`;prompt 落 agent prompts 侧并遵循其 docstring 登记约定。api 侧 `AgentClient.judge_stagnation` 独立短超时(20s)+ 既有降级契约(不可达/非 200/报文畸形 → None)。

### D6: 阈值关系满足「看门狗先于超时」

默认 `watchdog_stale_sec=300`、判读后二次窗同为 300,最坏路径 300+300+判读耗时 < 900s 墙钟预算 < 960s 超时——看门狗保证先于传输层超时生效,这是「提前收口」价值的下界;`watchdog_interval_sec=60` 决定感知粒度。三值均进 config,Field 钳制惯例(ge 约束)照旧。

### D7: 账本事件 kinds 与前端兜底

新增事件 kind 仅两个:`watchdog_judged{verdict, reason}` 与 `watchdog_taken_over{reason, epoch}`。任务详情事件流为既有接口,新 kind 随事件自然可见;实现时核验 web 端事件渲染是否存在 kind 白名单映射,有则补两个 kind 的展示(兜底显示原始 kind),无则零前端改动。

## Risks / Trade-offs

- [误杀合法停滞观感的长调用] → 判读闸 + 宽心跳(D2)+ 阈值远小于墙钟;误杀场景在账本留有判读理由,可审计可回溯。
- [判读调用本身挂起拖慢巡检] → 独立 20s 短超时 + 降级契约;巡检循环串行处理候选,候选规模 = 在途任务数,天然有界。
- [与编排循环/恢复操作的并发竞态] → 全部动作走既有 CAS + epoch 纪律,谓词未命中即放弃;无新并发原语。
- [LLM 判读给出错误「仍进展」] → 判读后二次窗机械收口兜底,错误判断最多延长一个观察窗,不会无限放行。

## Migration Plan

零迁移、零新表、零外部 API 变更。部署即生效;回滚仅需 `watchdog_enabled=false`(或环境变量等价物),行为完全回到现状,无数据残留需要清理(watchdog 事件作为历史账本保留无害)。
