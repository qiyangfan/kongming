# Tasks

## 1. api 领域层:名册归一(测试先行)

- [x] 1.1 在 `apps/api/tests/test_task_collab.py` 先写失败测试:分工拓扑 `workers` 名册归一——条目 `{name?, bot_key}` 合法归一(身份 = name 缺省 bot_key)、同身份去重裁首条、非法条目跳过、名册空视为未声明(回退现行为)、`max(1, max_workers_cap)` 钳制截断、圆桌分支不受影响
- [x] 1.2 实现 `apps/api/kongming/task/models.py` `normalize_collab_topology` 分工分支名册归一(纯函数,零框架依赖),跑 1.1 测试转绿

## 2. api 服务与路由:校验/歧义闸/决策解析/契约

- [x] 2.1 在 `apps/api/tests/test_task_collab.py` 先写失败测试:`set_step_topology` 名册逐条目 Bot 存在性校验(不存在 → 422 INVALID_BOT 指明条目,圆桌同款映射)、名册 + 顶层 `bot_key` 歧义闸拒绝(判原始载荷)、无名册 + 顶层 `bot_key` 照旧受理
- [x] 2.2 在同文件先写失败测试:`_collab_decision` 分工分支逐条目 Bot 运行时解析(解析失败如实降级 None,不虚报)、决策载荷携带逐条目解析结果
- [x] 2.3 实现 `apps/api/kongming/task/service.py`(校验循环、歧义闸判 raw、决策解析)与 `task/router.py`(`CollabTopologyIn` 增 `workers` / 新增 `CollabWorkerIn`,限长同圆桌条目口径),跑 2.1–2.2 测试转绿
- [x] 2.4 `make contracts` 重生成 `contracts/openapi.json` 与 `apps/web/src/lib/api-types.gen.ts`,md5 验真同步(记忆口径:未提交树必红属预期,提交时收敛)

## 3. agent 编排核心:名册感知分配制(剧本测试)

- [x] 3.1 在 `apps/agent/tests/test_collab_gen.py` 先写失败剧本:有 roster 时拆解 prompt 为分配制(输出 `{subgoal, worker}`),分配在名册内 → 派发对应槽位;名册外/未分配 → 进 `pending_subgoals` 不派发;畸形输出(非数组/缺字段)回落既有兜底语义
- [x] 3.2 实现 `apps/agent/kongming_agent/collab_gen.py`:roster 入参(成员标签清单,零 api 句柄)、分配制 prompt、解析映射、未映射披露;`WorkerRunner` 签名扩为 `(subgoal, ctx, slot)`(slot=None 现行为);核实/修订重派 slot=None(D4 治理动作无绑定),跑 3.1 转绿
- [x] 3.3 同文件补预算剧本:名册形态初派仍受 `max_workers` 与预算钳制,一成员多子目标为多账本行,触顶语义不变

## 4. agent 接线:per-worker 装配与账本

- [x] 4.1 在 `apps/agent/tests/test_collab_wiring.py` 先写失败剧本:名册装配——声明绑定解析失败 fail-closed 剔除披露、Bot 工具求交空集剔除披露、超执行面部署上限剔除披露(式样对齐圆桌装配段);全员剔除不开工 ERROR 诚实收口
- [x] 4.2 同文件先写失败剧本:per-worker 注入——成员系统提示按其 Bot 注入、工具 = 任务授权 ∩ 该成员 Bot 名单(逐 worker 求交不提权)、`collab_worker_start` 落 slot 的 bot_key、subgoal 携带成员标签;无名册路径 slot=None 全量照旧行为
- [x] 4.3 实现 `apps/agent/kongming_agent/collab_wiring.py` 分工分支名册装配与 `_worker` 槽位化,跑 4.1–4.2 转绿

## 5. web 运行视图与门禁

- [x] 5.1 `apps/web` 协作运行视图透出名册绑定(成员标签/bot_key,worker 行既有数据面),类型走 `api-types.gen.ts` 再生成件;`pnpm typecheck && pnpm lint` 过
- [x] 5.2 端到端冒烟:起 env 栈与双进程,声明带名册(2 Bot)的分工步骤执行一次,核对账本逐 worker bot_key、剔除/未派发披露与 `make check` 全绿

## Workflow follow-up

- 归档走 `/opsx:archive`(apply 完成且验收后,由 owner 触发)
