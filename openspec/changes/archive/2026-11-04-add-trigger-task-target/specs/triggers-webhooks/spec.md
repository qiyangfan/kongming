# Spec Delta: triggers-webhooks

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
存在性判定)。task 目标无 target_key 引用,SHALL 以模板校验替代存在性校验:
目标文本模板/数据源 key(须对创建者可见)/预算/可选协作拓扑在创建期
fail-fast,非法即 400;校验口径与派发期立项参数同源。目标已被删除的存量触发器
SHALL 仍可停用/删除/修改非目标字段(止损路径不被堵)。执行历史 SHALL 呈现
重试链:每行含尝试序号(attempt),重试行 SHALL 可追溯其首跑(重试来源关联)。

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

#### Scenario: task 目标模板非法创建被拒

- GIVEN 触发器创建者对数据源 `ds_x` 无可见权限
- WHEN 创建 target_type=task、模板引用 `ds_x` 的触发器
- THEN 返回 400(模板校验失败),不落库、不进调度

## ADDED Requirements

### Requirement: task 立项目标

系统 SHALL 支持 target_type=task 的触发器:配置含任务模板(目标文本模板/
数据源 key/预算/可选协作拓扑/确认模式)。事件到达时系统 SHALL 以模板+
事件载荷渲染目标文本,以**触发器定义创建者身份**创建任务(创建者账号态闸
复用,无权限放大);预算 SHALL 经既有部署级上限钳制;数据源可见性、SQL
执行防御、步骤审批门与任务事件账本 SHALL 与手工创建任务完全同口径。
触发 run 的 SUCCESS 语义为**立项成功**(任务后续生命周期归任务域账本,
不构成触发成败)。可选协作拓扑 SHALL 在计划生成后经既有拓扑声明路径应用,
应用失败 SHALL 落任务事件并以串行形态继续,SHALL NOT 静默丢弃声明。

#### Scenario: webhook 事件立项

- GIVEN task 目标触发器绑定 GitHub issue 适配器
- WHEN 合法 issue 事件通过签名校验与过滤闸
- THEN 以触发器创建者身份创建任务,run 记 SUCCESS 且目标 run 关联为任务 id

#### Scenario: 立项不放大权限

- GIVEN 触发器创建者仅对数据源 A 有权限,模板引用数据源 A
- WHEN 事件触发立项
- THEN 任务以创建者身份执行,SQL 走六层防御;任务可见性遵循任务域既有归属规则

#### Scenario: 模板拓扑应用失败可见

- GIVEN 模板声明的协作拓扑引用的 bot 已被删除
- WHEN 事件触发立项且计划生成
- THEN 任务事件记录拓扑应用失败,任务以串行形态继续,不静默丢弃

### Requirement: 两级事件过滤

task 目标的 WEBHOOK/EMAIL 事件 SHALL 经两级过滤后才立项:**程序层**
按触发器配置的确定性条件(动作类型/字段匹配)在调用语义判读前筛除噪音;
**语义层**对通过程序层的候选事件做单次判读(是否值得立项),判读结论与
理由 SHALL 落触发 run 历史。判读不可用(端点不可达/超时/报文畸形)SHALL
按 SKIPPED 处理并留原因,SHALL NOT 默认立项;SKIPPED SHALL NOT 触发重试。
CRON 触发 SHALL NOT 经过滤闸(定时触发无事件载荷,立项意图在创建期
已由模板确定)。判读内容 SHALL 只含事件标准化摘要,SHALL NOT 含凭据或
完整原始载荷。

#### Scenario: 程序层筛除噪音

- GIVEN 触发器配置仅接受 action=opened 的 GitHub 事件
- WHEN 一个 action=labeled 的事件到达
- THEN 程序层直接筛除,run 记 SKIPPED 与原因,不调用语义判读

#### Scenario: 语义判读否决立项

- GIVEN 候选事件通过程序层
- WHEN 语义判读结论为「不值得立项」
- THEN 不创建任务,run 记 SKIPPED 并含判读理由

#### Scenario: 判读不可用不默认立项

- GIVEN 候选事件到达但判读端点不可达
- WHEN 过滤闸执行
- THEN 记 SKIPPED(原因注明判读不可用),不立项、不重试

#### Scenario: CRON 免语义判读

- GIVEN task 目标的 CRON 触发器到期
- WHEN 调度器派发
- THEN 直接立项,不调用语义判读

### Requirement: 线程去重与事件归属

task 目标触发器 SHALL 支持线程键去重:线程键由适配器按事件推导
(GitHub 适配器 SHALL 内建 repo+issue/PR 推导;custom/email SHALL 支持
配置声明字段,未配置时视为无线程键)。同线程键存在未终态触发任务时,
后续事件 SHALL NOT 重复立项,SHALL 追加为该任务的任务事件(含事件来源
与摘要);既有任务终态后同线程键新事件 SHALL 立项新任务。

#### Scenario: 同线程不重复立项

- GIVEN 同一 GitHub issue 已存在未终态触发任务
- WHEN 该 issue 的新评论事件到达且通过过滤闸
- THEN 不创建新任务,事件追加为既有任务的账本事件

#### Scenario: 终态后重新立项

- GIVEN 同线程键的既有触发任务已 DONE
- WHEN 同线程新事件到达且通过过滤闸
- THEN 立项新任务,与旧任务无继承关系

### Requirement: 触发任务确认门

触发创建的任务 SHALL 默认进入**待确认**形态:落库为 PAUSED 并附
「触发立项待确认」任务事件,SHALL NOT 自动派发;归属人显式恢复后按
既有恢复语义派发。触发器 SHALL 可配置自动派发模式:确认门关闭时,
立项即按既有创建路径派发。两种模式下,预算钳制、步骤审批门、账本留痕
SHALL 完全一致。

#### Scenario: 默认待确认

- GIVEN 触发器为缺省确认模式
- WHEN 事件通过过滤闸立项
- THEN 任务落 PAUSED 且事件注明「触发立项待确认」,无编排派发

#### Scenario: 归属人确认后派发

- GIVEN 存在待确认的触发任务
- WHEN 归属人(task:manage)恢复该任务
- THEN 按既有恢复语义进入编排,与手工创建任务的恢复同口径

#### Scenario: 自动派发模式

- GIVEN 触发器显式配置自动派发
- WHEN 事件通过过滤闸立项
- THEN 任务创建后立即按既有创建路径派发,审批门与账本照常
