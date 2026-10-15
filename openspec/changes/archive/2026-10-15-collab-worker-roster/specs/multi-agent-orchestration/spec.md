# Spec Delta

## ADDED Requirements

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
