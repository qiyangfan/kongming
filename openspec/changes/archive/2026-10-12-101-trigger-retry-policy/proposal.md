# Proposal

## Why

触发器失败「v1 不自动重试」是 spec 明文登记的后续能力:瞬时故障(目标工作流
依赖的外部系统抖动、网络闪断)导致的触发失败没有恢复路径——只能等下一个
cron 周期(低频触发器可能等一整天)或人工重跑。触发器定位是「主动执行」,
可靠性语义缺重试是明显短板;产品待办已登记「触发器失败重试策略」。

## What Changes

- 触发器级失败重试次数上限(`max_retries`,0–5,缺省 0=不重试,零回归),
  创建/编辑表单可配。
- 失败重试采用**调度重排**而非执行内联:FAILED 终态且剩余次数 >0 时,调度器
  按固定退避间隔(部署级可配,缺省 60s)安排重试;重试为**独立 run 行**
  (attempt 递增并关联首跑),执行历史完整保留中间失败。
- 重试语义边界:链内任一次 SUCCESS 即链终态成功;SKIPPED 不构成重试对象
  (并发保护语义,不重试);「前次未完成记 SKIPPED」既有语义对重试行同样
  生效;触发器被停用/删除时挂起的重试取消。
- 出站通知(notify_url)只在重试链**终态**发送一次(中间失败不发),
  避免通知风暴。
- schema:新 Alembic 迁移——`sys_trigger` 加 `max_retries` 列、
  `sys_trigger_run` 加 `retry_of_run_id` 与 `attempt` 列(均带缺省,存量
  行为零回归)。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `triggers-webhooks`:「触发器管理与执行历史」requirement 修订(「失败 v1
  不自动重试」句收口到新 requirement;执行历史含重试链)+ 新增「失败重试
  策略」requirement。

## Impact

- `apps/api`:triggers 域(调度器重排逻辑、执行历史查询含 attempt 链、
  通知终态化);新增迁移两列 + 一列。
- `apps/web`:触发器表单加 max_retries 字段(数字输入,0–5);类型经管线
  生成。
- `apps/agent`:零改动。
- 派发语义:重试与既有「创建者身份 + 全量防御层 + 不绕过」完全一致,
  无权限放大。
