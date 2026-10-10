# Proposal: 多智能体协作编排

## Why

现有形态是「单 ReAct 内核 + 静态 DAG 工作流」:custom-agent 的 Bot 是各自独立的问答入口,workflow-orchestration 是人预先编排的静态图——都没有「运行时动态分工」:planner 把目标拆给多个 worker 并行/串行执行、critic 审查、结果聚合裁决。这是复杂分析任务(多源交叉验证、生成-审查分离、大目标分治)的质量与吞吐放大器,也是通用智能体的协作层;以 090 任务内核为宿主落地。

## What Changes

- 新增协作(collaboration)形态:一条协作 = orchestrator(planner/聚合者)+ N 个 worker + 可选 critic;worker 为既有 ReAct 内核实例(可绑定 custom-agent Bot 的人格与工具集)。
- 派发与聚合:子任务派发携带子目标 + 受控上下文摘要;worker 产出结构化结果(结论 + 证据引用);orchestrator 聚合,结论冲突时裁决(重查/标注分歧),SHALL NOT 静默丢弃分歧。
- 通信收口:worker 之间 MUST NOT 直连,一切经 orchestrator 上下文传递;worker 继承任务归属主体的授权集与数据范围,无提权。
- 失控防护:协作深度上限(协作嵌套层数)、并发 worker 上限、worker 级预算继承;触顶结构化终止 + 部分结果(对齐 ReAct 触顶语义)。
- 与任务内核集成:协作作为任务的执行策略——任务步骤可声明协作拓扑,步骤结果 = 聚合结论 + 各 worker 证据链。
- 观测:每 worker 独立 trace 且父子关联,协作总览时间线(派发/完成/裁决事件)。

## Capabilities

### New Capabilities

- `multi-agent-orchestration`: 运行时动态协作的拓扑、派发与聚合、通信收口、失控防护、任务集成与观测的全部行为契约。

### Modified Capabilities

(无——custom-agent 的 Bot 定义与 workflow-orchestration 的静态 DAG 行为零改动;协作是任务内核之上的新执行策略。)

## Impact

- `apps/agent`:协作编排器(规划/派发/聚合/裁决循环),worker 复用既有 ReAct 内核与 Bot 配置注入;工具调用仍 100% 经既有回调(ADR-002 零改动)。
- `apps/api`:协作运行记录(拓扑/派发/结果/裁决)持久化与查询端点,挂任务维度;权限沿用 task:* 体系 + 协作配置权限点(测试先行)。
- 数据库:协作运行与 worker 结果两表(ADR-004/VI;已规划列一次建全)。
- 契约:`make contracts` 重生成(ADR-005)。
- `apps/web`:任务详情内协作视图(worker 进度/结果/裁决时间线,标准件)。
- 成本面:并发 worker 放大 token 消耗——并发/深度上限与预算继承为第一道闸,用量按 worker 归集(038)。

## Non-goals

- 不做 worker 间点对点通信/共享黑板——通信恒经 orchestrator(治理与审计单点)。
- 不做自主无限 spawn:worker 数量与嵌套深度由上限硬约束,超限拒绝。
- 不做跨租户协作/多租户 worker 混合授权。
- 不替代 workflow-orchestration 的静态 DAG(确定性流水线场景两者并存,选型指引进 docs)。
- 不做 agent 市场/竞价/谈判类经济机制。
- 不引入消息队列做 worker 调度(宪法 I 禁项不变)。
