# Design

## Context

分工协作(091)现状:拓扑声明只有顶层单一 `bot_key`,运行时 `collab_gen` 自由拆解子目标、匿名编号 worker;`collab_wiring` 把顶层 Bot 人格/工具闭包注入全部 worker。圆桌(115)已有逐参与者 Bot 解析(api `_collab_decision` 循环)+ 执行面名册装配(解析失败/零工具/超上限剔除必披露)+ 顶层 `bot_key` 歧义闸。本变更是把圆桌的「声明期点名绑定」机制对称移植到分工形态,复用既有装配式样与账本通道(逐 worker `bot_key` 落账已存在)。动机见 proposal.md。

## Goals / Non-Goals

- Goals:名册声明的归一/校验/歧义闸;纯名册派发(分配制规划 + 映射失败诚实披露);逐 worker 人格/工具求交;治理动作(核实/修订)与名册成员的边界清晰。
- Non-Goals(设计级,提案 Non-goals 之外):不改预算公式(`workers_cap*2+1` 照旧,名册只约束初派去向);不做名册成员间通信(经 orchestrator 聚合不变);不做成员亲和/负载均衡(分配由规划 LLM 定)。

## Decisions

### D1 名册条目形态:结构化条目,身份 = 别名缺省 Bot 名

条目 `{name?, bot_key}`:身份(去重键、派发寻址、账本标签)取 `name` 缺省 `bot_key`,同身份重复声明裁到首条(与圆桌按 role 去重同构)。
- 否决纯 `bot_key` 数组:账本与规划输出的成员标识需可读可区分,结构化条目留出展示/扩展位,且与圆桌 `participants` 条目形态一致。
- 否决 `role` 字段:分工 worker 无内置章程域(圆桌 role 绑定 ROLE_CHARTERS),名册成员的人格完全由 Bot 定义承载,再加 role 是冗余状态源。

### D2 超部署上限:归一钳制不拒绝(与圆桌同式,偏离早前讨论口径)

名册规模 > 部署 worker 上限时:归一截断(`workers[:cap]`)+ 声明响应回显截断后名册 + 执行面双钳剔除披露。
- 早前讨论曾倾向「声明期 fail-closed 拒绝」;定稿改为钳制,理由:与圆桌既有 API 行为对称(圆桌超限即归一截断,拒绝会造成两形态声明语义分叉);对声明者不静默(响应回显归一结果);执行面已有「剔除必披露」通道兜底。
- 失效边界:若声明端不看响应回显,可能带着 5 人名册预期跑 3 人——由执行面披露条目(`xxx(超执行面部署上限)`)收口,与圆桌同风险同对策。

### D3 纯名册派发:分配制规划,映射失败进未派发清单

`collab_gen` 增可选 roster 入参(仅成员标签清单,零 api 句柄不变)。有 roster 时拆解 prompt 从「拆 1~N 个子目标」改为「把子目标分配给给定成员清单」,输出条目 `{subgoal, worker}`;解析后:分配对象在名册内 → 派发;名册外/未分配 → 进 `pending_subgoals`(既有未派发清单通道),不派发、不改派、不静默丢弃。
- 允许一成员承接多个子目标(名册是人格池不是并发槽),初派总数仍受 `max_workers` 与预算钳制,同成员多次执行为账本多行(按 bot_key 可归集)。
- 否决「缺分配默认派首成员」:静默兜底违背诚实收口;否决「名册外条目按合成 worker 执行」:破坏纯名册(审计面混入未声明执行者)。

### D4 治理动作无绑定:核实/修订重派不占名册

分歧核实与 critic 修订重派为编排器治理动作(与圆桌主席判定同性质):无 Bot 人格注入、任务授权工具全量、账本既有 `verification=True` 标记区分,不是名册成员。
- 授权边界:bare worker 工具集 = 任务授权工具全量,是现状无绑定形态的既有上界,「worker 工具 ⊆ 任务授权」恒成立,无提权。
- 否决「核实走名册成员」:引入「谁做裁决」的偏袒问题(成员核实自己的分歧),且名册成员工具面被 Bot 名单收窄,核实能力反而不完整。

### D5 接线层 per-worker 装配:worker 执行器增槽位参数

`collab_wiring` 分工分支:api 决策载荷 `cfg.workers`(逐条 Bot 运行时解析结果)→ 有效名册装配(声明绑定而解析失败 fail-closed 剔除、Bot 工具求交空集剔除、超执行面部署上限剔除,三者均披露——式样复用圆桌装配段);`WorkerRunner` 签名扩为 `(subgoal, ctx, slot)`,`slot=None` 为现行为(顶层单绑定/裸 worker),`slot` 携带该成员的 persona/tools/bot_key。worker_no 编号照旧全局递增,`collab_worker_start` 的 bot_key 参数改传槽位值,subgoal 前缀携带成员标签提升账本可读性。
- 全员剔除 → 不开工,ERROR 诚实收口披露剔除清单(圆桌「有效参与者不足不开桌」同式)。

### D6 api 面:归一/校验/歧义闸/决策解析四处增量

- `normalize_collab_topology` 分工分支:条目非法(非 dict/缺 bot_key)跳过,名册空视为未声明(回退现行为;与圆桌「名册无效回退缺省」同哲学——归一恒合法,非法回退,响应回显可见);按身份去重;`max(1, max_workers_cap)` 钳制。
- `set_step_topology`:逐条目 Bot 存在性校验(圆桌循环同构);歧义闸判**原始载荷**(名册 + 顶层 bot_key → `TaskConflict`;圆桌归一后剥 bot_key 判归一值恒假的死闸教训,闸必须判 raw)。
- `_collab_decision` 分工分支:逐条目 resolve(解析失败返回 None 如实降级,不虚报人格注入——剔除在执行面)。
- 路由模型:`CollabTopologyIn` 增 `workers: list[CollabWorkerIn]`,`CollabWorkerIn = {name, bot_key}`(限长与圆桌条目同口径)。

### D7 契约与迁移:免迁移,类型再生成

拓扑为既有 JSONB 列内归一载荷,无 Alembic 迁移(ADR-004 免迁移路径)。`make contracts` 重生成 `api-types.gen.ts` 并同步提交(ADR-005)。web 运行视图数据已具备(逐 worker bot_key 落账),展示层增量(成员标签/别名),不新开声明 UI。

## Risks / Trade-offs

- [规划 LLM 分配质量不稳(名册外引用/漏分配)] → prompt 显式给出成员清单与输出 schema;未映射条目进未派发清单(预算不浪费、不静默);剧本测试覆盖畸形输出(名册外 worker/缺字段/非数组)。
- [同成员多子目标并发执行] → worker 为无状态受限 ReAct,同成员并发多次执行互不共享状态,无一致性风险;账本多行如实记录。
- [两进程部署上限漂移(api 钳 3 / agent 面配 2)] → 执行面双钳(圆桌同式):api 声明钳制为先,agent 面按自身 Settings 复钳,超限走剔除披露通道。
- [名册形态成本高于匿名合成(逐成员人格上下文)] → 名册是 opt-in;预算公式不变,token 计入步级预算闸照常兜底。
- [别名与 bot_key 混用作账本标签造成观测歧义] → 单点规则:有效身份 = name or bot_key,装配/落账/披露统一取该值(单一定义防漂移)。

## Migration Plan

无 schema 迁移、无环境变量。部署即生效(opt-in:不声明名册的存量拓扑行为零变化);回滚 = 代码回滚,无数据残留(名册只存在于拓扑 JSONB 载荷内,旧行为读不到即忽略)。
