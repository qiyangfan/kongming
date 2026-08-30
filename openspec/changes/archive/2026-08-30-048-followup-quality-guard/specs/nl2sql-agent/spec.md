# nl2sql-agent spec delta(048-followup-quality-guard)

## MODIFIED Requirements

### Requirement: 多轮上下文链路打通(命脉级)

前端攒最近 5 轮 user 问句,控制面全链路透传 history 到 Agent 面,understand 节点用历史做指代消解与省略补全;不传 history 时向后兼容。指代消解 SHALL 保真:补全后的问句保留上轮全部限定条件(筛选/排序/数量限制/聚合口径),「这些/它们/上面提到的」SHALL 解析为对上轮结果集或上轮主体的追问,只补全语义 SHALL NOT 新增或篡改条件。**聚合追问 SHALL 显式汇总口径:追问上一轮整体的总量/总额/合计(「总共/合计/一共…多少」形态)时,补全后的问题 SHALL 明确为对上轮结果整体的单一聚合口径(如「这 10 个订单的支付总金额合计是多少」),SHALL NOT 改写为逐行明细口径**(048:改写问句的逐行/整体两读是 mt_006 聚合口径丢失的首因,聚合追问是多轮最高频真实问法)。

#### Scenario: 前端攒历史问句传后端
- GIVEN 用户已进行 2 轮对话
- WHEN 发起第 3 轮提问
- THEN 查询请求体含最近 5 轮 user 问句的 history 列表

#### Scenario: 控制面全链路透传 history 到 Agent
- GIVEN 查询请求含非空 history
- WHEN 控制面调用 Agent 面
- THEN Agent 请求体含相同 history,注入 AgentState

#### Scenario: understand 节点用历史做指代消解
- GIVEN history 非空,当前问句含指代词(「这些」/「它们」/「上面提到的」)
- WHEN understand 节点执行
- THEN 结合历史问句输出补全指代后的清晰问句,并传入 generate 节点

#### Scenario: 多轮连续查询上下文延续
- GIVEN 第 1 轮「查询租金最高的 5 部影片」已返回结果
- WHEN 第 2 轮问「这些影片各自的分类是什么」
- THEN 生成的 SQL 引用第 1 轮的影片范围(通过指代消解),执行成功

#### Scenario: 指代消解保留上轮限定条件
- GIVEN 第 1 轮为「租赁次数最多的前 5 位客户」(含 top-N 与排序限定)
- WHEN 第 2 轮问「他们各自的邮箱是什么」
- THEN 补全后的问句保留「租赁次数最多的前 5 位」限定(解析为对该结果集的追问),
  不退化为「所有客户的邮箱」

#### Scenario: 聚合追问补全为单一汇总口径(048)
- GIVEN 第 1 轮为「运费总额最高的前 10 个订单及其运费总额」
- WHEN 第 2 轮问「这些订单的支付总金额是多少」
- THEN 补全后的问题为整体聚合口径(如「这 10 个订单的支付总金额合计是多少」),
  生成 SQL 输出单行 SUM 汇总
- AND SHALL NOT 输出逐单明细行代替总额

#### Scenario: 指代消解不新增条件
- GIVEN 第 1 轮为「查询所有评分 NC-17 的影片」
- WHEN 第 2 轮问「其中租金大于 3 美元的有哪些」
- THEN 补全后的问句 = 上轮筛选条件 + 本轮新增条件的合取,不引入两轮均未出现的限定

#### Scenario: 契约向后兼容
- GIVEN 调用方不传 history
- WHEN 查询
- THEN Agent 用空 history 正常工作,不报错

#### Scenario: history 长度上限保护
- GIVEN 前端传了 10 轮历史
- WHEN understand 节点读取
- THEN 只取最近 5 轮,不因 history 过长导致 prompt token 爆炸

### Requirement: SQL 生成质量规则

generate 节点的系统提示必须强化:多表 JOIN 用显式连接条件(非隐式逗号 join)、聚合维度识别(GROUP BY + AVG/COUNT)、PostgreSQL 方言(ILIKE)、时间窗用 interval/DATE_TRUNC(非硬编码日期);且不影响单表简单查询。

统计/明细边界 SHALL 显式化:含 GROUP BY 或聚合函数(COUNT/AVG/SUM/MAX/MIN)的查询一律视为统计查询,SHALL NOT 加 LIMIT——用户明确要求 top-N/前 N 名时除外(此时按统计键排序后 LIMIT);仅逐行返回业务实体的明细查询才适用默认 LIMIT 100 保护。排序键可能并列时 SHALL 追加主键等唯一列作第二排序键,保证结果可复现(DISTINCT 去重查询除外,见投影纪律)。聚合小数值(AVG/比率/除法结果)SHALL 用 ROUND(…, 2) 统一舍入口径,禁止裸 ::numeric 截断或全精度直出。投影 SHALL 完备:问题点名的每个信息项(名称/数量/金额等)都必须出现在 SELECT 投影中;ID/编号列 SHALL NOT 替代名称列(用户点名编号或 ID 作排序键时可附加,名称列必须在投影中)。技术主键等代理键 SHALL 默认不进 SELECT 投影——用户未点名时投影不含 ID 列,用户明确点名 ID/编号时才投影(点名不拦);行标识需要机器可核对编号时 SHALL 优先业务编号列(订单号/编号类面向用户的列)而非代理主键。DISTINCT 去重查询 SHALL NOT 追加投影外的唯一列作排序键(SELECT DISTINCT 语义下 ORDER BY 列必须在投影内,主键入投影则去重失效),排序键 SHALL 取已在投影中的列,并列可能让位于去重语义。一对多 JOIN 后统计实体数量 SHALL 用 COUNT(DISTINCT 实体主键) 去重,防止扇出膨胀;JOIN 产生完全重复的明细行 SHALL 用 DISTINCT 去重;**「对应关系/名录」类明细经一对多 JOIN 后投影列组合重复(如同分类下多部影片重复出现同一演员)时亦 SHALL DISTINCT 去重——重复的对应行是噪声,不因问题未提「去重」而放行**(048:sakila_018 稳定失分靶点)。**追问上一轮整体的总量/总额/合计(「总共/合计/一共」)时 SHALL 输出对结果整体的单一聚合行(SUM/COUNT 等),SHALL NOT 逐行明细**(048:聚合口径丢失靶点,与 understand 显式汇总口径双层互锁)。用户请求写操作(增/删/改)时 SHALL 只生成查看相关数据现状的只读 SELECT,SHALL NOT 生成 WHERE FALSE 等规避形态假装完成。

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

#### Scenario: 对应关系类明细的组合去重(048)
- GIVEN 问题「演员与影片分类的对应关系前 100 条」(actor×film×category 一对多链,
  同一演员在同分类多部影片使投影组合重复)
- WHEN generate 节点执行
- THEN SQL 用 SELECT DISTINCT 去重,前 100 条不含重复对应行
- AND 排序键保持在投影内(与 DISTINCT 排序例外兼容)

#### Scenario: 一对多 JOIN 去重计数(016 失败归因⑤)
- GIVEN 问题「有多少客户租过影片」(rental 一对多扇出)
- WHEN generate 节点执行
- THEN SQL 用 COUNT(DISTINCT customer_id) 或等价去重形态,不按 rental 行数计数

#### Scenario: 聚合追问输出单行汇总(048)
- GIVEN 多轮追问「这些订单的支付总金额是多少」(上轮为 top-10 明细)
- WHEN generate 节点执行
- THEN SQL 输出对上轮结果集整体的单一 SUM 聚合行
- AND SHALL NOT 返回逐单明细行代替总额

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
