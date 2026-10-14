# 设计:对等圆桌(分析圆桌拓扑)

## Context

- 091 现态:`collab_gen` 编排核心 = 「规划→派发→聚合→裁决」循环(零 api 句柄);worker = 一次受限 ReAct 调用,输入派发时冻结,输出只进聚合——worker 相互不可见;`collab_wiring` 组合根开账 fail-closed、工具求交、父子 trace、用量归集;api 侧 `collab_run/collab_worker` 账本 + 拓扑归一钳制 + 任务事件流;judge 层有 `CollabRubric v1`。黑板不存在——091 Non-goal 明确排除,本变更为显式修订(opt-in 形态)。
- 外部证据(2026-10 调研):Anthropic 生产多智能体采用主从编排并记录同步瓶颈(worker 间不能协调是已知代价);MAD 研究:固定轮次 + 共识判定 + 裁判是主流终止策略,选择性参与可大幅降本,失败模式 = 附和式假共识与错误收敛;黑板模式以共享结构化转写消灭传话式信息损耗。
- 硬约束:不引入消息总线(宪法 I);agent 面零凭证、工具调用 100% 回调(宪法 IV / ADR-002);新表走 Alembic、UUID v7、VARCHAR+CHECK(宪法 VI / ADR-004);契约生成(ADR-005)。

## Goals / Non-Goals

Goals:对等可见性(全员共享转写)+ 显式治理协议(终止/预算/授权),以 091 既有治理资产承载,新增代码面收敛在编排核心与转写账本。

Non-Goals:见 proposal Non-goals(分布式/自由寻址/沙箱/实时 SSE/嵌套/动态名册)。

## Decisions

### D1:圆桌 = collab 域第二拓扑形态,不建新域

`collab_topology` 增加 `kind`(缺省 `decomposition`,存量数据语义零变化;`roundtable` 为 opt-in)。宿主仍是 090 步骤,账本/授权/trace/预算/事件全复用。否决「独立 roundtable 域」:治理语义同源,分域只会复制失控防护与账本逻辑;否决「通用 swarm 引擎」:当前需求是分析对辩一种形态,引擎抽象无第二个实例验证(范围/规模纪律)。

### D2:黑板 = 追加式轮次转写,不是自由消息总线

轮次制 + 结构化发言(立场/论据/证据引用);参与者每轮输入 = 黑板受控快照(体积截断,沿用 091 摘要纪律)。通信仍单点收口——**黑板即审计单元**,转写本身落 DB 账本。否决 MsgHub 式广播与点对点寻址:审计与预算无法单点收口,且 N≤5 规模下广播的吞吐收益为零;「对等」体现在全员可见与轮流主笔,不体现在自由建联。

### D3:参与者 = 既有 ReAct 内核实例(与 091 worker 同物)

人格注入两来源:Bot 绑定(既有 `bot_system_prompt` 口径)或内置角色章程(analyst/skeptic/auditor 的 prompt 资产,撰写纪律同既有 prompt 资产)。工具集 = 任务授权 ∩ Bot 名单,空集 fail-closed(与 bot 域「零工具不可运行」同口径);查证动作走既有只读回调工具,零新执行通道。参与者行复用 `collab_worker` 账(参与者 = 生命周期贯穿全部轮次的 worker 行),无新参与者模型。

### D4:终止协议的实现位置与预算式

三通道全在编排核心循环内判定,控制面兜底:(1) 共识——主席为一次独立 LLM 结构化判定(收敛 + 理由),**计 token 不占执行预算池**(与 091 orchestrator 拆解/仲裁同位);(2) 轮次——`KM_AGENT_COLLAB_MAX_ROUNDS`(执行面兜底)与 api 侧归一钳制双口径,缺省 3;(3) 预算——发言与查证均消耗一次参与者执行,预算式 = 参与者数 × 轮次上限(由声明推导、部署钳制收口;圆桌无核实/修订重派,无需 091 的 2n+1 裕量式)。任一触顶 → 沿用 091 部分完成诚实收口,不新建终止语义。

### D5:选择性参与(弃权)为一级语义

弃权 = 结构化产出(空发言 + 原因),落转写;连续一轮全员弃权即提前收口(无可新增论据的收敛信号),收口不改标共识。每轮不做强制互评——主席判定每轮仅一次,发言轮内查证按需,直接落实 MAD 选择性参与降本结论。

### D6:存储——新表 `sys_agent_collab_turn`

`id`(UUID v7)、`run_id`(FK collab_run)、`round_no`、`worker_no`(参与者)、`kind`(speech/verification/abstain/verdict,VARCHAR+CHECK)、`content`(JSONB 结构化发言/判定)、`evidence`(JSONB 证据引用)、`usage`(JSONB)、`trace_id`、时间列;索引 `(run_id, round_no)`。轮次转写不可改写(无 UPDATE 路径)。`collab_topology` JSONB 已有列(0041)承载 kind 与参数,无新列;`collab-runs` 响应扩展 `turns`。ADR-004/VI:一次建全已规划列。

### D7:观测与评测

任务事件流新增圆桌生命周期事件(沿用 `collab_*` 事件族命名);web 任务详情协作视图扩展圆桌形态(轮次转写时间线,标准件 + DataView 双形态);judge 新增 `RoundtableRubric v1`(维度:共识质量/分歧诚实/证据接地/收敛效率),对象取数复用 `fetch_collab_objects` 扩展 turns;权重在实现期随基线登记确定(劣化 ≥5pp 门禁沿用)。

## Risks / Trade-offs

- **token 放大(参与者 × 轮次)** → 参与者复用协作 worker 部署上限、轮次硬顶、预算池、弃权降本四道闸;用量按参与者归集,090 步级预算闸兜底。
- **附和式假共识(MAD 已知失败模式)** → 结构化发言强制证据引用;主席判定附理由且落转写;分歧保留为一级产出(未共识是合法终态);剧本化回归含「诱导附和」用例;judge 把「分歧诚实」设为一等维度。
- **黑板快照体积膨胀** → 快照受控截断 + 摘要承载,纪律同 091 上下文下传。
- **主席误判(早收敛/漏收敛)** → 轮次硬顶与预算池为确定性兜底;判定理由可审计;judge 收敛效率维度提供事后信号。
- **存量兼容** → kind 缺省 decomposition,既有运行/查询/契约行为零变化;新表独立,回滚 = downgrade。

## Migration Plan

Alembic up/down 回环验证;部署配置新增轮次上限(api/agent 两侧,缺省 3)并更新 `docs/tech-stack.md`「任务/协作部署级上限」节;`make contracts` 重生成并核对零漂移;无存量数据迁移(kind 缺省值即旧语义)。

## Open Questions

无阻塞项。rubric 权重与内置角色章程文案在实现期以评测基线校准,不影响 spec 与任务拆分。
