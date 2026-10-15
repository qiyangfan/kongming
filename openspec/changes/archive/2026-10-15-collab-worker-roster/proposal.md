# Proposal

## Why

协作域两形态的 Bot 绑定能力不对称:圆桌(115)参与者可逐个点名绑定 Bot,分工(091)形态只有顶层单一 `bot_key` 一刀切注入全部 worker,worker 本身由编排器运行时匿名合成。结果是「Bot 作为可复用专家」在分工形态下消费链断裂——预定义的领域专家(人格 + 工具白名单)无法作为命名 worker 参与分工派发,声明者只能靠编排器拆解运气。补齐后 Bot 的消费形态闭环:对话嵌入 / OpenAI 兼容 API / 分工 worker / 圆桌参与者,四种形态一个实体。

## What Changes

- 分工协作拓扑声明新增可选 `workers` 名册(条目 = Bot 点名 + 可选别名):声明名册后,worker 不再匿名合成,编排器把子目标**分配**给名册成员执行(纯名册语义,不与合成 worker 混用)。
- 逐 worker 的人格注入与工具求交:名册成员按各自 Bot 定义注入系统提示,工具集 = 任务授权工具 ∩ 该成员 Bot 工具名单(单绑定口径的 per-worker 化,复用圆桌既有机制)。
- 声明期校验与歧义闸:逐条目 Bot 存在性校验(同圆桌绑定口径);声明名册后顶层 `bot_key` 拒绝(与圆桌同构的歧义闸)。
- 运行时降级诚实:名册成员 Bot 解析失败(已删/停用)/零工具 fail-closed 剔除并如实披露(剔除必披露口径);子目标无法映射到名册成员时进未派发清单,不静默改派合成 worker。
- 无名册 = 现行为一寸不变(opt-in;顶层单绑定照旧)。
- 契约面:`CollabTopologyIn` 增字段 → `make contracts` 重生成前端类型(ADR-005);协作账本逐 worker `bot_key` 落账通道已存在,观测面无需结构变更。

## Non-goals

- **不做拓扑声明 UI**:两形态的声明面现状均走 API 无 UI,这是存量现状,非本变更引入的缺口,不扩范围。
- **不做名册模板/团队实体持久化**:常驻班底等复用形态待真实复用信号,不预立项。
- **不做嵌套协作提额**:深度上限缺省 1 不变,名册成员不是子编排器。
- **不改动圆桌形态**:名册/章程/轮次机制原样,仅做对称参照。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `multi-agent-orchestration`:「协作拓扑」需求扩展——分工形态拓扑声明新增可选 worker 名册(Bot 点名、声明期锁定、纯名册派发),派发与授权需求随动(逐 worker 人格/工具注入、映射失败诚实披露);其余需求(聚合裁决、失控防护、观测面)语义不变。

## Impact

- **apps/api**:`task/models.py` `normalize_collab_topology` 分工分支增名册归一(条目校验/去重/部署上限钳制);`task/service.py` `set_step_topology` 增逐条目 Bot 存在性校验与顶层 `bot_key` 歧义闸,`_collab_decision` 分工分支增逐条目 Bot 运行时解析;`task/router.py` `CollabTopologyIn` 增字段。信任边界零扩大(ADR-002):worker 授权仍由任务主体授权集钳制,求交是收窄不是放大。
- **apps/agent**:`collab_gen.py` 编排核心增名册感知(拆解 prompt 改为分配制、解析增成员映射、映射失败进未派发清单);`collab_wiring.py` 分工分支 worker 装配改 per-worker(人格/工具求交/账本 bot_key),分歧核实与 critic 修订重派为编排器治理动作(无 Bot 绑定的受限 ReAct,账本既有 `verification` 标记区分)。
- **apps/web**:api-types 再生成;协作运行视图透出名册绑定信息(数据已落账,展示层增量)。
- **schema**:无迁移(拓扑为既有 JSONB 列内归一载荷,ADR-004 免迁移路径)。
- **contracts**:`make contracts` 重生成并同步提交(ADR-005)。
