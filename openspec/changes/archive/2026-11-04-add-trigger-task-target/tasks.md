# Tasks: add-trigger-task-target

## 1. Schema 与契约基座

- [x] 1.1 新 Alembic 迁移:触发器表 ADD COLUMN `task_template` JSONB(可空,注释说明仅 task 目标使用)
- [x] 1.2 `TriggerIn`/创建用例增 `task_template` 可选对象(模板校验:goal 模板非空/datasource 对创建者可见/budget 钳制预检/topology 成员声明期校验/confirm_auto/filter/thread_field 值域),`make contracts` 重生成

## 2. 判读端点(api ↔ agent)

- [x] 2.1 agent 新增 `POST /internal/judge-event`(X-Internal-Token):输入 source/summary/goal_preview,输出 `{verdict, reason}`;prompt 落 agent prompts 侧遵循 docstring 登记约定;端点单测
- [x] 2.2 api `AgentClient.judge_event`:独立短超时 + 降级契约(不可达/非 200/报文畸形 → None);单测覆盖降级路径

## 3. 过滤闸与线程去重(测试先行)

- [x] 3.1 先写失败测试(按 spec 场景):程序层筛除、语义否决、判读不可用 SKIPPED、CRON 免闸、同线程不重复立项且事件落既有任务、终态后重新立项
- [x] 3.2 实现过滤闸用例(程序层谓词求值 + judge_event 调用 + SKIPPED 落库,原因区分程序层/语义否决/判读不可用)
- [x] 3.3 实现线程去重(run summary 记 thread_key/task_id;未终态→append_task_event(kind=trigger_event)+SKIPPED;查询-后写不加锁,注释说明竞态取舍);3.1 全部转绿

## 4. 立项目标与确认门

- [x] 4.1 `TargetRunner` task 分支:创建者账号态闸 → 模板渲染(InputGuardrail + 载荷限界截断)→ `TaskService.create`(预算钳制)→ 确认模式分派;run 记 SUCCESS(target_run_ref=task_id, summary 记 thread_key)
- [x] 4.2 确认门:缺省落 PAUSED + `trigger_awaiting_confirm` 事件(fail_reason 注明触发器来源);`confirm_auto` 走既有创建派发路径;复用路径单测(恢复→编排进入)
- [x] 4.3 模板协作拓扑:计划生成后对首个 PENDING 步骤调 `set_step_topology`;应用失败落任务事件并以串行继续

## 5. 前端与门禁

- [x] 5.1 web 触发器表单增 task 形态(模板/过滤/确认开关/线程字段;标准件,窄屏走查);详情页展示 SKIPPED 原因与判读理由
- [x] 5.2 全套门禁:`make check` + `make contracts-check`;测试失败修复后重跑全套
