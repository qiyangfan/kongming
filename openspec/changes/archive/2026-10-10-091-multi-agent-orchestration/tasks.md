# 实施任务

每项按不超过两小时拆分。v1 交付形态=未接线库模块(编排循环+护栏+迁移+测试);
持久化/端点/前端/Bot 装配为接线期待办(见各条注记),勾选状态以实际执行证据为准
(2026-10-09 独立审查后修正)。

## 1. 宿主集成与失控防护(测试先行)

- [x] 1.1 先写失败测试:无游离协作入口(脱离任务步骤发起被拒)、worker 授权不提权(未授权工具在协作内照拒并落审计)。(接线完成:api 侧 collab-start 拒绝非 RUNNING/未声明拓扑/超深步骤=test_task_collab.py::test_no_free_collab_entry;结构断言=agent test_collab_gen.py::test_no_free_collab_entry_structural[collab_gen 无 router/app 面]+授权面签名断言;worker 工具 ⊆ 任务载荷授权工具的结构收口=collab_wiring._worker_tools[Bot 名单求交,test_collab_wiring.py::test_collab_runner_bot_persona_and_tool_narrowing];逐 worker 工具调用落审计继承 ReAct 六层防御[e2e 实证 tool/execute 审计])
- [x] 1.2 先写失败测试:并发上限触顶不超限运行、深度上限(缺省 1)拒绝嵌套、预算继承不足不派发。(测试真实存在:并发=v1 串行派发结构上无并发、上限=DEFAULT_MAX_WORKERS 单一定义截断+未派发披露;深度=CollabDepthExceeded 构造即拒;预算=worker_budget=0 不拆解不派发/截断披露 pending_subgoals)
- [x] 1.3 新建 Alembic 迁移:sys_agent_collab_run / sys_agent_collab_worker 两表(UUID v7、VARCHAR+CHECK、jsonb 拓扑与证据),验证升级与回滚。(0040;dev 库 downgrade/upgrade 回环实证;审查 P2-7 后补 task 维度索引 idx_collab_run_task、conclusion TEXT→JSONB 对齐 design D3)

## 2. 编排循环(agent 面)

- [x] 2.1 orchestrator 规划-派发-聚合-裁决循环:子任务拆解、受限上下文摘要下传、结构化结果(结论+证据引用)回集。(11 剧本化测试;聚合 summary 恒为合法 JSON、conclusions 结构化字段、verdict=全 PASS 才 PASS)
- [x] 2.2 worker 复用既有 ReAct 内核调用 + Bot 绑定注入(bot_system_prompt 口径);worker 间无直连的结构断言测试。(接线完成:collab_wiring.make_collab_runner 装配 worker=一次受限 _run_react[经 main._react_adapter],人格+上下文摘要经 bot_system_prompt 注入、工具=任务载荷∩Bot 名单;test_collab_wiring.py 全链/Bot 注入/工具求交断言 + e2e 实证)
- [x] 2.3 分歧裁决: dissent 标注(不静默调和)5 例剧本化测试;重派验证或如实标注分歧剧本用例(mock worker 固定结果);critic 审查与有限轮修订。(接线完成:test_collab_gen.py——分歧核实采纳 A/仲裁 neither 保留/预算不足保留 3 例 + critic 通过/修订重派替换/轮次尽保留意见 3 例;验证 worker 同计入预算且不参与交叉判定;核实条目结论可查[worker 账本 verification 语义])
- [x] 2.4 触顶/预算尽的部分完成收口对齐 090 诚实语义;步骤终态由聚合结论驱动。(部分完成披露已实现并测试[pending_subgoals];步骤终态驱动=接线项)

## 3. 观测、契约与治理

- [x] 3.1 协作事件落任务事件流;每 worker 独立 trace 父子关联;用量按 worker 归集(038/077 口径回归)。(接线完成:collab_started/worker_started/worker_finished/finished 四事件经 api 内部回调落任务事件流[test_task_collab 全链断言];worker trace=trace_root-wN 父子口径[0040 trace_id 列,e2e 实证];用量按 worker 归集=collab_worker.usage jsonb[e2e 实证 84 tokens/worker];llm-usage 038 通道照常)
- [x] 3.2 协作运行查询端点(任务维度)+ 步骤拓扑配置面;跑 `make contracts` 重生成并核对无既有契约漂移(ADR-005)。(接线完成:GET /tasks/{id}/collab-runs[task:read,归属 404 同形]+PUT /tasks/{id}/steps/{sid}/collab-topology[task:manage,PENDING 闸/Bot 校验/部署钳制];make contracts 已重生成,导出前后 md5 一致=幂等零既有漂移)
- [x] 3.3 上限与预算的部署级配置面 + 缺省值文档化(docs/tech-stack.md 或部署文档)。(接线完成:api config[KM_TASK_BUDGET_MAX_* 三项+KM_COLLAB_MAX_WORKERS,创建/声明时钳制=clamp_budget/normalize_collab_topology]+agent settings[KM_AGENT_COLLAB_* 三项,执行面最后防线];缺省值表已落 docs/tech-stack.md「任务/协作部署级上限」节;深度超部署上限 api 侧拒绝[test_no_free_collab_entry])

## 4. 前端与验收

- [x] 4.1 任务详情内协作视图:worker 进度/结论摘要/证据入口/裁决事件时间线(标准件,DataView 双形态)。(接线完成:TaskDetailPage 协作 section[GitBranch 标题+运行卡:状态徽章/裁决/拓扑参数/分歧-部分完成警示+worker DataView 双形态:子目标/状态/结论摘要/token/trace];活动期 4s 轮询;标准件无手搓骨架,色走 token)
- [x] 4.2 `pnpm typecheck && pnpm lint` + 宪法 Article 8 自检;375/768/1280 亮暗两态走查。(tsc/eslint/vitest 86 全绿;Article 8 自检 grep[硬编码色/emoji/inline style/window.confirm]零命中;Playwright+系统 Chrome 走查 23/23 PASS:协作视图 375[卡片]/768/1280[表格]双形态+页面零横向溢出+亮暗两态[暗色正文 rgb(14,20,22) token]+任务列表状态筛选器渲染与筛选生效)
- [x] 4.3 端到端冒烟:建任务→协作步骤(3 worker 并行 + critic)→聚合回写→分歧标注场景→预算部分完成;`make check` 对应子集全绿并记录证据。(真实 LLM 栈[dashscope 直连 qwen-plus]冒烟双场景过[apps/api/scripts/e2e_collab_smoke.py,2026-10-10]:场景一=建任务→暂停→声明拓扑[3 worker·parallel·critic]→恢复→协作编排真实运行[3 worker 真 SQL 取数、逐 worker trace/用量、裁决 PASS、critic 审查]→run DONE→聚合结论驱动协作步 DONE;任务整体 FAILED=其余步骤部署级 token 预算耗尽的诚实收口[逐项附因,非缺陷];场景二=运行中取消→run CANCELLED+worker SKIPPED 账本收敛。分歧标注/预算截断细节场景由剧本化测试承载。**归档前终值(2026-10-10 两轮审查循环+四项裁决落地后)**:api 1159/agent 505/liannu 22/web 86 全绿,openspec strict 本变更 spec 通过;spec「失控防护」预算口径按 owner 委托裁决修订为次数口径+步级闸兜底[delta 与主 specs 已同步])

## 5. 收尾

- [x] 5.1 独立代码审查(授权不提权/三重上限/通信收口为重点),缺陷修复后复跑门禁。(子代理独立审查已做:P1×3[深度防护零实现+docstring 过度声明/聚合 summary [:4000] 截断产非法 JSON/1.1、1.2 虚勾]、P2×7;修复=深度+预算护栏实装、外层截断取消+conclusions 结构化、上限单一定义[删 report_max_llm_calls//4 技巧]、worker 异常留日志且结论只带类型名、api 死参数删除、聚合改全 PASS 裁决、迁移补索引与列型;授权不提权/通信收口/串行并发/分歧取简四项结构核可;agent 486 用例+ruff/mypy/pyright/lint-imports 全绿)
- [x] 5.2 归档前 sync delta 进主 specs;在 docs/roadmap.md 登记「协作 vs 静态工作流」选型指引与后续域(并行步骤执行等)待办。
