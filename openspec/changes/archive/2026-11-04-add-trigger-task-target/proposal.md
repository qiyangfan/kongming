# Proposal: add-trigger-task-target

## Why

trigger 域已有 CRON/WEBHOOK/EMAIL 三类事件源与 workflow/bot 两类目标,但外部事件**接不进任务编排**(`target_type` 限 `workflow|bot`)——定时巡检、webhook 告警、邮件请求都无法自动立项走多步任务与协作拆解。事件驱动立项是通用智能体方向中协作消费的下一形态:自主的是**发起**,受约束的是**执行**——事件只能唤醒系统按显式模板立项,立项后照常过预算、审批门、SQL 六层防御与账本审计。这是审计优先定位下「主动性」的正确形态,也是事件驱动的 agent 架构(DeepSeek super-agent 类设计)在本项目的落地内核。

## What Changes

- **target_type 扩展 `task`**:触发器配置携带**任务模板**(目标文本模板/数据源 key/预算/可选协作拓扑/确认模式),取代 target_key 对既有实体的引用;模板校验取代 target_key 存在性校验(数据源对创建者可见性 fail-fast)。
- **两级事件过滤闸**:webhook/email 事件先过**程序层确定性条件**(适配器动作/字段匹配,调用 LLM 前筛除噪音),候选事件再经 agent **单次语义判读**(是否值得立项);判读不可用 → 记 SKIPPED run 不立项;CRON 不经语义判读(定时意图在创建期已确定)。
- **线程键去重**:同线程键(如 GitHub repo#issue)存在未终态触发任务时,新事件 SHALL 追加为该任务账本事件而非重复立项。
- **确认门**:触发创建的任务**默认落 PAUSED 等归属人确认**(恢复即派发,零新状态零新端点,复用既有恢复语义),可按触发器配置自动派发;无论哪种模式,预算钳制、步骤审批门、事件账本照常。
- **主体验证与注入防线复用**:任务以**触发器创建者身份**创建(既有 creator 账号态闸复用,无权限放大);事件载荷渲染进 goal 前经既有 InputGuardrail 文本护栏;任务执行照常走六层防御。
- **agent 新增 `/internal/judge-event` 单次判读端点**(api 不直连 LLM,ADR-002);api 侧 AgentClient 增对应方法与降级契约。
- **schema(ADR-004)**:触发器表新增任务模板配置列(JSONB 单列),走新 Alembic 迁移。
- **契约(ADR-005)**:TriggerIn/CreateTrigger 增字段,`contracts/openapi.json` 与 web 类型重生成。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `triggers-webhooks`: ①「触发器管理与执行历史」requirement 修改——target_key 存在性校验收窄为 workflow/bot 引用目标,task 目标以模板校验替代;②新增「task 立项目标」「两级事件过滤」「线程去重与事件归属」「触发任务确认门」四个 requirement。

## Impact

- **apps/api/kongming/trigger/**: models/service(create 校验分支+过滤闸+线程去重)/router(TargetRunner/Checker 的 task 分支+TriggerIn 字段)/repository(模板列读写)/ports(TargetChecker 语义扩展)/adapters(线程键提取与程序层谓词)。
- **apps/api/kongming/task/**: service 复用(创建+预算钳制+拓扑应用);router 组合根接线。
- **apps/agent/kongming_agent/**: `/internal/judge-event` 端点与判读 prompt(X-Internal-Token,与 judge 端点族同构)。
- **apps/api/kongming/chat/agent_client.py**: `judge_event` 方法(降级契约对齐既有判读方法)。
- **apps/api/alembic/**: 新迁移(触发器表模板列)。
- **contracts + apps/web**: OpenAPI 导出与前端类型重生成;触发器表单页增 task 目标形态(模板/过滤/确认模式编辑)。
- **零信任边界触碰**: 判读走 agent 内部端点;任务执行零凭证纪律原样(ADR-002)。

## Non-goals

- **不做订阅直通**:worker 保持快照式(派发时输入固定);线程内后续事件只落任务账本供审计与详情可见,不进入运行中的执行——持续订阅是后续独立形态。
- **不做投机执行**:判读通过≠立即执行,默认确认门后置;自动派发模式也是「立项即走正常编排」,不含未确认的后台预执行。
- **过滤闸不扩展到既有 workflow/bot 目标**:既有两类目标行为零回归,闸门本期仅挂 task 目标;扩展另议。
- **不做跨触发器事件总线/复杂规则引擎**:过滤条件是触发器级确定性配置,不是全局事件流处理。
- **不做多副本选主**:与 trigger 调度器同口径(单进程形态)。
- **不引入消息队列/流处理**:事件处理在既有调度/入站路径内同步完成(ADR-001 技术栈否决清单)。
- **不改任务状态机**:确认门复用 PAUSED→恢复既有语义,零新状态。
