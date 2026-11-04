# Spec Delta: multi-agent-orchestration

## MODIFIED Requirements

### Requirement: 派发与上下文受控传递

orchestrator 派发子任务 SHALL 携带:子目标、受控上下文摘要(任务目标 + 相关已完成结论)、可用能力提示;MUST NOT 把整份任务历史原样下传(上下文体积受控)。受控摘要发生截断时 SHALL 保留续读入口:附任务标识与只读续读工具提示,worker SHALL 可按需读取**自身任务**的目标全文与已完成步骤结论;未截断 SHALL NOT 附入口,worker 间授权边界与体积受控原则不变。worker 产出 SHALL 为结构化结果:结论 + 证据引用(工具调用/查询快照引用)+ 置信表述。分工形态下 worker 间 MUST NOT 直连——一个 worker 的输出 SHALL NOT 直接进入另一 worker 的输入,一律经 orchestrator 聚合后再决定是否作为后续子任务的上下文。圆桌形态下参与者 MUST NOT 建立点对点直连通道——参与者后续输入 SHALL 为 orchestrator 管理的黑板转写受控快照(体积有上限,截断时同样附续读入口),一切通信经黑板留痕。

#### Scenario: 上下文受控下传

- **GIVEN** 任务已有 6 步完成结论
- **WHEN** orchestrator 派发新子任务
- **THEN** 下传的是与子目标相关的摘要,非全量历史,体积有上限

#### Scenario: 截断附续读入口

- **GIVEN** 派发上下文摘要超出体积上限发生截断
- **WHEN** worker 接收派发
- **THEN** 摘要尾部附任务标识与续读工具提示,worker 可按需读取自身任务完整上下文

#### Scenario: 未截断不附入口

- **GIVEN** 派发上下文摘要未超体积上限
- **WHEN** worker 接收派发
- **THEN** 摘要原样下传,不含续读提示,行为与既有一致

#### Scenario: 续读只限自身任务

- **GIVEN** worker 持有他任务的标识
- **WHEN** worker 经续读工具请求他任务上下文
- **THEN** 请求被拒绝(404 同形),仅自身任务可读

#### Scenario: worker 输出经聚合中转

- **GIVEN** worker A 与 worker B 并行执行
- **WHEN** A 先完成
- **THEN** A 的结果不直接流向 B;B 的输入在派发时已定,聚合发生在 orchestrator

#### Scenario: 圆桌通信经黑板留痕

- **GIVEN** 圆桌参与者 A 第 2 轮发言
- **WHEN** 参与者 B 在第 3 轮获得输入
- **THEN** B 的输入是黑板转写的受控快照(含 A 的发言),无转写之外的私有通道
