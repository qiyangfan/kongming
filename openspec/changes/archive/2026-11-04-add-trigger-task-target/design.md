# Design: add-trigger-task-target

## Context

trigger 域的执行骨架已就位:`TargetRunner.run_target` 端口以**定义创建者身份**执行并自带创建者账号态闸(trigger/router.py 组合根),`TargetChecker` 做写边界 fail-fast,run 链 + 重试 + 出站通知齐备;任务域 `TaskService.create` 接受 goal/datasource_key/budget 并经部署级上限钳制,创建即后台派发;`InputGuardrail` 端口已为邮件外部输入设防;agent 已有单次判读端点族(`/internal/score-answer` 等 + AgentClient 降级契约)。缺的只有三块:task 目标形态本身、事件→立项之间的过滤闸、同线程去重。

## Goals / Non-Goals

- Goals: 事件→立项的最短可信路径(过滤闸 + 模板 + 确认门);主体与防御零新面(全部复用既有闸);契约与前端一次性到位。
- Non-Goals(设计层): 不为「运行中任务接收线程事件进执行」铺路(事件只落账本);不做判读结果的缓存/学习;不做跨触发器的线程键全局视图。

## Decisions

### D1: 模板存储 = 触发器表新增单列 `task_template` JSONB

模板(goal 文本模板/datasource_key/budget/collab_topology/confirm_auto/filter/thread_field)整体落一列可空 JSONB:触发器行的读写本就是整行取用,无按字段查询需求,单列避免列爆炸与多次迁移。备选拆多列不取。可空列对存量行零影响,迁移只有 ADD COLUMN。`filter`(程序层条件)与 `thread_field` 放模板内而非触发器行独立列:它们只对 task 目标有意义,生命周期与模板一致。

### D2: 过滤闸位置 = 适配器标准化之后、`_fire` 之前,webhook/email 共用

闸做成 service 层用例(gate):程序层谓词(模板 `filter`:动作/字段等值匹配,由适配器提供确定性求值)不通过 → `insert_run(SKIPPED, reason)` 返回,零 LLM 调用;通过 → `AgentClient.judge_event` 单次判读,`create|skip` + 理由,`skip` 或判读不可用(降级 None)→ 同样 SKIPPED(原因区分)。既有 SKIPPED 语义「不触发重试」恰好是判读否决想要的语义,零改重试策略。CRON 路径不经闸(spec 已豁免:无事件载荷)。判读输入只含适配器标准化摘要(邮件已是四字段白名单口径),不含原始载荷与凭据。

### D3: 判读端点与 prompt 进 agent,同构 judge 端点族

`POST /internal/judge-event`(X-Internal-Token):输入 `{source, summary, goal_preview}`(模板渲染预览让判读知道「立的是什么项」),输出 `{verdict: create|skip, reason}`。prompt 落 agent prompts 侧并遵循其 docstring 登记约定(测试锚定)。api 侧 `AgentClient.judge_event` 独立短超时 + 既有降级契约。api 不直连 LLM(ADR-002)。

### D4: 立项执行 = TargetRunner task 分支,复用 creator 闸与护栏

`_TargetRunnerImpl.run_target` 增 task 分支:创建者账号态闸(与 workflow 分支同款)→ 模板渲染 goal(事件载荷限界截断 + InputGuardrail 文本护栏,拒绝即 FAILED run)→ `TaskService.create`(budget 经 `clamp_budget` 部署钳制)→ 确认模式分派(见 D6)→ 返回 `(True, task_id, "")`。触发 run 的 SUCCESS = 立项成功;任务后续失败/完成归任务域账本,不回写触发器成败——两个生命周期不耦合。run 失败重试语义沿用(立项动作失败=数据库/约束错误,重试有意义)。

### D5: 线程去重 = run 行既有字段承载,零新关联表

「同线程键未终态任务」的判定走既有数据:触发器最新 SUCCESS run 的 `summary`(JSONB)记 `thread_key` 与 `task_id`,查询该 run → 反查任务状态,未终态则 `append_task_event(kind=trigger_event)` 追加事件并 SKIPPED 本轮(原因「线程已有在途任务」),终态则正常立项。备选任务表加 trigger 关联列(动任务域 schema,越界)与新关联表(为查询建表,过度)均不取。同线程并发事件的查询-后写竞态窗口不做加锁:后果是重复立项两个可审计可取消的任务,与单进程口径(Non-goal 多副本)相称。

### D6: 确认门 = 复用 PAUSED + 既有恢复,零新状态零新端点

缺省模式:立项即 `TaskService.create` 落 PAUSED + 任务事件 `trigger_awaiting_confirm`(fail_reason 注明来源触发器),不调派发;归属人恢复(PAUSED→RUNNING 既有白名单迁移 + `_bump_and_schedule`)即进入编排——恢复端点、权限点(task:manage)、检查点语义全部现成。`confirm_auto=true` 时走既有创建派发路径。不引入「待确认」新状态:状态机加态是全局契约变更,PAUSED + 事件留痕的表达力足够且前端任务列表天然可见。

### D7: 模板协作拓扑 = 计划生成后经既有声明路径应用

模板含 `collab_topology` 时,计划生成后对首个 PENDING 步骤调用既有 `set_step_topology` 用例(校验规则与手工声明完全同源);应用失败(bot 已删等)落任务事件并以串行形态继续——声明意图可见,不静默丢弃,也不让拓扑失败炸掉整个任务。备选创建期校验全部成员(只能覆盖 bot 存在性,拦不住运行期漂移)与失败即 FAIL 任务(把配置漂移升格为任务事故)均不取。

### D8: 契约与前端一次性到位

`TriggerIn` 增 `task_template` 可选对象 → `make contracts` 重生成 OpenAPI 与 web 类型(ADR-005);web 触发器表单增 task 形态(模板编辑/过滤条件/确认开关/线程字段),详情页展示 run 的 SKIPPED 原因与判读理由。表单遵循宪法标准件,无新组件。

## Risks / Trade-offs

- [高频 webhook 拖垮判读预算] → 程序层先筛 + SKIPPED 不重试 + 判读单次短超时;run 历史可观测判读命中率,阈值治理后置。
- [事件载荷注入 goal 劫持任务] → InputGuardrail 既有护栏 + 载荷限界截断 + 结构化定界渲染;即便注入得手,SQL 六层防御与预算/审批门框死爆炸半径;判读输入只含标准化摘要。
- [PAUSED 复用的语义歧义(用户暂停 vs 待确认)] → 事件与 fail_reason 注明「触发立项待确认」,web 详情可见来源;不为此加状态。
- [判读误否决有价值事件] → run 历史留判读理由可回溯;程序层条件与模板措辞由配置侧调整,不做判读自动调优。
- [模板引用的 bot/数据源运行期漂移] → 创建期 fail-fast + 派发期同源校验 + 拓扑失败留痕继续;止损路径(停用/删触发器)不受影响(spec MODIFIED 已保留)。

## Migration Plan

新 Alembic 迁移 ADD COLUMN `task_template`(可空)→ 部署 → `make contracts` 重生成随功能提交。存量触发器行为零变化(新分支仅 target_type=task 命中)。回滚:停用/删除 task 触发器 + 迁移降级删列;无数据残留(触发任务本身是合法任务实体,保留无害)。
