## Purpose

在任务内核之上提供**运行时动态多智能体协作**:orchestrator 把目标拆解为子任务派发给多个 worker(既有 ReAct 内核实例,可绑定 Bot 人格与工具集),可选 critic 审查,聚合裁决产出结构化结论。通信收口于 orchestrator、授权不提权、深度/并发/预算三重失控防护——治理模式与单内核一致,无新执行通道(ADR-002 零改动)。

## Requirements

### Requirement: 协作拓扑

协作 SHALL 支持两种拓扑形态,由拓扑声明指定:**分工(decomposition)**与**圆桌(roundtable)**。分工形态 SHALL 由 orchestrator(planner + 聚合者)、一至多个 worker、可选 critic 构成。圆桌形态 SHALL 由主席(收敛判定者)与二至部署上限个参与者构成,SHALL 声明拓扑参数:参与者集合(可绑定 custom-agent 的 Bot 定义)、轮次上限;主席 MUST NOT 产出立场发言,只做收敛判定与收口。两形态中,worker 与参与者 SHALL 均为既有 ReAct 内核实例:可绑定 Bot 定义(系统提示与工具集),绑定校验沿用 Bot 运行时权限继承口径。分工形态 SHALL 声明拓扑参数:worker 数量上限、是否启用 critic、子任务分配策略(并行/串行)。协作 MUST 以任务(task-kernel)步骤为宿主发起——不提供脱离任务的游离协作入口;步骤声明协作拓扑后,该步的执行即协作,步骤终态由聚合结论驱动。

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

#### Scenario: 任务步骤发起圆桌

- **GIVEN** 任务步骤「评估该指标异常的三种可能成因」声明圆桌拓扑(3 参与者,轮次上限 3)
- **WHEN** 该步执行
- **THEN** 参与者按轮对辩,主席判定收敛或触顶收口,结论与分歧记录回写步骤

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

协作 SHALL 受三重上限约束,任一触顶即结构化终止:(1) 嵌套深度——协作内派生的子协作层数不超过配置上限(缺省 1,即不嵌套);(2) 并发——同时运行的 worker 数不超过配置上限;(3) 预算——worker 执行次数预算自拓扑声明与部署上限推导(初派、分歧核实、critic 修订重派计入同一预算池),SHALL NOT 超出部署上限放大;worker token 用量 SHALL 按 worker 归集并计入任务步骤预算,由任务步级预算闸在步骤边界兜底。触顶终止 SHALL 携带已完成 worker 的部分结果与未完成清单(对齐 ReAct 触顶语义与预算退出诚实),SHALL NOT 谎称完成。

#### Scenario: 并发上限生效

- **GIVEN** 并发上限 3,orchestrator 请求派发第 4 个并行 worker
- **WHEN** 派发请求
- **THEN** 拒绝派发或排队等待(实现择一,行为一致),不超限运行

#### Scenario: 执行预算饥饿如实披露

- **GIVEN** worker 执行次数预算已被初派与核实/修订重派耗尽
- **WHEN** orchestrator 需再派一个 worker(分歧核实或 critic 重派)
- **THEN** 不派发并如实标注预算不足,已派发结论照常聚合收口,SHALL NOT 虚报完成

#### Scenario: worker 用量计入任务预算

- **GIVEN** 协作步骤执行中各 worker 消耗 LLM token
- **WHEN** 步骤收口回写
- **THEN** worker 用量计入该步骤 token 增量,任务预算闸可据此在后续步骤触发暂停

### Requirement: 协作观测面

系统 SHALL 提供协作运行记录查询(任务维度):拓扑(orchestrator/worker/critic 及其绑定)、每 worker 的子目标/状态/结论摘要/证据链入口、裁决事件(含分歧标注)。每 worker SHALL 有独立 trace 且与任务 trace_root 父子关联,协作全程可在既有 trace 时间线查看。

#### Scenario: 协作总览可查

- **GIVEN** 一次 3 worker + critic 的协作已完成
- **WHEN** 查看任务详情的协作视图
- **THEN** 各 worker 子目标/结论摘要/证据入口与裁决事件完整可查,trace 父子关联可跳转

### Requirement: 圆桌轮次与黑板

圆桌 SHALL 以轮次制推进:每轮参与者按既定顺序依次获得发言机会,产出 SHALL 为结构化发言(立场 + 论据 + 证据引用),并可附查证动作(经既有回调工具执行,调用与结果作为证据引用落转写)。黑板 SHALL 为 orchestrator 管理的追加式结构化转写:发言、查证、弃权与主席判定按轮落账、不可改写。参与者名册 SHALL 在拓扑声明时确定,运行中 MUST NOT 增减参与者。

#### Scenario: 轮次发言全员可见

- **GIVEN** 圆桌进行至第 2 轮,参与者 B 发言
- **WHEN** 第 3 轮开始
- **THEN** 其余参与者的输入快照包含 B 的发言,按轮可追溯

#### Scenario: 查证动作落转写

- **GIVEN** 参与者在发言中附查证动作
- **WHEN** 只读工具经既有回调执行
- **THEN** 既有防御层与审计照常生效,发言终态的证据引用(查证 SQL 等)落黑板,调用明细在既有工具审计

#### Scenario: 名册运行期锁定

- **GIVEN** 3 参与者圆桌运行中
- **WHEN** 任何方请求追加第 4 名参与者
- **THEN** 拒绝,名册不变

### Requirement: 选择性参与

参与者于任一轮 SHALL 可弃权(不产出新论据,弃权事实落转写);连续一轮全体参与者弃权 SHALL 触发提前收口。提前收口 SHALL NOT 将未共识状态改标为共识。

#### Scenario: 连续全员弃权提前收口

- **GIVEN** 某轮全体参与者均弃权
- **WHEN** 收口判定
- **THEN** 圆桌提前终止,以最后实质发言轮的结论收口,未共识状态如实标注

### Requirement: 圆桌终止协议

圆桌 SHALL 受三通道终止约束,任一触发即结构化收口:(1) 共识——主席每轮后判定是否收敛,判定 SHALL 附理由;(2) 轮次——轮数达部署上限即止;(3) 预算——参与者发言与查证计入协作执行预算池,耗尽即止。收口 SHALL 诚实:共识结论 SHALL 可追溯到全体参与者最后实质发言;未共识收口 SHALL 完整保留分歧各方立场与证据,SHALL NOT 编造调和结论;收口状态映射沿用协作运行账既有语义,未竟事项如实披露。

#### Scenario: 共识收敛

- **GIVEN** 第 N 轮后主席判定收敛并附理由
- **WHEN** 圆桌收口
- **THEN** 终态为共识结论,判定理由与各参与者立场在转写中可追溯

#### Scenario: 轮次触顶保留分歧

- **GIVEN** 轮数达部署上限仍未收敛
- **WHEN** 圆桌收口
- **THEN** 输出当前结论与各方分歧立场、证据,不编造调和,未共识如实标注

#### Scenario: 预算耗尽诚实收口

- **GIVEN** 执行预算池在圆桌中途耗尽
- **WHEN** 收口判定
- **THEN** 停止开启新轮,已完成发言与结论照常落账收口,未竟状态如实披露

### Requirement: 圆桌转写观测

系统 SHALL 提供圆桌转写查询(挂既有协作运行记录查询面):每轮每参与者的结构化发言、查证证据引用、弃权与主席判定完整可查、按序可回放。圆桌生命周期事件(开桌、每轮完成、判定、收口)SHALL 落任务事件流;每参与者独立 trace 与用量归集沿用协作既有口径。

#### Scenario: 转写回放可查

- **GIVEN** 一次 3 参与者 4 轮圆桌已收口
- **WHEN** 查询该协作运行记录
- **THEN** 全部发言、查证、弃权、判定按轮次与参与者完整可查

#### Scenario: 生命周期事件落流

- **GIVEN** 圆桌运行中
- **WHEN** 每轮完成或主席判定
- **THEN** 对应事件落任务事件流,消费方可据以刷新视图

### Requirement: 分工 worker 名册声明

分工形态拓扑声明 SHALL 可选携带 worker 名册:每条目为一位命名 worker(Bot 点名 + 可选别名,别名为派发寻址与账本标识,缺省取 bot_key)。名册 SHALL 在拓扑声明时确定,步骤运行中 MUST NOT 增减或改绑成员。名册条目 MUST 通过 Bot 存在性校验(与圆桌参与者绑定同口径),不通过则拒绝声明。声明名册的拓扑 MUST NOT 同时携带顶层单绑定,歧义声明 SHALL 拒绝。名册规模 SHALL 受部署级 worker 上限钳制(归一截断,执行面超限剔除必披露)。未声明名册的分工拓扑 SHALL 保持既有行为:worker 由编排器运行时合成,顶层单绑定照旧生效。

#### Scenario: 名册声明与运行期锁定

- **GIVEN** PENDING 步骤声明分工拓扑,名册 3 成员(各点名一个 Bot,别名可省)
- **WHEN** 声明受理且步骤执行
- **THEN** 逐条目 Bot 存在性校验通过后归一落库,运行中名册不变(增减/改绑均不受理)

#### Scenario: 名册成员 Bot 不存在拒绝声明

- **GIVEN** 名册某条目点名的 bot_key 在租户内不存在
- **WHEN** 声明到达
- **THEN** 拒绝该声明(422 INVALID_BOT,提示具体条目,圆桌同款映射),不落库,落审计

#### Scenario: 名册与顶层单绑定互斥

- **GIVEN** 分工拓扑声明同时携带名册与顶层 bot_key
- **WHEN** 声明到达
- **THEN** 拒绝(歧义声明;单绑定与名册不共存),提示用名册逐成员绑定

#### Scenario: 无名册现行为不变

- **GIVEN** 分工拓扑仅声明顶层 bot_key(无名册)
- **WHEN** 步骤执行
- **THEN** worker 由编排器合成,全部按顶层 Bot 注入人格与工具(既有行为,零变化)

#### Scenario: 名册超部署上限截断披露

- **GIVEN** 名册声明 5 条而部署级 worker 上限为 3
- **WHEN** 声明归一与执行
- **THEN** 归一截断至 3 条(声明响应可见截断后名册),执行面超限/剔除条目如实披露,不静默蒸发

### Requirement: 名册派发与逐 worker 绑定

声明名册的分工协作 SHALL 以纯名册派发:编排器把子目标分配给名册成员执行,每个工作派发 MUST 落在名册成员上,MUST NOT 混入匿名合成 worker。名册成员执行 SHALL 按其 Bot 定义注入系统提示,工具集为任务授权工具与该成员 Bot 工具名单的交集(逐 worker 求交,不提权)。成员 Bot 运行时解析失败(已删/停用)或求交为空集时 SHALL fail-closed 剔除并如实披露;子目标无法映射到名册成员时 SHALL 计入未派发清单如实披露,SHALL NOT 静默改派合成 worker。分歧核实与 critic 修订重派为编排器治理动作:以无 Bot 绑定的受限 worker 执行,在账本如实标注,不计入名册成员。

#### Scenario: 命名专家分工执行

- **GIVEN** 步骤名册 = [「财务分析」Bot(财务工具),「运营指标」Bot(运营工具)]
- **WHEN** 编排器拆解子目标并分配派发
- **THEN** 各子目标由被分配成员执行:系统提示按其 Bot 注入,工具集逐 worker 求交,账本逐 worker 记 bot_key 与结论,可按成员归集

#### Scenario: 成员解析失败剔除披露

- **GIVEN** 名册某成员的 Bot 在步骤执行前被删除
- **WHEN** 协作装配名册
- **THEN** 该成员 fail-closed 剔除并在收口披露(绑定解析失败),其余成员照常派发;全体剔除则不开工、诚实收口

#### Scenario: 零工具成员剔除

- **GIVEN** 某成员 Bot 工具名单与任务授权工具求交为空集
- **WHEN** 协作装配名册
- **THEN** 该成员剔除并披露(零工具不可运行),不放大为不限制

#### Scenario: 子目标映射失败诚实披露

- **GIVEN** 编排器规划输出中某子目标的分配对象不在名册内(或未给出分配)
- **WHEN** 派发
- **THEN** 该子目标计入未派发清单如实披露,SHALL NOT 改派匿名合成 worker,SHALL NOT 静默丢弃

#### Scenario: 治理动作不占名册

- **GIVEN** 名册派发后两成员结论分歧
- **WHEN** 编排器触发核实重派
- **THEN** 核实 worker 为无 Bot 绑定的受限执行(任务授权工具全量),账本标注为核实条目,不新增名册成员
