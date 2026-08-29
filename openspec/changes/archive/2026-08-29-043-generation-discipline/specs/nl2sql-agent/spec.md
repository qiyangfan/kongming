# nl2sql-agent spec delta(043-generation-discipline)

## ADDED Requirements

### Requirement: 表范围纪律与如实说明短路

generate 环节 SHALL 具备表范围纪律:生成的表名与列名 MUST 仅来自本次
注入的「可用表结构」上下文(表关系段与历史成功 SQL 段仅作口径参考,
SHALL NOT 成为引入其中不存在的表的依据);可用表结构未覆盖问题所需
数据时,生成 SHALL 以哨兵(固定 token `NO_RELEVANT_TABLE`)如实说明,
系统提示 MUST 显式声明该哨兵为「只输出 SQL」规则的唯一例外。空表结构
占位文案 MUST NOT 诱导臆测(不得出现「尽力生成」类表述),SHALL 与
哨兵机制联动。generate 节点 SHALL 识别哨兵输出(去除包裹标记后大小写
不敏感全等比较)并短路终止:不进入本地预检与执行、不进入 selfheal
重试(零执行、零 LLM 重试消耗),以用户友好文案收敛为失败终态——文案
SHALL 面向用户说明「未找到相关数据表,可切换数据源或调整问法」,且
SHALL NOT 携带表名、schema 名或内部术语。上次执行错误为「表不存在」
类时,喂回生成环节的修正上下文 SHALL 附「所引表不在可用表结构清单中,
清单确无所需数据只输出哨兵」的引导。

#### Scenario: 知识未覆盖哨兵短路(零执行零重试)

- GIVEN default 租户可用表结构不含问题所需表(如问 sakila 员工租赁)
- WHEN 模型输出哨兵 NO_RELEVANT_TABLE
- THEN 请求终止,不发起 SQL 执行、不进 selfheal(sys_sql_audit 零新增执行记录)
- AND 响应为失败终态,error 为「未找到相关数据表,可切换数据源或调整问法」类友好文案

#### Scenario: 空表结构不诱导臆测

- GIVEN 召回零命中,表结构段为空占位文案
- WHEN 生成环节执行
- THEN 占位文案明确「严禁臆测表名或列名」并与哨兵机制联动
- AND 模型输出哨兵时按上一场景短路

#### Scenario: 表不存在类错误的自愈引导

- GIVEN 首轮模型编造表名,执行报 relation ... does not exist 进入 selfheal
- WHEN 重试生成时构造修正上下文
- THEN 修正上下文附「所引表不在可用表结构清单中,清单确无所需数据只输出 NO_RELEVANT_TABLE」引导
- AND 第二轮模型输出哨兵时短路,不再同因连败

#### Scenario: 哨兵识别的形态防御

- GIVEN 模型以 markdown 代码块包裹哨兵输出
- WHEN generate 节点检测
- THEN 去除包裹后全等命中,照常短路
- AND 哨兵 token 仅作为子串出现在解释性文本中时不触发短路(按普通输出走既有防御链)

#### Scenario: 既有生成链路零回归

- GIVEN 可用表结构覆盖问题所需数据
- WHEN 正常生成 SQL
- THEN 生成、预检、执行、汇总链路行为与引入前逐字一致(纪律条不误伤正常生成)
