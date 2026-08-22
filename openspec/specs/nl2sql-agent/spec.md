# nl2sql-agent 能力域

## Purpose

NL2SQL 核心链路的能力深化:多轮上下文全链路打通(前端→控制面→Agent history 透传 + understand 指代消解)、SQL 生成质量规则(join 连接条件/聚合维度/方言/时间窗)、结构化洞察(summary + insights + stats,失败降级纯文本)、Schema 提示由 RAG 智能选取取代全表倾倒。

## Requirements

### Requirement: 多轮上下文链路打通(命脉级)

前端攒最近 5 轮 user 问句,控制面全链路透传 history 到 Agent 面,understand 节点用历史做指代消解与省略补全;不传 history 时向后兼容。指代消解 SHALL 保真:补全后的问句保留上轮全部限定条件(筛选/排序/数量限制/聚合口径),「这些/它们/上面提到的」SHALL 解析为对上轮结果集或上轮主体的追问,只补全语义 SHALL NOT 新增或篡改条件。

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

统计/明细边界 SHALL 显式化:含 GROUP BY 或聚合函数(COUNT/AVG/SUM/MAX/MIN)的查询一律视为统计查询,SHALL NOT 加 LIMIT——用户明确要求 top-N/前 N 名时除外(此时按统计键排序后 LIMIT);仅逐行返回业务实体的明细查询才适用默认 LIMIT 100 保护。排序键可能并列时 SHALL 追加主键等唯一列作第二排序键,保证结果可复现。聚合小数值(AVG/比率/除法结果)SHALL 用 ROUND(…, 2) 统一舍入口径,禁止裸 ::numeric 截断或全精度直出。投影 SHALL 完备:问题点名的每个信息项(名称/数量/金额等)都必须出现在 SELECT 投影中;ID/编号列 SHALL NOT 替代名称列(用户点名编号或 ID 作排序键时可附加,名称列必须在投影中)。一对多 JOIN 后统计实体数量 SHALL 用 COUNT(DISTINCT 实体主键) 去重,防止扇出膨胀;JOIN 产生完全重复的明细行 SHALL 用 DISTINCT 去重。用户请求写操作(增/删/改)时 SHALL 只生成查看相关数据现状的只读 SELECT,SHALL NOT 生成 WHERE FALSE 等规避形态假装完成。

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

### Requirement: 结果智能分析(结构化洞察)

summarize 节点输出结构化洞察:summary(一句话)+ insights[](聚合洞察 max/min/ratio、数据质量提示)+ stats;LLM 返回非 JSON 时降级为纯文本。

摘要 SHALL 事实性接地:summarize 喂给 LLM 的上下文 SHALL 含结果行数、本地确定性统计(stats)与前若干行预览,不只给行预览;summary 与 insights 的数值结论 SHALL 以所给统计与可见行为准,SHALL NOT 外推到不可见数据;用户请求写操作而实际执行的是只读查询时,摘要 SHALL 如实说明未执行写操作,SHALL NOT 声称删除/更新/插入成功。

#### Scenario: 聚合结果结构化洞察
- GIVEN 查询返回分组聚合结果
- WHEN summarize 节点执行
- THEN 返回 JSON 含 summary + insights(如 [{type: "max", text: "动作类最多 120 部"}])+ stats
- AND 前端渲染洞察卡片(不只一句话)

#### Scenario: 摘要上下文含行数与统计接地
- GIVEN 查询返回 50 行分组聚合结果
- WHEN summarize 节点构造 LLM 上下文
- THEN 上下文含结果行数、本地计算的数值列统计(max/min/avg)与前 5 行预览

#### Scenario: 数值结论不外推(005 冒烟观察)
- GIVEN 结果集 >5 行,前 5 行不含全局最大值
- WHEN summarize 节点执行
- THEN 摘要中的最值结论以所给统计为准,不以可见前 5 行外推

#### Scenario: 写操作请求摘要不谎称成功(005/006 冒烟观察)
- GIVEN 用户问题为「删除 name 为 X 的商品」,实际执行为只读 SELECT
- WHEN summarize 节点执行
- THEN 摘要说明未执行写操作(只读查询返回了相关数据现状),不声称「删除成功」

#### Scenario: 明细结果数据质量提示
- GIVEN 查询返回明细结果且某列有空值
- WHEN summarize 节点执行
- THEN insights 含 {type: "quality", text: "email 列有 3 个空值"}

#### Scenario: LLM 返回非 JSON 降级
- GIVEN summarize 节点 LLM 返回纯文本
- WHEN 解析失败
- THEN 退化为 summary=该文本,insights/stats 为空,不报错

### Requirement: Schema 智能选取

retrieve 节点 SHALL 从控制面获取 schema 提示,不维护硬编码全表提示。
提示源按序降级:①RAG 已落地——SHALL 经 `POST /internal/rag/retrieve`(内部
令牌)取三路召回结果,把提示收窄到相关表(≤topK),并同时携带历史成功 SQL
(few-shot);②表说明召回零命中(该租户+数据源知识库为空)或召回服务失败时
SHALL 回退实时内省兜底(005 过渡形态保留为兜底——新租户/未抓取知识的数据源
零质量回归);③内省亦失败时 SHALL 降级为空提示并提示 LLM「无法获取表结构」
——不误导、不硬编码全表,生成失败属可接受降级。历史成功 SQL SHALL 由
generate 节点以独立「参考历史成功 SQL」段拼入用户提示(仅非空时;系统提示
模板资产不变),其示例优先级高于表说明内嵌示例。

#### Scenario: 过渡形态内省全量提示
- GIVEN 该租户+数据源知识库无任何表说明(新租户/未抓取),或召回服务不可用
- WHEN retrieve 节点执行
- THEN 回退内部元数据通道获取表/列/注释(表数截断保护),拼入 generate 的 schema 提示
- AND 不硬编码任何表名清单于 Agent 面代码或提示模板

#### Scenario: 内省失败的空提示降级
- GIVEN 回退内省亦失败(网络/租户非法/超时)
- WHEN retrieve 节点降级
- THEN 返回空 schema 提示,generate 提示 LLM「无法获取表结构」
- AND 可能生成失败 SQL 是可接受的降级(不误导,不硬编码全表)

#### Scenario: 多数据集场景提示收窄
- GIVEN RAG 已落地,数据集有 11 张表,问题只涉及 2 表
- WHEN retrieve 节点拿到 RAG Top-K 召回(K=5)
- THEN 喂给 generate 的 schema 提示只含相关表(≤5 张),SQL 通过率不降

#### Scenario: RAG 完全失败的安全降级
- GIVEN RAG 召回服务不可用(网络/超时/5xx)
- WHEN retrieve 节点降级
- THEN 回退实时内省;内省亦失败时返回空 schema 提示,提示 LLM「无法获取表结构」
- AND 可能生成失败 SQL 是可接受的降级(不误导,不硬编码全表)

#### Scenario: 历史成功 SQL 注入 generate
- GIVEN 召回结果携带历史成功 SQL(该租户+数据源下语义相似的历史问句)
- WHEN generate 节点拼接用户提示
- THEN 含「参考历史成功 SQL」段(位于表结构提示之后),系统提示模板不变
- AND 无历史命中时该段不出现,提示形态与 005 一致

### Requirement: selfheal 错误分类(省 LLM 配额)

selfheal 按错误类型分类:权限/防火墙拦截类直接终止不重试;语法/执行错误才重试,节省 LLM 配额。

#### Scenario: 权限拦截不重试
- GIVEN 生成的 SQL 被防火墙拦截(FORBIDDEN_DML)
- WHEN 路由决策
- THEN 直接终止,不进入 selfheal,不浪费 LLM 重试配额

#### Scenario: 语法错误仍重试
- GIVEN 生成 SQL 有语法错误(非权限类)
- WHEN execute 失败
- THEN 进入 selfheal 重试(retry_count < MAX_RETRY),重试耗尽才终止


### Requirement: 流式事件发射(自定义流通道)

Agent 面问答图节点 SHALL 支持在流式调用下发射过程事件:每个节点进入时
发射 stage(running)、完成时发射 stage(done);节点名 ∈ understand/
retrieve/generate/validate/execute/selfheal/summarize(与图拓扑一致)。
validate 通过时 SHALL 发射 sql 事件(内容=当前生成的 SQL);summarize
完成时 SHALL 发射 summary 事件(delta=完整总结文案,单条)。
selfheal 的 stage 事件 SHALL 携带重试序号(retry_count=本次重试序号),
其余节点 retry_count=当前已重试次数。事件发射点 SHALL NOT 改动节点
业务逻辑与 prompt 资产;非流式调用(invoke/单测直调)下事件发射 SHALL
静默跳过,节点行为零变化。

#### Scenario: 节点 stage 事件成对发射
- GIVEN 流式调用问答图
- WHEN 任一节点执行
- THEN 该节点 stage(running)与 stage(done)事件先后发射,data 携带节点名

#### Scenario: 校验通过发射 sql 事件
- GIVEN generate 产出 SQL 且 validate 通过
- WHEN validate 节点完成
- THEN 发射 sql 事件,内容与 state 中生成的 SQL 一致
- AND 校验不通过时 不发射 sql 事件

#### Scenario: 自愈重试序号随 stage 事件透出
- GIVEN 触发自愈(第 1 次重试)
- WHEN selfheal 节点完成
- THEN selfheal 的 stage 事件 retry_count=1

#### Scenario: 非流式调用零行为变化
- GIVEN 同步调用问答图(invoke)
- WHEN 节点执行
- THEN 无事件副作用,返回 state 与流式改造前逐字段一致

### Requirement: 流式问答端点(Agent 面)

Agent 面 SHALL 提供流式问答端点(内部令牌认证,ADR-002 信任边界):
以自定义流通道转发节点事件,以 updates 通道累积终态;终态 result 事件
data SHALL 与同步问答端点响应同构(单一定义防字段漂移);图执行意外
异常 SHALL 收敛为 error 终态事件(友好文案),不裸 500;LLM 故障
 SHALL 与同步端点同口径收敛(success=false 的 result 事件)。
流式消费方断开时 SHALL NOT 影响图执行完整性判定(终态由消费方语义
决定,Agent 面单次执行到终态为止)。

#### Scenario: 流式端点终态与同步同构
- GIVEN 同一输入分别调用同步与流式端点
- WHEN 两请求完成
- THEN 流式 result 事件 data 与同步响应逐字段一致

#### Scenario: 图执行异常收敛 error 终态
- GIVEN 图执行抛出意外异常
- WHEN 流式响应进行
- THEN 下发 error 终态事件(友好文案),不裸 500
