# 设计:开放式任务内核

## 范围与证据

- 现状:自主性 = 单请求 ReAct(轮次上限)+ 会话内 Todo 账本(tool-calling spec),任务随请求生灭;写工具已有挂起-确认-恢复闭环(同一请求内);进程重启无任务续跑语义。
- 本变更把「账本 + 确认 + 预算诚实」三原则从请求内提升为跨请求持久语义,不发明新原则。

## D1:任务状态住控制面,执行住 agent 面

任务/步骤/检查点/审批都是**事实与权限客体**,落控制面 `task/` 域包;「下一步做什么」的决策循环住 agent 面(与 ReAct 内核同范式):控制面经内部端点下发「当前任务上下文(目标+计划+最后检查点)」,agent 面执行一步(若干工具回调)后回写步骤结果与下一检查点。挂起/恢复 = 控制面状态机控制是否下发,agent 面无任务持久状态——重启后 agent 面无残余,恢复天然干净。ADR-002 零改动:工具执行仍 100% 经既有 `/internal` 回调。

否决「任务状态放 agent 面/Redis」:任务是审计与权限客体,必须住在持凭证侧且可事务落库;Redis 只做执行期锁与去重,不做真理源。

## D2:状态机与表

两表一次建全(ADR-004/VI):

- `sys_agent_task`(id UUID v7,tenant_id,user_id,goal,status VARCHAR+CHECK,budget_jsonb(配置)/budget_used_jsonb(消耗),chat_conversation_id 可空,trace_root,created/updated,cancel_reason 等);
- `sys_agent_task_step`(id,task_id,ord,goal,status VARCHAR+CHECK,evidence_jsonb,fail_reason,require_confirm,checkpoint_jsonb,updated)。

迁移白名单(合法状态对)以领域 models 层纯函数表达,非法迁移拒绝 + 审计;INTERRUPTED 只由启动扫描(RUNNING/PLANNING 存量标记)写入。检查点 = 步骤行自带(evidence + 上下文摘要),不单独建表——「最后完成步骤」即恢复点,最小 schema。

## D3:审批门复用既有确认流

步骤级审批复用既有写工具确认流的骨架:待确认记录(归属/过期/幂等键)、确认端点(归属校验)、过期处置。差异点:既有确认挂在「会话内工具调用」上,本变更挂在「任务步骤」上——待确认记录加 subject_type(task_step),确认后由任务调度驱动该步执行,执行仍过全量防御。不另造审批表,扩展现有待确认存储的归属维度。

## D4:执行循环与预算

agent 面任务循环 = 无界外层(步骤) + 有界内层(单步内 ReAct 轮次上限沿用既有配置)。预算三约束在控制面任务行原子累加(工具回调与 LLM 用量上报时),任一超限 → PAUSED;防超跑硬顶 = 单步执行前后各查一次预算。调度形态 v1 为「拉取式」:恢复/创建时由控制面触发一次执行请求,步间连续执行,挂起/预算尽/失败即停——不引入后台常驻队列(Non-goal 对齐宪法 I:不引入消息队列)。

## D5:观测与权限

任务事件 = 状态机每次迁移落事件(表内 jsonb 或轻事件行,实现取简),trace_root 贯穿所有步内工具调用与 LLM 调用,对齐 038 时间线。权限点 task:create/read/manage,默认角色映射 ANALYST 及以上(种子,部署可调)——安全规则类,失败测试先行(状态机非法迁移/归属 404/审批越权/预算暂停)。

## 风险与缓解

- 长任务半途模型/端点故障:单步失败 → 步骤 FAILED + 任务 PAUSED(非全局 FAILED),可恢复重试该步;预算摘要如实。
- 并发操作竞态(同时暂停与恢复):任务行乐观锁(版本号),冲突拒绝重试,与 088 报告并发编辑同模式。
- 检查点上下文膨胀:checkpoint 摘要有体积上限,超限截断标注——恢复后由 agent 面按需重新取数(有证据原则兜底)。
