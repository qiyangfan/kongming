# 实施任务

每项按不超过两小时拆分;本次仅提案,以下任务均未实施。

## 1. 状态机与存储(测试先行)

- [x] 1.1 先写失败测试:状态迁移白名单(非法迁移拒绝+审计)、终态不复活、他人任务 404 同形、权限点 task:create/read/manage 授予与拒绝面。
- [x] 1.2 新建 Alembic 迁移:sys_agent_task / sys_agent_task_step 两表(UUID v7、VARCHAR+CHECK、timestamptz、预算与检查点 jsonb),验证升级与回滚。
- [x] 1.3 task 域包落地:状态机纯函数 + 步骤账本 + 事件落库;domain 零框架依赖过 import-linter。

## 2. 执行循环与检查点

- [x] 2.1 agent 面任务执行循环:目标→计划→逐步执行→检查点回写;工具调用 100% 走既有回调(六层防御回归先行)。
- [x] 2.2 步骤证据驱动回归:有证据才 DONE、失败/防御拒绝置 FAILED/SKIPPED——先写失败测试再实现;执行中追加步骤落事件。
- [x] 2.3 暂停/恢复/取消与重启标记 INTERRUPTED(启动扫描存量 RUNNING/PLANNING);恢复从最后检查点续跑不重复副作用。
- [x] 2.4 任务预算三约束原子累加、超限 PAUSED、诚实收口(部分完成披露)——预算退出回归先行。

## 3. 审批门与观测

- [x] 3.1 步骤审批门:归属校验/幂等/过期置 SKIPPED——越权与重复审批失败测试先行。(实现取简:不走待确认存储,内嵌步骤行状态 + require_confirm 一次性闸门;过期语义=步骤 updated_at + APPROVAL_TTL_HOURS(24h),审批读取侧 TTL 过滤 + 启动清扫置 SKIPPED,任务级 AWAITING 由显式 resume 续跑)
- [x] 3.2 任务事件流 + trace_root 贯穿 + 用量按任务维度归集(038/077 口径回归)。
- [x] 3.3 任务公开端点(创建/列表/详情/暂停/恢复/取消/审批)+ `make contracts` 重生成(ADR-005),核对无既有契约漂移。

## 4. 前端与验收

- [x] 4.1 任务列表/详情页(DataView 双形态、StatusBadge 状态、事件时间线);详情内暂停/恢复/取消操作。
- [x] 4.2 `pnpm typecheck && pnpm lint` + 宪法 Article 8 自检 grep;375/768/1280 亮暗两态走查。(tsc/eslint/vitest86 绿;走查=Playwright+系统 Chrome 实测 smoke088:/tasks 与 /tasks/{id} 六组合(3 档×亮暗),横滚溢出 0px、亮暗背景翻转、375 卡片形态/768+ 表格形态、审批按钮 6/6 在场,截图 12 张留档 /tmp/taskwalk)
- [x] 4.3 端到端冒烟:建任务→计划→执行→审批挂起→批准→续跑→预算暂停→恢复→DONE;`make check` 对应子集全绿并记录证据。(HTTP 全链内联驱动冒烟=19 用例含审批/预算/取消中止/续跑;浏览器级走查见 4.2)

## 5. 收尾

- [x] 5.1 独立代码审查(状态机/审批归属/预算原子性为重点),缺陷修复后复跑门禁。(子代理独立审查已做:P0×1(终态复活链:begin_step 缺活动态闸门+收口器绕过白名单+finish 非法迁移 500)、P1×6(审批 resume 旁路/规划态假 DONE/token 二次累加/墙钟死约束/审批过期未实现/CAS 非真乐观锁)、P2×11;已修复 P0 全链+P1 全部+P2 十项(同款 tenant_key 派发连 report 侧一并修),证据粒度弱一项登记缓议;api 1139+agent 486 全绿,ruff/mypy/pyright/lint-imports 全过,契约重生成)
- [x] 5.2 归档前 sync delta 进主 specs;核对与 tool-calling Todo 账本语义边界描述无冲突。
