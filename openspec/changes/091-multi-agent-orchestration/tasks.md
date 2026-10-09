# 实施任务

每项按不超过两小时拆分。v1 交付形态=未接线库模块(编排循环+护栏+迁移+测试);
持久化/端点/前端/Bot 装配为接线期待办(见各条注记),勾选状态以实际执行证据为准
(2026-10-09 独立审查后修正)。

## 1. 宿主集成与失控防护(测试先行)

- [ ] 1.1 先写失败测试:无游离协作入口(脱离任务步骤发起被拒)、worker 授权不提权(未授权工具在协作内照拒并落审计)。(v1 未实施:模块无任何入口/端点——未接线即无游离面;授权不提权以结构断言测试锁定(run_collab 无 api/token/credentials/tools 参数、WorkerRunner 签名仅 subgoal+context),工具照拒与审计继承既有 ReAct 回调六层防御;游离入口拒绝与协作级审计断言在接线时补)
- [x] 1.2 先写失败测试:并发上限触顶不超限运行、深度上限(缺省 1)拒绝嵌套、预算继承不足不派发。(测试真实存在:并发=v1 串行派发结构上无并发、上限=DEFAULT_MAX_WORKERS 单一定义截断+未派发披露;深度=CollabDepthExceeded 构造即拒;预算=worker_budget=0 不拆解不派发/截断披露 pending_subgoals)
- [x] 1.3 新建 Alembic 迁移:sys_agent_collab_run / sys_agent_collab_worker 两表(UUID v7、VARCHAR+CHECK、jsonb 拓扑与证据),验证升级与回滚。(0040;dev 库 downgrade/upgrade 回环实证;审查 P2-7 后补 task 维度索引 idx_collab_run_task、conclusion TEXT→JSONB 对齐 design D3)

## 2. 编排循环(agent 面)

- [x] 2.1 orchestrator 规划-派发-聚合-裁决循环:子任务拆解、受限上下文摘要下传、结构化结果(结论+证据引用)回集。(11 剧本化测试;聚合 summary 恒为合法 JSON、conclusions 结构化字段、verdict=全 PASS 才 PASS)
- [ ] 2.2 worker 复用既有 ReAct 内核调用 + Bot 绑定注入(bot_system_prompt 口径);worker 间无直连的结构断言测试。(部分:worker 间无直连结构断言已测[WorkerRunner 签名+通信收口核可];ReAct 内核装配与 Bot 绑定注入=组合根接线项,v1 以 worker_runner 抽象位承接)
- [ ] 2.3 分歧裁决: dissent 标注(不静默调和)5 例剧本化测试;重派验证或如实标注分歧剧本用例(mock worker 固定结果);critic 审查与有限轮修订。(部分:dissent 标注测试真实存在;重派验证与 critic 审查/有限轮修订 v1 未实施,以交叉裁决[非全 PASS 即 ERROR]+dissent 标注取简,接线时按 design 补 critic)
- [x] 2.4 触顶/预算尽的部分完成收口对齐 090 诚实语义;步骤终态由聚合结论驱动。(部分完成披露已实现并测试[pending_subgoals];步骤终态驱动=接线项)

## 3. 观测、契约与治理

- [ ] 3.1 协作事件落任务事件流;每 worker 独立 trace 父子关联;用量按 worker 归集(038/077 口径回归)。(v1 未实施:0040 两表已建含 usage/trace_id 列,写入在接线时落)
- [ ] 3.2 协作运行查询端点(任务维度)+ 步骤拓扑配置面;跑 `make contracts` 重生成并核对无既有契约漂移(ADR-005)。(v1 未实施:无端点无契约面,零漂移即现状;接线时补)
- [ ] 3.3 上限与预算的部署级配置面 + 缺省值文档化(docs/tech-stack.md 或部署文档)。(v1 未实施:上限为模块常量 DEFAULT_MAX_WORKERS/DEFAULT_MAX_DEPTH 并在模块 docstring 文档化;部署级配置面接线时补)

## 4. 前端与验收

- [ ] 4.1 任务详情内协作视图:worker 进度/结论摘要/证据入口/裁决事件时间线(标准件,DataView 双形态)。(v1 未实施:后端无数据面)
- [ ] 4.2 `pnpm typecheck && pnpm lint` + 宪法 Article 8 自检;375/768/1280 亮暗两态走查。(协作视图不存在,无页面可走查;web 全仓门禁 tsc/eslint/vitest86 绿,任务页走查证据见 090 tasks.md 4.2)
- [ ] 4.3 端到端冒烟:建任务→协作步骤(3 worker 并行 + critic)→聚合回写→分歧标注场景→预算部分完成;`make check` 对应子集全绿并记录证据。(v1 未接线,端到端不存在;剧本化全链=20 用例绿[486 总],接线时补真端到端)

## 5. 收尾

- [x] 5.1 独立代码审查(授权不提权/三重上限/通信收口为重点),缺陷修复后复跑门禁。(子代理独立审查已做:P1×3[深度防护零实现+docstring 过度声明/聚合 summary [:4000] 截断产非法 JSON/1.1、1.2 虚勾]、P2×7;修复=深度+预算护栏实装、外层截断取消+conclusions 结构化、上限单一定义[删 report_max_llm_calls//4 技巧]、worker 异常留日志且结论只带类型名、api 死参数删除、聚合改全 PASS 裁决、迁移补索引与列型;授权不提权/通信收口/串行并发/分歧取简四项结构核可;agent 486 用例+ruff/mypy/pyright/lint-imports 全绿)
- [x] 5.2 归档前 sync delta 进主 specs;在 docs/roadmap.md 登记「协作 vs 静态工作流」选型指引与后续域(并行步骤执行等)待办。
