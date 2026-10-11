# Spec Delta

## MODIFIED Requirements

### Requirement: 触发器管理与执行历史

系统 SHALL 提供触发器 CRUD+启停+执行历史分页(system:trigger:manage,
租户隔离,跨租户 404 同形);sys_trigger_run SHALL 记录每次触发
(SUCCESS/FAILED/SKIPPED+耗时+错误+目标 run 关联);目标执行
MUST 以定义创建者身份经既有防御层(无权限放大);失败重试策略见
「失败重试策略」requirement(缺省不重试)。创建 SHALL 校验 target_key
在当前租户对应目标域(workflow/bot)中真实存在,更新仅在 target_key 变更时
重走同校验,不存在即 400 `TARGET_NOT_FOUND`——写边界 fail-fast,防拼错目标
静默挂到运行期才暴露;校验与派发期目标解析同口径(同源,不出现第二套
存在性判定)。目标已被删除的存量触发器 SHALL 仍可停用/删除/修改非目标字段
(止损路径不被堵)。执行历史 SHALL 呈现重试链:每行含尝试序号(attempt),
重试行 SHALL 可追溯其首跑(重试来源关联)。

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

#### Scenario: 历史呈现重试链

- GIVEN 某触发器首跑 FAILED 后经 2 次重试成功
- WHEN 查询执行历史
- THEN 可见 3 行(attempt=1/2/3),重试行可追溯到首跑,末行 SUCCESS

## ADDED Requirements

### Requirement: 失败重试策略

触发器 SHALL 支持失败重试次数上限配置(0–5,缺省 0=不重试,与既有行为
零回归)。FAILED 终态且剩余重试次数 >0 时,调度器 SHALL 按固定退避间隔
(部署级可配,缺省 60s)安排一次重试;重试为**独立执行历史行**(尝试序号
递增并关联首跑),MUST NOT 覆盖或改写先前失败行。重试执行 SHALL 与首跑
同身份(定义创建者)、同防御层(无权限放大)、同目标解析口径。重试链内
任一次 SUCCESS SHALL 终止重试(链终态成功);次数耗尽仍失败 SHALL 终态
FAILED。SKIPPED 行 SHALL NOT 触发重试(并发保护语义,不是执行失败);
「前次未完成记 SKIPPED」既有语义对重试行同样生效(重试执行期间到期的
cron 触发记 SKIPPED,不并发堆积)。触发器被停用或删除时,挂起未执行的重试
SHALL 取消(不再派发)。出站通知 SHALL 只在重试链终态发送一次(中间失败
不发送),避免通知风暴。

#### Scenario: 失败后自动重试成功

- GIVEN 触发器 max_retries=3,目标工作流首跑因外部系统抖动 FAILED
- WHEN 退避间隔到期
- THEN 调度器派发重试(attempt=2,关联首跑),执行成功则链终态 SUCCESS,
  不再继续重试

#### Scenario: 重试耗尽终态失败

- GIVEN 触发器 max_retries=2,连续 3 次(首跑+2 重试)均 FAILED
- WHEN 第 3 次失败落定
- THEN 链终态 FAILED,不再安排重试,历史保留全部 3 行

#### Scenario: 缺省不重试零回归

- GIVEN 触发器未配置重试(缺省 max_retries=0)
- WHEN 执行 FAILED
- THEN 行为与引入前一致(单行终态,无重试派发)

#### Scenario: SKIPPED 不触发重试

- GIVEN 触发器 max_retries=3,执行因「前次未完成」被记 SKIPPED
- WHEN 记录落定
- THEN 不安排重试(SKIPPED 非执行失败)

#### Scenario: 停用取消挂起重试

- GIVEN 某触发器存在挂起中的重试(退避未到期)
- WHEN 管理员停用该触发器
- THEN 挂起重试取消,退避到期后不再派发;重新启用从下个 cron 周期正常触发

#### Scenario: 通知只在链终态发一次

- GIVEN 触发器配置 notify_url 且 max_retries=2
- WHEN 首跑 FAILED、重试 SUCCESS
- THEN 出站通知恰发一次(终态成功结果),首跑失败不发送通知
