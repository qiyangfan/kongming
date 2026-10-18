# Design: add-graph-checkpoint(图级检查点与图内挂起)

## Context

- 恢复现状:任务恢复粒度=步骤。api 持任务状态机与步骤账本
  (`apps/api/kongming/task/`),恢复=重派 `/internal/task-run`;agent 侧
  `task_gen.run_task_orchestration` 重入编排,已完成步骤经上下文摘要携带,
  **中断步骤从零重跑**。两图(`graph/build.py` 问数图、`graph/react/build.py`
  受限工具循环、协作装配)均以无检查点形态编译。
- 人工确认现状:计划期步骤声明 `require_confirm` → 整步挂起(api 审批门);
  工具层已有 `confirm_required + confirm_id` 协议,但任务通道内收到后只
  「引导终止循环、留待确认」,图状态丢弃。
- 信任边界(ADR-002):agent 零凭证、禁 DB/缓存驱动(import-linter);
  图框架原生的 Postgres 检查点保存器**不可用**,存储必须走内部受控通道。
- 动机与范围见 proposal.md(Why);alignment 依据见
  `docs/ai-platform-alignment.md` §4/§6。

## Goals / Non-Goals

Goals:步骤内中断可从最近一致点续跑;任务通道内高危动作可挂图审批后精确恢复;
快照/确认状态持久于控制面,agent 重启无损。Non-goals:见 proposal.md
(不换运行时、问数交互图不接入、task-kernel 审批门语义不动)。

## Decisions

### D1 存储通道:检查点保存适配器 + `/internal/*` 受控端点(而非任何本地/直连存储)

agent 侧新增检查点存储适配器(实现图框架的检查点保存协议:put / get /
put_writes / delete_thread),存储后端=apiclient 回调 api 新端点:

- `POST /internal/graph-checkpoints`(put:快照或 pending writes)
- `GET  /internal/graph-checkpoints`(get:按 tenant+thread 取最新或指定)
- `DELETE /internal/graph-checkpoints`(delete_thread:清理)

api 只存不透明 blob + 元数据,**不理解图框架序列化格式**——控制面与图框架解耦,
未来换运行时只换 agent 适配器(对齐 alignment §5「换向也不背存储债」)。
备选「AsyncPostgresSaver 直连」违背 ADR-002,否决;备选「Redis」同违且引入
第二存储语义,否决。

### D2 Schema:两张新表,对齐既有 `sys_agent_*` 前缀(迁移 0056)

- `sys_agent_graph_checkpoint`:`id`(UUID v7)、`tenant_id`、`thread_id`
  (text)、`task_id`/`step_id`(可空,归集用)、`kind`(VARCHAR+CHECK:
  checkpoint/writes)、`payload`(bytea)、`metadata`(jsonb)、`created_at`
  (timestamptz);索引 `(tenant_id, thread_id, created_at DESC)`,
  清理随任务终态钩子。
- `sys_agent_graph_confirm`:`id`(UUID v7)、`task_id`/`step_id`、`confirm_id`
  (工具层既有标识)、`thread_id`、动作摘要(目标/预期副作用,给确认卡)、
  `status`(VARCHAR+CHECK:pending/approved/denied/expired,CAS 迁移)、
  `expires_at`、`decided_by`/`decided_at`、`created_at`。

ADR-004 约定全项遵守(UUID v7 主键、VARCHAR+CHECK、timestamptz、无 ENUM)。

### D3 线程标识:任务步骤内确定性派生,api 不感知

`thread_id = "task:{task_id}:step:{step_id}"`(协作 worker 同构:
`collab:{run_id}:worker:{worker_id}`)。恢复时 api 无需携带任何图状态——
agent 收到步骤派发后先按派生线程查最新快照:有则续跑,无则新起。
备选「api 显式传 thread」把图语义泄进控制面,否决。

### D4 图内确认点:任务通道启用 `confirm_required → 挂图`,交互通道不动

任务/协作通道下,工具分派收到 `confirm_required` 时调图内挂起原语
(确认值={confirm_id, 动作摘要}),图挂起、task-run 同步返回
`{status:"suspended", confirm_id, ...}`;api 侧建 `sys_agent_graph_confirm`
行、任务 RUNNING→AWAITING_APPROVAL(既有白名单边,不扩状态机)、落任务事件。
归属人经既有任务审批面处置(新确认类型,复用确认卡形态):

- 批准 → 确认行 CAS pending→approved → resume 派发携带
  `{confirm_id, decision}` → agent 从最新快照恢复图,处置结果喂回挂起点:
  批准则携 confirm_id 重发该工具调用(工具层 confirm 协议幂等消化),
  拒绝则构造跳过观察喂回。
- 拒绝 → CAS pending→denied,同上恢复路径喂拒绝观察,按声明收口。
- 过期 → 复用任务审批过期扫描节奏,CAS pending→expired,喂过期观察。
- 幂等:CAS 单向迁移,重复提交同态返回(与 approve_step 同纪律)。

交互问数通道的 `confirm_required` 处置(引导终止循环)逐字保留。

### D5 恢复派发:复用既有 resume 链路,不新增调度器

图内确认批准后的恢复=既有任务 resume/审批后重派链路的自然延伸(api 已有
「批准→回队→调度重下」);快照续跑 vs 整步回退的分支在 agent 适配器内,
api 只透传恢复语义标记(事件用)。暂停-在途窗口沿用
「pause 不杀在途循环 + resume 立即派发」既有裁决,快照使其重做成本趋零,
不新增机制。

### D6 限额与清理:复用部署级上限口径,终态钩子清理

快照量随(任务数×步骤×循环轮数)增长,量级可控;部署级上限进
`docs/tech-stack.md`「任务/协作部署级上限」节(缺省值与行为一致原则);
任务终态钩子删除该任务全部快照(过程审计由任务事件时间线承载,快照行不残留)。
备选「TTL 后台扫描」引入新调度面,否决。

### D7 观测:恢复方式落任务事件与步骤详情

挂图/处置/恢复(来源=快照续跑|整步回退)落任务事件(既有事件面);
步骤详情标注恢复方式。SSE 流问数不变。

## Risks / Trade-offs

- [图框架检查点协议演进(序列化格式变更)] → blob 为不透明字节+版本元数据,
  格式漂移只影响旧快照续跑(回退整步重跑),不阻塞恢复主路径。
- [挂图后 task-run 同步返回,在途循环的另一半(并发 worker)协作场景] →
  圆桌/分工 worker 均为步骤内串行派发,挂图仅发生在当前派发线程;跨线程
  挂起不做(Non-goal:协作挂起编排留待真实场景)。
- [快照写失败降级导致「以为能续跑实际整步重来」] → 降级事件落任务时间线,
  恢复方式如实标注(spec 观测要求),不谎称。
- [内部通道新端点扩大攻击面] → 复用内部令牌中间件+租户绑定复校+审计,
  与 `/internal/sql/execute` 同级防御;跨租户 404 同形测试先行。

## Migration Plan

1. 迁移 0056 建两表(幂等,先于代码部署);2. api 部署(internal 端点+
   确认处置);3. agent 部署(适配器+图装配);4. web 跟进(确认卡/恢复方式
   展示)。回滚:agent 关闭挂图与快照写入(行为回退现状,快照表闲置无害);
   无数据迁移反向问题。

## Open Questions

(无——开工前无需再裁决的事项;工具层 confirm_id 幂等消化细节在实现期
以测试钉死。)
