# triggers-webhooks · Delta

## ADDED Requirements

### Requirement: cron 预览

系统 SHALL 提供 cron 表达式预览端点(`GET /system/triggers/cron-preview?expr=`,
system:trigger:manage 权限):返回按 Asia/Shanghai 时区自当前时刻起接下来的
3 次触发时刻(ISO 8601,时间递增);表达式非法或不可计算 SHALL 400 并附
可读原因。预览与创建/更新校验及调度器 SHALL 同源(同一解析/计算实现,
不出现第二套 cron 语义)。

#### Scenario: 预览返回递增的三次触发时刻

- GIVEN 合法表达式 `0 9 * * *`
- WHEN 请求预览
- THEN 返回 3 个按时间递增的 ISO 时刻,首值为次日 09:00(Asia/Shanghai)

#### Scenario: 非法表达式被拒

- GIVEN 表达式 `61 * * * *`
- WHEN 请求预览
- THEN 返回 400 并附可读原因(与创建期校验同文案口径)

## MODIFIED Requirements

### Requirement: 触发器管理与执行历史

系统 SHALL 提供触发器 CRUD+启停+执行历史分页(system:trigger:manage,
租户隔离,跨租户 404 同形);sys_trigger_run SHALL 记录每次触发
(SUCCESS/FAILED/SKIPPED+耗时+错误+目标 run 关联);目标执行
MUST 以定义创建者身份经既有防御层(无权限放大);失败 v1 不自动
重试。创建 SHALL 校验 target_key 在当前租户对应目标域(workflow/bot)中
真实存在,更新仅在 target_key 变更时重走同校验,不存在即 400
`TARGET_NOT_FOUND`——写边界 fail-fast,防拼错目标静默挂到运行期才暴露;
校验与派发期目标解析同口径(同源,不出现第二套存在性判定)。目标已被
删除的存量触发器 SHALL 仍可停用/删除/修改非目标字段(止损路径不被堵)。

#### Scenario: 管理与历史

- GIVEN 持 system:trigger:manage 的用户
- WHEN 创建/启停/删除触发器并查询历史
- THEN 落库并调度同步;历史分页含状态/耗时/错误

#### Scenario: 目标不存在创建被拒

- GIVEN 目标域中不存在 key 为 `wf_report` 的工作流
- WHEN 创建 target_type=workflow、target_key=wf_report 的触发器
- THEN 返回 400 TARGET_NOT_FOUND,不落库、不进调度

#### Scenario: 更新改指不存在目标被拒

- GIVEN 存量触发器原目标存在
- WHEN 更新请求把 target_key 改为不存在的工作流 key
- THEN 返回 400 TARGET_NOT_FOUND,原值不变

#### Scenario: 目标已删的存量触发器可止损

- GIVEN 存量触发器目标 workflow 已被删除
- WHEN 停用该触发器或修改非目标字段(名称/cron 等)
- THEN 正常执行(止损不被堵;派发失败由执行历史可见)

#### Scenario: 目标存在正常创建

- GIVEN 目标域存在启用中的 bot `after_sale`
- WHEN 创建 target_type=bot、target_key=after_sale 的触发器
- THEN 落库并调度同步

#### Scenario: 触发执行不绕过防御

- GIVEN 触发的工作流含 SQL 节点
- WHEN 触发执行
- THEN SQL 仍走六层防御(创建者身份行级权限适用)
