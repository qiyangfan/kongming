# Spec Delta(nl2sql-agent)

## ADDED Requirements

### Requirement: 结构化 Schema 图驱动生成

Agent 的生成与校验 SHALL 共享同一份结构化 Schema 图:表、列、主键、外键,
以及在结构中可确定的表角色。生成阶段 SHALL 只允许引用图中存在的表与列;
文本 schema_hint 仍可作为提示材料,但 SHALL NOT 覆盖图的表/列白名单。图缺失
或旧响应未携带图时,Agent MAY 回退现有文本提示路径,并 SHALL 保持既有行为。

#### Scenario: 图驱动生成成功

- GIVEN 召回响应携带结构化 Schema 图,图中包含事件表、金额表及其连接维表
- WHEN 生成节点组装提示并生成 SQL
- THEN SQL 的表和列均来自该图
- AND 提示中的业务名称/列语义与图一致

#### Scenario: 图外表被拒绝

- GIVEN 生成的 SQL 引用了 Schema 图中不存在的表或列
- WHEN 生成后预检
- THEN 该 SQL SHALL NOT 进入执行
- AND 违规项进入定向自愈,要求只使用图中的表/列

#### Scenario: 无图旧路径零回归

- GIVEN 召回响应没有结构化 Schema 图(旧控制面或图组装失败)
- WHEN Agent 执行生成
- THEN 按既有 schema_hint 文本路径运行
- AND 不因缺少图而拒绝原本可执行的 SQL

### Requirement: 生成后语义校验与定向自愈

Agent SHALL 在 SQL 执行前执行确定性语义校验;校验只使用 SQL 结构、Schema 图、
表关系与问句,不调用第二个模型。校验至少覆盖以下结构上可判定的违规:

- 事件笔数从金额角色表计数,或事件时间使用金额时间;
- 事件计数关联可能一对多的明细/金额表后,未按事件主键去重或预聚合;
- 事件表自带员工/门店归属外键时,门店/员工归属绕过该路径,改用商品、库存等
  被操作对象所属维度。

违规 SQL SHALL NOT 进入执行。Agent SHALL 把具体违规项和修正方向交给定向
自愈;自愈耗尽后 SHALL 以明确的失败终态结束,SHALL NOT 执行错误 SQL,也
SHALL NOT 编造查询结果。表角色或归属路径无法从结构确定时,SHALL NOT 仅凭
猜测作硬拒绝。

#### Scenario: 金额表行数不冒充事件笔数

- GIVEN 问句要求统计业务事件笔数并汇总金额
- WHEN 候选 SQL 从金额角色表计数
- THEN 校验拒绝该 SQL,提示回到事件表计数并按事件主键关联金额
- AND 该 SQL 不被执行

#### Scenario: 一对多关联后计数去重

- GIVEN 事件表与金额/明细表是一对多关系
- WHEN 候选 SQL 直接 `COUNT(事件外键)` 且未去重或预聚合
- THEN 校验拒绝该 SQL,提示按事件主键去重或先子查询聚合

#### Scenario: 事件时间不被金额时间替代

- GIVEN 问句按事件发生时间统计
- WHEN 候选 SQL 使用金额角色的时间列分组
- THEN 校验拒绝该 SQL,提示使用事件时间列

#### Scenario: 事件归属优先于被操作对象归属

- GIVEN 事件表自带员工或门店归属外键
- WHEN 候选 SQL 通过商品/库存等被操作对象所属门店统计事件
- THEN 校验拒绝该 SQL,提示沿事件表自身归属外键关联门店

#### Scenario: 角色歧义不误拒

- GIVEN 结构证据无法唯一确定表的角色或归属路径
- WHEN 语义校验运行
- THEN 不做上述角色类硬拒绝,只执行可确定的结构规则
- AND 不把猜测写成用户可见的正确性结论

#### Scenario: 自愈耗尽拒绝执行

- GIVEN 语义违规在多轮定向自愈后仍未修正
- WHEN 重试预算耗尽
- THEN 返回明确失败终态
- AND 目标 SQL 未被交给执行链路

### Requirement: 语义护栏可观测与兼容

语义校验 SHALL 在阶段轨迹中留下 guard 结论与违规类别;用户可见响应 SHALL NOT
因此暴露内部完整 SQL 或敏感结构。通过校验的 SQL SHALL 继续经过既有防火墙、
data_scope 与只读执行路径,语义护栏 SHALL NOT 替代或削弱任何安全防线。

#### Scenario: 违规可追溯

- GIVEN 一条 SQL 被语义护栏拒绝
- WHEN 检查本轮阶段轨迹
- THEN 可看到 guard 阶段与违规类别
- AND 用户侧只看到明确的失败/修正说明,不泄露内部完整 SQL

#### Scenario: 正确 SQL 零回归

- GIVEN 一条 SQL 原本能通过既有校验并执行
- WHEN 结构化图和语义护栏可用
- THEN 该 SQL 的权限、data_scope 与执行结果不变
- AND 仅新增图范围/语义判定,不改变既有安全语义
