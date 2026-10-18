# Tasks: add-graph-checkpoint

## 1. Schema 与内部受控端点(api 侧)

- [x] 1.1 迁移 0056:建 `sys_agent_graph_checkpoint` 与 `sys_agent_graph_confirm`
  (UUID v7 主键、VARCHAR+CHECK 状态、timestamptz、索引
  `(tenant_id, thread_id, created_at DESC)`,ADR-004 约定;向上兼容空库与既有库)。
  验证:`uv run alembic upgrade head` 干净库/既有库双向通过,`alembic downgrade -1` 可逆。
- [x] 1.2 internal 域新增检查点端点(put/get/delete_thread):内部令牌中间件 +
  租户解析绑定复校 + 审计落点,与 `/internal/sql/execute` 同级防御;**测试先行**:
  先写失败测试(跨租户 404 同形、未授权 403、get 最新/指定语义、delete 清理)。
  验证:pytest internal 检查点用例由红转绿;新端点出现在 openapi。
- [x] 1.3 task 域扩展图内确认处置:确认行创建(挂图回调进入)、CAS 状态机
  (pending→approved/denied/expired 单向)、归属人校验、过期扫描接入既有节奏、
  批准后 resume 派发携带确认决策;**测试先行**:先写失败测试(他人处置拒绝落审计、
  重复提交同态、过期迁移、AWAITING_APPROVAL 迁移走既有白名单)。
  验证:pytest task 确认用例由红转绿;既有任务状态机/审批门测试零回归。
- [x] 1.4 `make contracts` 重生成并核对 diff 仅增量(新端点/模型)。
  验证:contracts/openapi.json 与 web 生成类型同步,CI 漂移门禁绿。

## 2. 检查点适配器与图装配(agent 侧)

- [x] 2.1 检查点存储适配器:实现图框架检查点保存协议(put/get/put_writes/
  delete_thread),存储后端=apiclient 新回调方法;写失败降级(返回缺省、
  不抛断主路径)并发射降级事件;**测试先行**:先写适配器单测(fake apiclient:
  put/get 往返、写失败降级、线程隔离)。
  验证:pytest 适配器用例绿;import-linter 检查通过(agent 无 DB/缓存驱动)。
- [x] 2.2 图装配接入:`get_react_graph`/协作装配以适配器为检查点存储编译,
  线程标识按 D3 确定性派生(task/collab 两式);步骤派发入口先查快照——
  有则恢复续跑、无则新起,恢复方式(快照续跑/整步回退)随结果返回;
  **测试先行为图拓扑**:恢复路径用快照注入测试(中断后恢复不重做已完成轮次、
  无快照回退整步)。
  验证:pytest test_graph_topology 扩展用例绿;既有 8 节点图与交互通道行为
  零变化(既有测试全绿)。
- [x] 2.3 图内确认点原语:任务/协作通道下工具分派收到 `confirm_required` 时
  挂图(挂起值=confirm_id+动作摘要),task-run 返回 suspended 载荷;交互通道
  处置逐字保留;恢复入口消化确认决策(批准→携 confirm_id 重发调用,拒绝/
  过期→构造对应观察喂回);**测试先行**:先写挂图/恢复双路径失败测试。
  验证:pytest 图内挂起用例由红转绿;交互通道 confirm_required 既有测试零回归。
- [x] 2.4 task_gen 编排接线:suspended 载荷透传 api(建确认行、任务
  AWAITING_APPROVAL);恢复派发分支(带决策 resume);任务事件发射
  (挂图/处置/恢复方式,落既有事件面)。
  验证:合同覆盖拆分为两侧边界(api test_task_graph_checkpoint 挂起→审批→
  恢复提示 HTTP 全链;agent test_graph_checkpoint 挂起→Command(resume)→兑现→
  DONE 载荷形),载荷形两侧断言互锁(实施注记:单进程全栈 e2e 留归档前冒烟)。

## 3. web 与文档收尾

- [x] 3.1 web 任务详情:图内确认卡(复用既有确认卡组件形态,新确认类型)、
  步骤恢复方式标注(快照续跑/整步回退/降级);类型来自生成产物,禁手写。
  验证:`pnpm typecheck && pnpm lint` 绿;亮暗两态走查任务详情页;宪法
  Article 8 自检 grep 通过。
- [x] 3.2 docs/tech-stack.md「任务/协作部署级上限」节补快照部署级上限缺省值;
  docs/HANDOFF.md 补图级检查点现态与配方(按接手文档纪律,只写下一棒需要的)。
  验证:文档自查(删句测试);HANDOFF 无过程叙事。
- [x] 3.3 全量质量门禁:`make check`(api/agent ruff+mypy+pytest,web
  tsc+test+eslint)全绿;挂起→批准→快照续跑→DONE 全链由双侧测试覆盖
  (api HTTP 全链 + agent 图级端到端)。
  验证:api-check/agent-check/web-check 全绿;contracts-check 报 openapi 产物
  相对上次提交有 diff=预期形态(产物已与代码同步,提交等用户 git 指令)。

## Workflow follow-up

- 归档前:spec deltas sync 进 `openspec/specs/graph-checkpoint/`;roadmap
  第四梯队登记行更新(图级检查点落地);alignment 文档 §6 行动表勾注落地形态。
- git 提交等用户明确指令(铁律,不在本 change 任务内)。
