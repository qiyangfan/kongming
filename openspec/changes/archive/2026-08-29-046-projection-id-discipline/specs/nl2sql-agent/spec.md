# nl2sql-agent spec delta(046-projection-id-discipline)

## MODIFIED Requirements

### Requirement: SQL 生成质量规则

generate 节点的系统提示必须强化:多表 JOIN 用显式连接条件(非隐式逗号 join)、聚合维度识别(GROUP BY + AVG/COUNT)、PostgreSQL 方言(ILIKE)、时间窗用 interval/DATE_TRUNC(非硬编码日期);且不影响单表简单查询。

统计/明细边界 SHALL 显式化:含 GROUP BY 或聚合函数(COUNT/AVG/SUM/MAX/MIN)的查询一律视为统计查询,SHALL NOT 加 LIMIT——用户明确要求 top-N/前 N 名时除外(此时按统计键排序后 LIMIT);仅逐行返回业务实体的明细查询才适用默认 LIMIT 100 保护。排序键可能并列时 SHALL 追加主键等唯一列作第二排序键,保证结果可复现(DISTINCT 去重查询除外,见投影纪律)。聚合小数值(AVG/比率/除法结果)SHALL 用 ROUND(…, 2) 统一舍入口径,禁止裸 ::numeric 截断或全精度直出。投影 SHALL 完备:问题点名的每个信息项(名称/数量/金额等)都必须出现在 SELECT 投影中;ID/编号列 SHALL NOT 替代名称列(用户点名编号或 ID 作排序键时可附加,名称列必须在投影中)。技术主键等代理键 SHALL 默认不进 SELECT 投影——用户未点名时投影不含 ID 列,用户明确点名 ID/编号时才投影(点名不拦);行标识需要机器可核对编号时 SHALL 优先业务编号列(订单号/编号类面向用户的列)而非代理主键。DISTINCT 去重查询 SHALL NOT 追加投影外的唯一列作排序键(SELECT DISTINCT 语义下 ORDER BY 列必须在投影内,主键入投影则去重失效),排序键 SHALL 取已在投影中的列,并列可能让位于去重语义。一对多 JOIN 后统计实体数量 SHALL 用 COUNT(DISTINCT 实体主键) 去重,防止扇出膨胀;JOIN 产生完全重复的明细行 SHALL 用 DISTINCT 去重。用户请求写操作(增/删/改)时 SHALL 只生成查看相关数据现状的只读 SELECT,SHALL NOT 生成 WHERE FALSE 等规避形态假装完成。

#### Scenario: 多表 join 连接条件推导
- GIVEN 问题「查询每个客户租过的所有影片」(需 4 表连接)
- WHEN generate 节点执行
- THEN SQL 用显式 JOIN + ON 连接条件,基于外键关系

#### Scenario: 聚合维度识别
- GIVEN 问题「每个影片分类的平均租金」
- WHEN generate 节点执行
- THEN SQL 含 GROUP BY + AVG,统计查询不加行数限制截断语义

#### Scenario: 统计查询误加 LIMIT 拦截(016 失败归因①)
- GIVEN 问题「查询每位客户的支付总金额」(GROUP BY 统计,客户数 >100)
- WHEN generate 节点执行
- THEN SQL 不含 LIMIT(全部分组返回,不被明细保护截断)

#### Scenario: 用户明确 top-N 的统计查询保留 LIMIT
- GIVEN 问题「支付总金额前 10 的客户」
- WHEN generate 节点执行
- THEN SQL 含 ORDER BY 统计键 DESC + LIMIT 10

#### Scenario: 排序键并列追加唯一列 tiebreaker
- GIVEN 问题「租金最高的 5 部影片」(租金列存在并列值)
- WHEN generate 节点执行
- THEN SQL 排序含第二唯一排序键(如 ORDER BY rental_rate DESC, film_id LIMIT 5),
  结果集确定可复现

#### Scenario: DISTINCT 去重查询不追加投影外唯一列(046)
- GIVEN 明细查询经一对多 JOIN 产生重复行,生成 DISTINCT 去重且需排序
- WHEN generate 节点执行
- THEN 排序键取已在投影中的业务列,不追加投影外主键(不触发 PG
  「ORDER BY expressions must appear in select list」报错,主键也不入投影)
- AND 并列可能让位于去重语义(去重正确性优先于排序可复现)

#### Scenario: 聚合小数值统一 ROUND 口径(016 失败归因②)
- GIVEN 问题「每个影片分类的平均租金」
- WHEN generate 节点执行
- THEN 平均值表达式用 ROUND(AVG(…), 2) 形态,不裸 ::numeric 截断、不全精度直出

#### Scenario: 投影完备(016 失败归因③)
- GIVEN 问题「哪个分类的影片最多,数量是多少」
- WHEN generate 节点执行
- THEN SELECT 同时投影分类名称与数量两列(问题点名的信息项不丢失)

#### Scenario: ID 列不替代名称列
- GIVEN 问题「按影片ID排序,查询前100部影片的标题和分类名称」
- WHEN generate 节点执行
- THEN SELECT 投影影片标题(title)而非仅 film_id(ID 作排序键可附加,名称列必须在投影中)

#### Scenario: 技术主键默认不进投影(046)
- GIVEN 问题「列出所有影片的标题」(用户未点名 ID)
- WHEN generate 节点执行
- THEN SELECT 投影只含业务列(title 等),不含 film_id 等代理主键

#### Scenario: 用户点名 ID 不拦(046)
- GIVEN 问题「查询 film_id 为 5 的影片标题」(用户点名 ID)
- WHEN generate 节点执行
- THEN SQL 正常生成(WHERE film_id = 5,点名条件不拦),投影按点名信息项出

#### Scenario: 业务编号列优先作展示标识(046)
- GIVEN 明细列举且行需要编号标识,表中同时存在业务编号列与代理主键
- WHEN generate 节点执行
- THEN 投影附业务编号列(面向用户的编号)而非代理主键

#### Scenario: JOIN 完全重复明细行去重
- GIVEN 明细查询经一对多 JOIN 后存在完全重复行(同一客户两次租同一影片)
- WHEN generate 节点执行
- THEN SQL 用 DISTINCT 消除完全重复行

#### Scenario: 一对多 JOIN 去重计数(016 失败归因⑤)
- GIVEN 问题「有多少客户租过影片」(rental 一对多扇出)
- WHEN generate 节点执行
- THEN SQL 用 COUNT(DISTINCT customer_id) 或等价去重形态,不按 rental 行数计数

#### Scenario: 写操作请求诚实处理(005/006 冒烟观察)
- GIVEN 用户问题为「删除 name 为 X 的商品」(写操作请求)
- WHEN generate 节点执行
- THEN 只生成查看相关数据现状的只读 SELECT(如 SELECT 该商品行),不生成 WHERE FALSE 等规避形态

#### Scenario: PostgreSQL 方言正确使用
- GIVEN 问题「查询邮箱包含 smith 的客户」
- WHEN generate 节点执行
- THEN SQL 用 ILIKE 或 LOWER() + LIKE(大小写不敏感)

#### Scenario: 时间窗表达
- GIVEN 问题「上个月租借的影片」
- WHEN generate 节点执行
- THEN SQL 用 interval 或 DATE_TRUNC 表达相对时间

#### Scenario: 单表查询不过度复杂化
- GIVEN 问题「查询所有客户邮箱」
- WHEN generate 节点执行
- THEN SQL 仍是简单单表查询(强化规则不影响简单场景)

### Requirement: 表范围纪律与如实说明短路

generate 环节 SHALL 具备表范围纪律:生成的表名与列名 MUST 仅来自本次
注入的「可用表结构」上下文(表关系段与历史成功 SQL 段仅作口径参考,
SHALL NOT 成为引入其中不存在的表的依据);可用表结构未覆盖问题所需数据
时,生成 SHALL 以哨兵(固定 token `NO_RELEVANT_TABLE`)如实说明,系统
提示 MUST 显式声明该哨兵为「只输出 SQL」规则的唯一例外。「未覆盖」SHALL
为两级:可用表结构中无支撑本问题的表(表级),或问句所需关键信息项(排序/
筛选/统计所依据的列)不存在且无法由现有列合理推导(列级)——列级缺口
SHALL NOT 以其他列近似顶替冒充答案。空表结构占位文案 MUST NOT 诱导臆测
(不得出现「尽力生成」类表述),SHALL 与哨兵机制联动。generate 节点 SHALL
识别哨兵输出(去除包裹标记后大小写不敏感全等比较)并短路终止:不进入本地
预检与执行、不进入 selfheal 重试(零执行、零 LLM 重试消耗),以用户友好
文案收敛为失败终态——文案 SHALL 面向用户说明「未找到回答该问题所需的
数据,可切换数据源或调整问法」,且 SHALL NOT 携带表名、schema 名或内部
术语。上次执行错误为「表或列不存在」类时,喂回生成环节的修正上下文
SHALL 附「所引表或列不在可用表结构清单中,清单确无所需数据只输出哨兵」
的引导。

#### Scenario: 知识未覆盖哨兵短路(零执行零重试)

- GIVEN default 租户可用表结构不含问题所需表(如问 sakila 员工租赁)
- WHEN 模型输出哨兵 NO_RELEVANT_TABLE
- THEN 请求终止,不发起 SQL 执行、不进 selfheal(sys_sql_audit 零新增执行记录)
- AND 响应为失败终态,error 为「未找到回答该问题所需的数据,可切换数据源或调整问法」类友好文案

#### Scenario: 列级缺口如实说明(046)

- GIVEN 问题「价格最高的3个商品」而可用表结构无价格类列且无推导口径
- WHEN generate 环节执行
- THEN 模型输出哨兵短路(零执行零重试),SHALL NOT 以主键/时间列等其他列
  近似顶替排序或统计
- AND error 为「未找到回答该问题所需的数据」类友好文案(不实指「数据表」)

#### Scenario: 计算型指标不误伤列级哨兵

- GIVEN 问「销售额」而可用表结构无销售额列,但存在单价与数量列
- WHEN generate 环节执行
- THEN 正常生成推导表达式(如 SUM(单价×数量)),不触发哨兵(可合理推导
  不算缺口)

#### Scenario: 空表结构不诱导臆测

- GIVEN 召回零命中,表结构段为空占位文案
- WHEN 生成环节执行
- THEN 占位文案明确「严禁臆测表名或列名」并与哨兵机制联动
- AND 模型输出哨兵时按上一场景短路

#### Scenario: 表不存在类错误的自愈引导

- GIVEN 首轮模型编造表名或列名,执行报 relation/column ... does not exist 进入 selfheal
- WHEN 重试生成时构造修正上下文
- THEN 修正上下文附「所引表或列不在可用表结构清单中,清单确无所需数据只输出 NO_RELEVANT_TABLE」引导
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
