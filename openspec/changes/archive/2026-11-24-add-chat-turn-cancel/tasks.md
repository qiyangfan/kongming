# Tasks: add-chat-turn-cancel

> 顺序即依赖:迁移先行 → Agent 面能停 → 控制面能取消 → 质量面表态 → 前端
> 改交互 → 收口。安全规则类(归属校验)与取消编排均测试先行。

## 1. 数据库:消息状态第五态(ADR-004)

- [x] 1.1 先写失败测试(api 集成测试):status=CANCELLED 的 assistant 消息
      可写入;非法状态仍被约束拒绝——确认当前为红
- [x] 1.2 新增 Alembic 迁移(编号按当时最新顺延):放宽 `chat_message.status`
      的 CHECK 纳入 CANCELLED,降级路径为精确逆;HANDOFF 记下「迁移回滚前
      须先处理 CANCELLED 行」——验证:升级/降级往返成功,1.1 转绿
- [x] 1.3 评审补漏:取消链路按 trace_id 取该轮消息(收敛查重与已结束轮次的
      回答)原先无索引支撑——执行计划驱动自会话位图扫描、trace_id 只作过滤,
      取消处理延迟随该用户历史消息量增长。新增迁移 `0069` 建
      `idx_chat_message_trace`——验证:开发库升级/降级往返,执行计划改走
      该索引(Index Cond: trace_id)

## 2. Agent 面:在途问答中止端点

- [x] 2.1 先写失败测试(apps/agent/tests,慢图替身):①流式进行中携正确令牌
      请求中止→图执行被取消、其后不进入新节点、不再发起模型调用;②trace_id
      未知或已结束→返回「未在执行」;③无令牌或错令牌→403 且不中止;
      ④常规问答图与工具调用内核两个流式入口各覆盖一例——确认红
- [x] 2.2 实现:两处流式帧生成器把图执行放进自有任务并按 trace_id 登记
      (任务结束即注销),生成器只排空帧队列;既有异常收敛原样搬入该任务,
      行为逐字不变;新增中止端点(既有内部令牌中间件覆盖)——验证 2.1 全绿
- [x] 2.3 agent 既有流式用例零回归——验证 `make check` 的 agent 子集
      (pytest/ruff/mypy/pyright/lint-imports)全绿并在 apply 记录

## 3. 控制面:在途轮登记、取消编排与取消端点(ADR-002/ADR-005)

- [x] 3.1 先写失败测试(api 单测:假 AgentClient + 假流存储):①登记与注销
      随后台任务生命周期;②取消→通知 Agent 面→本轮收敛为已取消终态
      (persist=true 落 CANCELLED 行并带取消前轨迹;persist=false 不落库);
      ③已收到正常终态后到达的取消不取消任务,按已完成返回;④按 trace_id
      查重,同一轮不出现两条 assistant 行;⑤通知 Agent 面失败或超时→硬取消
      本地任务,仍落已取消;⑥取消标记未置时被外部取消→原样上抛——确认红
- [x] 3.2 先写失败测试(取消端点,安全规则测试先行):①本人取消在途轮→
      CANCELLED;②他人 trace_id→404,与不存在同形,该轮不受影响;③无
      `chat:execute`→403;④已完成(persist=true)→COMPLETED 携消息标识;
      ⑤已结束未落库(persist=false)→404;⑥流式响应头 `X-Trace-Id` 等于
      该轮 trace_id——确认红
- [x] 3.3 实现:chat 域新增在途轮登记(进程内,按「用户 + trace_id」隔离,
      经 Protocol 注入)、已取消终态落库(裸终态行 + 查重)、消费任务的取消
      收敛、取消编排与端点;AgentClient 增中止调用(限时,失败只记日志);
      HANDOFF 记下「在途登记是进程内的,单节点部署形态下才保证命中」——
      验证 3.1/3.2 全绿
- [x] 3.4 契约:取消端点与出参模型入 OpenAPI;流式端点 200 响应声明
      `X-Trace-Id` 头;`make contracts` 再生产物——验证契约检查绿,
      生成的前端类型含新端点
- [x] 3.5 评审补漏(ai-code-review):取消收敛的查重读取若抛非 ChatStoreError
      (连接中断一类),异常会逃出 `persist_cancelled`——收敛任务告终、流以
      无终态帧收场(前端看不到「本轮已取消」)、取消端点 500。先补回归测试
      (读必失败→不抛、按裸已取消终态返回且不写行;再经消费任务端到端:
      终态帧照常下发、取消结果为已取消且无落库标识),再修:查重单独容错,
      失败即记日志并不写行(失败会话上的插入必再失败,绕过查重续写有同轮
      双行风险)——验证新测试转绿、既有取消用例零回归

## 4. 质量观测面:排除已取消轮次

- [x] 4.1 先写失败测试:窗口内含已取消轮次时,质量汇总的总轮次/四态/各
      比率、按天趋势、明细分页、同会话串联、导出均不含该轮;按 trace_id 的
      用量时间线仍可见其取消前的调用——确认红
- [x] 4.2 实现:quality 仓储的轮次范围统一排除 CANCELLED(各查询同源)——
      验证 4.1 全绿
- [x] 4.3 api `make check` 子集(pytest/ruff/format/mypy/pyright/lint-imports)
      全绿并在 apply 记录

## 5. 前端:真取消交互

- [x] 5.1 vitest 纯函数先行:①取消结果→提示文案映射(成功/未送达/已结束/
      流未开始);②终态快照映射 verdict=CANCELLED→status=CANCELLED——
      先确认红
- [x] 5.2 实现:读流前取响应头 `X-Trace-Id`;停止按钮中止读取并调用取消
      端点,按结果提示;停止提示文案随结果变化;历史回显新增已取消说明卡
      (无反馈/纠错/再执行/导出/执行计划入口)——验证 5.1 全绿,
      `pnpm typecheck && pnpm lint && pnpm test:run` 全绿
- [x] 5.3 走查:375/768/1280 × 亮暗两态检查对话页(停止后提示、历史已取消卡);
      宪法 Article 8 自检 grep——记录走查结果。2026-11-24 做完:抓出并修掉
      停止提示被空态吞掉、停止后已取消卡片不自行出现、严格模式双发请求三个问题
      (见 HANDOFF 当前状态与验收记录)

## 6. 收口

- [x] 6.1 spec 增量 sync 进主 specs(query-streaming/chat/observability/
      nl2sql),`openspec validate --all --strict` 绿
- [x] 6.2 roadmap 第四梯队登记本变更,并记下「语音通话(待立项)以本变更
      为前置」
- [x] 6.3 手工冒烟(真实 LLM):①慢查询中途点停止→返回 CANCELLED,历史出现
      已取消卡,`sys_llm_usage` 中该 trace 不再新增行;②中流刷新→后台照常
      完成(断线续存零回归);③流式中切会话→不取消;④取消后立即重发同一问→
      新一轮正常;⑤停掉 Agent 面后点停止→仍落已取消(兜底路径)——记录结果
- [x] 6.4 全量 `make check` 绿并在 apply 记录——2026-11-24:api-check 1781 passed/
      10 skipped(覆盖率 77.22%)、agent-check 626 passed、liannu-check 23 passed、
      web 192 tests + 2 e2e;dead-code、dup-check、deps-audit、license-check、
      lock-check、compose-check、security-cov、secret-scan、spec-check(57/57)、
      lint-config 同批通过。contracts-check 在未提交树上必红(与已提交版本比对),
      以「重复生成 `contracts/openapi.json` 零差异」为准,提交后复跑转绿

## Workflow follow-up

- 按 ai-code-review 走评审循环,修复后归档。2026-11-24 已评一轮并修复两个
  已证实缺陷(见 1.3、3.5);评审中另辨明若干疑点为非问题或多实例部署的
  已知限制(记录在 HANDOFF)。
