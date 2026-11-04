# Proposal: add-worker-context-readthrough

## Why

协作 worker 的派发上下文摘要与圆桌黑板转写都是**裸截断**(2000 字硬上限),截断处没有续读入口——worker 判断摘要不够用时无从获取完整上下文,只能带着残缺输入干活。「上下文窗口当 Cache 管理」(Inline & Spill)的最小落地形态就是:默认摘要受控下传,截断处附对象标识与按需读取工具——把「知道」变成「做过」上下文工程,同时不改体积受控原则。

## What Changes

- **截断处附续读入口**:worker 派发摘要与圆桌黑板转写在发生截断时,SHALL 附任务标识与续读工具提示(「摘要已截断,可用 task_context 工具读取全文」);不截断则不附,行为零变化。
- **agent 新增 `task_context` 只读工具**:worker 可按需读取**自身任务**的目标全文与已完成步骤结论;经 api 新内部端点 `/internal/task-context` 回调取数(零凭证纪律不变,ADR-002)。
- **授权不提权边界**:该工具只读任务自身上下文、不含任何数据源访问,进入 worker 工具交集的缺省集合;跨任务读取一律拒绝。
- **体积受控原则不变**:默认仍摘要下传,MUST NOT 整份下传(与「派发与上下文受控传递」既有约束一致);全文按需、单次读取。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `multi-agent-orchestration`: 「派发与上下文受控传递」requirement 增加——受控摘要截断时 SHALL 保留续读入口,worker SHALL 可按需只读获取自身任务完整上下文;受控下传原则不变。

## Impact

- **apps/agent/kongming_agent/**: `collab_gen.py`/`roundtable_gen.py` 摘要组装处附截断提示;工具注册新增 `task_context`(只读,进 worker 缺省工具集);prompts 侧登记约定。
- **apps/api/kongming/task/**: router 增 `/internal/task-context` 端点(X-Internal-Token,返回目标+已完成步骤结论);service 复用既有取数用例。
- **contracts**: api 新增 internal 端点,`make contracts-check` 核验,产物有漂移则重生成提交(ADR-005)。
- **schema(ADR-004)**: 零迁移。

## Non-goals

- **不做通用链式召回/引用图基础设施**:只做「摘要→自身任务全文」一跳;对象 ID 全图化(Trigger/Thread/Session 引用网)是后续独立形态。
- **不做圆桌黑板全文化**:黑板仍受控快照,只附续读入口;参与者彼此原始输出仍不可见。
- **不做跨任务/跨租户上下文读取**:worker 只能读自己任务;他任务调用一律拒绝(404 同形)。
- **不改摘要生成逻辑**:摘要内容与 2000 字上限不变,只加截断尾部入口。
- **不设 eval 通过阈值**:对照实验只记录数据(摘要 vs 全文的 worker 成功率/轮次),不立验收门槛——证据积累另册。
