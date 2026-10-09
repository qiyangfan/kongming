## Purpose

在任务内核之上提供**运行时动态多智能体协作**:orchestrator 把目标拆解为子任务派发给多个 worker(既有 ReAct 内核实例,可绑定 Bot 人格与工具集),可选 critic 审查,聚合裁决产出结构化结论。通信收口于 orchestrator、授权不提权、深度/并发/预算三重失控防护——治理模式与单内核一致,无新执行通道(ADR-002 零改动)。

## Requirements

### Requirement: 协作拓扑

一条协作 SHALL 由 orchestrator(planner + 聚合者)、一至多个 worker、可选 critic 构成。worker SHALL 为既有 ReAct 内核实例:可绑定 custom-agent 的 Bot 定义(系统提示与工具集),绑定校验沿用 Bot 运行时权限继承口径。协作 SHALL 声明拓扑参数:worker 数量上限、是否启用 critic、子任务分配策略(并行/串行)。协作 MUST 以任务(task-kernel)步骤为宿主发起——不提供脱离任务的游离协作入口;步骤声明协作拓扑后,该步的执行即协作,步骤终态由聚合结论驱动。

#### Scenario: 任务步骤发起协作

- **GIVEN** 任务步骤「交叉验证三个数据源口径」声明协作拓扑(3 worker 并行)
- **WHEN** 该步执行
- **THEN** orchestrator 拆解派发 3 个子任务,聚合结论回写步骤(证据含各 worker 引用)

#### Scenario: worker 绑定 Bot 人格

- **GIVEN** 协作某 worker 绑定「财务分析」Bot(限定财务工具集)
- **WHEN** 该 worker 执行子任务
- **THEN** 其系统提示与工具集按 Bot 定义注入,运行时权限继承校验照常生效

#### Scenario: 无游离协作

- **GIVEN** 任意调用方试图绕过任务直接发起协作
- **WHEN** 请求到达
- **THEN** 拒绝(协作必以任务步骤为宿主),落审计

### Requirement: 派发与上下文受控传递

orchestrator 派发子任务 SHALL 携带:子目标、受控上下文摘要(任务目标 + 相关已完成结论)、可用能力提示;MUST NOT 把整份任务历史原样下传(上下文体积受控)。worker 产出 SHALL 为结构化结果:结论 + 证据引用(工具调用/查询快照引用)+ 置信表述;worker 间 MUST NOT 直连——一个 worker 的输出 SHALL NOT 直接进入另一 worker 的输入,一律经 orchestrator 聚合后再决定是否作为后续子任务的上下文。

#### Scenario: 上下文受控下传

- **GIVEN** 任务已有 6 步完成结论
- **WHEN** orchestrator 派发新子任务
- **THEN** 下传的是与子目标相关的摘要,非全量历史,体积有上限

#### Scenario: worker 输出经聚合中转

- **GIVEN** worker A 与 worker B 并行执行
- **WHEN** A 先完成
- **THEN** A 的结果不直接流向 B;B 的输入在派发时已定,聚合发生在 orchestrator

### Requirement: 聚合与分歧裁决

orchestrator SHALL 聚合各 worker 结构化结果产出步骤结论:结论 MUST 附各 worker 证据链引用。worker 结论冲突时(数值/口径/定性分歧),orchestrator SHALL 裁决:重派验证子任务或如实标注分歧呈现——SHALL NOT 静默丢弃任一方、SHALL NOT 编造调和值。critic 启用时 SHALL 对聚合结论审查(一致性/证据充分性),审查不通过的结论 SHALL 触发修订循环(有限轮次,轮次上限后如实输出保留意见)。

#### Scenario: 分歧如实标注

- **GIVEN** worker A 与 B 对同一指标给出不同数值
- **WHEN** 聚合
- **THEN** 结论标注分歧与两方证据链,或派发验证子任务后以验证结果裁决,不静默取均值

#### Scenario: critic 修订有限轮

- **GIVEN** critic 审查聚合结论不通过
- **WHEN** 触发修订
- **THEN** 修订循环至多 N 轮(可配),超限后输出结论 + 保留意见,不无限循环

### Requirement: 授权不提权与审计

协作内一切 worker SHALL 以任务归属主体的授权集与数据范围执行——worker 数量增多 MUST NOT 扩大权限面;每个 worker 的工具调用走既有回调与六层防御,权限点/租户/数据范围逐层照常生效。协作事件(拓扑创建/派发/worker 完成/裁决/终态)SHALL 落审计与任务事件流;每 worker 的 LLM 用量独立采集并可按 worker 维度归集(038 口径)。

#### Scenario: worker 授权面不扩大

- **GIVEN** 任务归属主体未被授予某工具权限点
- **WHEN** 任一 worker 尝试调用该工具
- **THEN** 既有防御层照常拒绝并落审计,协作不提供旁路

#### Scenario: 用量按 worker 归集

- **GIVEN** 一次协作 3 worker 共消耗 9 次 LLM 调用
- **WHEN** 查询该任务用量
- **THEN** 各 worker 的 token 与失败次数可分别归集

### Requirement: 失控防护

协作 SHALL 受三重上限约束,任一触顶即结构化终止:(1) 嵌套深度——协作内派生的子协作层数不超过配置上限(缺省 1,即不嵌套);(2) 并发——同时运行的 worker 数不超过配置上限;(3) 预算——worker 级预算从任务步骤预算继承,不得凭空放大。触顶终止 SHALL 携带已完成 worker 的部分结果与未完成清单(对齐 ReAct 触顶语义与预算退出诚实),SHALL NOT 谎称完成。

#### Scenario: 并发上限生效

- **GIVEN** 并发上限 3,orchestrator 请求派发第 4 个并行 worker
- **WHEN** 派发请求
- **THEN** 拒绝派发或排队等待(实现择一,行为一致),不超限运行

#### Scenario: 预算继承不放大

- **GIVEN** 任务步骤剩余 token 预算不足以支撑再派一个 worker
- **WHEN** orchestrator 计划派发
- **THEN** 不派发,以已有结果部分完成收口,披露未完成子任务

### Requirement: 协作观测面

系统 SHALL 提供协作运行记录查询(任务维度):拓扑(orchestrator/worker/critic 及其绑定)、每 worker 的子目标/状态/结论摘要/证据链入口、裁决事件(含分歧标注)。每 worker SHALL 有独立 trace 且与任务 trace_root 父子关联,协作全程可在既有 trace 时间线查看。

#### Scenario: 协作总览可查

- **GIVEN** 一次 3 worker + critic 的协作已完成
- **WHEN** 查看任务详情的协作视图
- **THEN** 各 worker 子目标/结论摘要/证据入口与裁决事件完整可查,trace 父子关联可跳转
