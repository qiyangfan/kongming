# nl2sql-agent 能力域

## Purpose

NL2SQL 核心链路的能力深化:多轮上下文全链路打通(前端→控制面→Agent history 透传 + understand 指代消解)、SQL 生成质量规则(join 连接条件/聚合维度/方言/时间窗)、结构化洞察(summary + insights + stats,失败降级纯文本)、Schema 提示由 RAG 智能选取取代全表倾倒。

## Requirements

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

### Requirement: 对话复语不进取数补全

多轮上下文补全 SHALL 先做**零模型调用**的直通判定:当前问句规范化后
(去首尾与内部空白、去中英文句读标点、拉丁字母小写)**全等于**一个闭合的
纯对话复语词表成员时,系统 SHALL NOT 发起指代消解/省略补全调用,
SHALL NOT 进入检索与 SQL 生成,该轮 SHALL 以族别中性应答文案作答并
按既有中性第四态收敛——应答文案按词表三族(否定与终止/应答与致谢/
招呼)取对应静态文案,SHALL 为零模型调用,SHALL NOT 呈现「未找到数据」
类数据缺失文案(招呼与致谢不是数据问题,SHALL NOT 以数据问题的话术
作答)。该轮 SHALL 携带兜底分类标记 CHITCHAT(经管理面诊断字段回传,
SHALL NOT 进入用户响应载荷)。

复语词表 SHALL 覆盖三类且为**闭合枚举、全等匹配**:否定与终止、
应答与致谢、招呼。SHALL NOT 采用包含、前缀或语义相似度匹配——
「取消订单」「确认切换工作模式」这类以复语词起头的真实问法
SHALL 照常进入补全。词表 SHALL 随代码发布,SHALL NOT 做成运行期可热改配置。

**动机约束**:补全提示词要求"把当前问题补全为一个独立可理解的查询意图",
对不携带取数意图的输入,该指令本身即制造臆造(补全素材只能取自上一轮问句),
故复语轮 SHALL 在执行前被拦为直通,而非交由下游判别。

直通命中数 SHALL 计入可观测,作为词表宽严的调整依据。

#### Scenario: 致谢与终止语直通零执行

- GIVEN 会话已有上一轮查询,本轮输入为纯复语「不用了」
- WHEN 问答链路执行
- THEN 不发起补全调用、不检索、不生成 SQL、不执行(对目标库零调用),
  该轮以中性应答文案作答(否定终止族)并按中性第四态收敛

#### Scenario: 招呼语不作数据缺失应答

- GIVEN 会话无任何历史,用户首句为「你好」
- WHEN 问答链路执行
- THEN 该轮以招呼族中性文案作答,响应与历史回显 SHALL NOT 出现
  「当前数据源未找到…」类数据缺失文案

#### Scenario: 复语起头的真实问法不被误拦

- GIVEN 上一轮为门店库存查询,本轮问「取消订单的门店有哪些」
- WHEN 问答链路执行
- THEN 该问句不被判为复语,照常进入补全与后续链路

#### Scenario: 句读与大小写不影响判定

- GIVEN 输入为「  不用了。」「NO, thanks!」「好的」等形态差异
- WHEN 直通判定执行
- THEN 规范化后命中闭合词表即直通;词表外的输入(如「好的那看看上海仓」)
  不被判为复语

#### Scenario: 单轮无历史同样直通

- GIVEN 会话无任何历史,用户首句即为招呼或致谢
- WHEN 问答链路执行
- THEN 不发起补全调用、不检索、不执行,以族别中性文案作答并收敛

#### Scenario: 既有重试铁律不受影响

- GIVEN 直通出口以条件边承载
- WHEN 图拓扑被执行与测试
- THEN 数据查询路径的节点链与重试语义逐字不变,任何重试仍必经自愈节点

### Requirement: 歧义问句澄清引导

问答链路 SHALL 在检索与生成前识别**高置信歧义**问句并澄清反问:判定
SHALL 与意图路由**共用同一次模型调用**(在既有意图路由输出上扩展歧义
字段,SHALL NOT 新增每轮模型调用)。命中口径 SHALL 保守且全部满足:
问句缺关键查询口径(如指标/时间范围/主体限定)、会话历史无法补全该
口径、且按任一合理解读执行都会实质改变查询结果。命中时该轮 SHALL
早退为澄清反问——SHALL NOT 检索、SHALL NOT 生成 SQL、SHALL NOT 执行,
回答为引导用户补全口径的单一澄清问句(问句须可被一句话回答)。
判定 SHALL fail-open:模型失败、超时、输出不可解析或置信不足时
SHALL NOT 澄清,照常进入既有链路(宁可猜错被点踩,SHALL NOT 让澄清
打断可回答的问题)。防循环:上一轮已为澄清轮的会话,本轮 SHALL NOT
再命中澄清判定(直接照常链路);澄清问句轮与普通轮的后续轮次 SHALL
经既有指代消解通道自然衔接(用户补全口径后即正常执行),SHALL NOT
引入独立澄清会话状态。

#### Scenario: 缺口径问句澄清

- GIVEN 单轮会话,问句「帮我看看销售情况」(无时间范围/无主体限定)
- WHEN 链路执行且歧义判定高置信命中
- THEN 不检索、不生成 SQL、不执行,回答为澄清反问(如「想看哪个时间
  范围、哪个口径的销售数据?」)

#### Scenario: 历史可补全不澄清

- GIVEN 上一轮为「上海仓 3 月出库量」,本轮问「那 4 月呢」
- WHEN 歧义判定执行
- THEN 指代消解已补全口径,判定不命中,照常进入检索与生成

#### Scenario: 判定 fail-open 零回归

- GIVEN 意图路由模型调用失败或输出不可解析
- WHEN 歧义问句进入链路
- THEN 不澄清,照常检索与生成(与既有路由回退行为一致)

#### Scenario: 澄清不循环

- GIVEN 上一轮为澄清轮,用户本轮补充仍不完整(或继续寒暄)
- WHEN 链路执行
- THEN 本轮不再触发澄清,照常进入既有链路(含复语直通等既有出口)

#### Scenario: 澄清轮后补全口径正常执行

- GIVEN 上一轮为澄清轮「想看哪个时间范围?」,用户答「上个月的」
- WHEN 链路执行
- THEN 指代消解结合澄清问句与回答补全为自包含问题,照常检索生成执行

### Requirement: 闲聊语义兜底判定

意图路由模型调用 SHALL 同时输出闲聊判定(第四意图值),接住闭合词表
漏网的、完全不携带取数意图的输入(寒暄/致谢/客套,如「太感谢了」
「在吗」):高置信命中时该轮 SHALL 早退为中性应答文案并携带 CHITCHAT
标记,SHALL NOT 进入检索/生成/执行。判定 SHALL fail-open:模型失败、
输出不可解析或置信不足时 SHALL NOT 判闲聊,照常进入既有链路(宁可
照常链路误答被点踩,SHALL NOT 误拦真实问句;判定口径 SHALL 要求
「完全无取数意图」)。闲聊判定与澄清判定 SHALL 互斥:澄清 SHALL 仅
作用于携带取数意图但缺关键口径的问句,两类判定同帧产出,冲突时 SHALL
以闲聊判定优先(确定性解析规则)。语义路径的闲聊应答 SHALL 为统一
中性文案(零额外模型调用产出),SHALL NOT 呈现数据缺失文案;词表
直通快路径 SHALL 保留(精确命中零模型调用短路),语义层仅兜底漏网。

#### Scenario: 词表漏网闲聊被语义层接住

- GIVEN 用户输入「太感谢了,真是帮大忙了」(词表全等不命中)
- WHEN 链路执行且意图路由帧高置信判闲聊
- THEN 不检索、不生成 SQL、不执行,以统一中性文案作答并携带
  CHITCHAT 标记

#### Scenario: 短真实问句不被误判闲聊

- GIVEN 用户输入「帮我把上月销量排个序」
- WHEN 意图路由帧执行
- THEN 不判闲聊,照常进入检索与生成

#### Scenario: fail-open 漏网与现态一致

- GIVEN 意图路由模型调用失败或输出不可解析
- WHEN 词表漏网的闲聊输入进入链路
- THEN 不判闲聊,照常进入既有链路(与引入前行为一致)

#### Scenario: 闲聊判定优先于澄清

- GIVEN 同帧输出中闲聊命中与歧义命中同时出现(模型输出冲突)
- WHEN 解析收口
- THEN 以闲聊判定优先,早退为闲聊应答,不产出澄清问句

#### Scenario: 与复语直通分层

- GIVEN 用户输入「不用了」(词表精确命中)
- WHEN 链路执行
- THEN 直通快路径零模型调用短路,语义层不被触发

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

### Requirement: 结果智能分析(结构化洞察)

summarize 节点输出结构化洞察:summary(一句话)+ insights[](聚合洞察 max/min/ratio、数据质量提示)+ stats;LLM 返回非 JSON 时降级为纯文本。

摘要 SHALL 事实性接地:summarize 喂给 LLM 的上下文 SHALL 含结果行数、本地确定性统计(stats)与前若干行预览,不只给行预览;summary 与 insights 的数值结论 SHALL 以所给统计与可见行为准,SHALL NOT 外推到不可见数据;用户请求写操作而实际执行的是只读查询时,摘要 SHALL 如实说明未执行写操作,SHALL NOT 声称删除/更新/插入成功。

摘要 SHALL 称谓对齐:summarize 喂给 LLM 的上下文 SHALL 含所涉表的业务称谓别名(alias,随召回 schema_hint 段注入);summary 与 insights 提及数据实体时 SHALL 使用业务称谓——用户用语优先,其次所给别名;SHALL NOT 只写物理表名,SHALL NOT 出现 schema 全限定名;首次提及可以「业务名(物理名)」并注;别名缺失时回退表说明中的中文名称;SHALL NOT 编造别名(与「数值结论不外推」同构的事实性约束)。

**统计 SHALL 具备度量资格(050)**:本地确定性统计对数值列的取舍 SHALL 经列角色判定——列名为标识形态(`id/no/code` 后缀)或期间词(year/quarter/month/week/day)、或整数取值呈小值域稠密序列(起点 0/1、连续、去重数≤16、上界≤64)的列,SHALL NOT 产出 max/min/avg 统计(标识与期间量无统计语义);过滤同时作用于喂给 LLM 的上下文与回传结果,SHALL NOT 在洞察文本中对标识/期间列给出均值类结论。bool 列 SHALL NOT 进入数值统计(既有语义保持)。

#### Scenario: 聚合结果结构化洞察
- GIVEN 查询返回分组聚合结果
- WHEN summarize 节点执行
- THEN 返回 JSON 含 summary + insights(如 [{type: "max", text: "动作类最多 120 部"}])+ stats
- AND 前端渲染洞察卡片(不只一句话)

#### Scenario: 摘要上下文含行数与统计接地
- GIVEN 查询返回 50 行分组聚合结果
- WHEN summarize 节点构造 LLM 上下文
- THEN 上下文含结果行数、本地计算的数值列统计(max/min/avg)与前 5 行预览

#### Scenario: 标识列不进统计(050)
- GIVEN 结果含整型标识列 store_id(取值 1/2)与度量列 inventory_count
- WHEN compute_stats 执行
- THEN stats 含 inventory_count 的 max/min/avg,SHALL NOT 出现 store_id 的任何统计键
- AND 喂给 LLM 的上下文同样不含标识列统计,洞察 SHALL NOT 出现「门店 ID 均值」类结论

#### Scenario: 期间列不进统计(050)
- GIVEN 结果含整型期间列 year(取值 2005/2006)
- WHEN compute_stats 执行
- THEN SHALL NOT 出现 year_max/year_min/year_avg 统计键

#### Scenario: 数值结论不外推(005 冒烟观察)
- GIVEN 结果集 >5 行,前 5 行不含全局最大值
- WHEN summarize 节点执行
- THEN 摘要中的最值结论以所给统计为准,不以可见前 5 行外推

#### Scenario: 实体称谓以业务名表达
- GIVEN 用户提问「商品表有多少种商品」(goods 表 alias=「商品表」已注入上下文)
- WHEN summarize 节点执行
- THEN 摘要称「商品表」而非 goods;不出现 tenant_default.goods 全限定名;首次提及可「商品表(goods)」并注

#### Scenario: 用户用语优先于别名
- GIVEN 用户称某表为「订单主表」而 alias 为「订单表」
- WHEN summarize 节点执行
- THEN 摘要沿用用户的「订单主表」,不被别名带偏

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
SHALL 回退实时内省兜底(005 过渡形态保留为兜底——新租户/未扫描知识的数据源
零质量回归);③内省亦失败时 SHALL 降级为空提示并提示 LLM「无法获取表结构」
——不误导、不硬编码全表,生成失败属可接受降级。历史成功 SQL SHALL 由
generate 节点以独立「参考历史成功 SQL」段拼入用户提示(仅非空时;系统提示
模板资产不变),其示例优先级高于表说明内嵌示例。**请求携带多轮 history 时,
SHALL 以最近一条历史问句独立执行一路向量+全文召回,将其命中按序合并进融合
结果(主路命中优先,重复 (schema,table) 去重,总量封顶 2×top-K)**——多轮
追问经 understand 改写后语义完整,但召回对改写问句仍可能丢失上轮依赖表
(047 实证:追问「他们各自的邮箱」召回缺排名依据表 rental,模型越界引用
清单外表/列);上轮命中表是本轮查询的确定性上下文,继承 SHALL 是结构性行为
而非依赖向量相似度。ctx 缓存键 SHALL 并入历史指纹(末条历史问句),同问句
不同上下文 SHALL NOT 互串缓存。history 为空(单轮)时行为与单路召回逐字节
同形。

#### Scenario: 过渡形态内省全量提示
- GIVEN 该租户+数据源知识库无任何表说明(新租户/未扫描),或召回服务不可用
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

#### Scenario: 多轮追问继承上轮依赖表(047)
- GIVEN 第一轮问句「查询租赁次数最多的前 5 位客户」命中 customer/rental
- WHEN 第二轮追问「他们各自的邮箱是什么」(history 携带第一轮问句),
  本轮主路召回未命中 rental
- THEN 合并结果包含 rental(继承路补入)
- AND 生成 SQL 不再引用『可用表结构』清单之外的表

#### Scenario: 继承不重复且封顶(047)
- GIVEN 主路与继承路命中大量重叠表
- WHEN 合并
- THEN 重复 (schema, table) 只保留主路位次,总量不超过 2×top-K

#### Scenario: 单轮零回归(047)
- GIVEN 请求不携带 history
- WHEN 召回
- THEN 与单路召回行为一致(不发起继承路,缓存键与现状相同)

#### Scenario: 同问句不同上下文缓存不互串(047)
- GIVEN 同一问句分别以 history=[A] 与 history=[B] 请求
- WHEN 召回
- THEN 两次使用不同缓存键,各自命中各自的上下文

### Requirement: selfheal 错误分类(省 LLM 配额)

selfheal 按错误类型分类:权限/防火墙拦截类直接终止不重试;语法/执行错误才重试,节省 LLM 配额。**generate 组装喂回时 SHALL 按 PG 错误形态附加针对性修正指令**(047:泛化「请修正」使机械错同因连败,实证 missing FROM-clause 重试 2 轮同错):`missing FROM-clause entry for table "X"` → 指示补 X 的 JOIN 或改用已定义别名、不得引用『可用表结构』之外的表;`column "…" does not exist` → 指示只用清单内列并核对拼写、缺口径列不得近似顶替(046 列级哨兵的喂回镜像,SHALL NOT 引向表哨兵出口);`relation "…" does not exist` → 指示核对清单、确无数据只输出 NO_RELEVANT_TABLE(043 出口收窄到关系类);其余形态维持泛化喂回。

#### Scenario: 权限拦截不重试
- GIVEN 生成的 SQL 被防火墙拦截(FORBIDDEN_DML)
- WHEN 路由决策
- THEN 直接终止,不进入 selfheal,不浪费 LLM 重试配额

#### Scenario: 语法错误仍重试
- GIVEN 生成 SQL 有语法错误(非权限类)
- WHEN execute 失败
- THEN 进入 selfheal 重试(retry_count < MAX_RETRY),重试耗尽才终止

#### Scenario: 未定义别名错误获得针对性指令(047)
- GIVEN 上一轮 SQL 执行失败,error 含 `missing FROM-clause entry for table "s"`
- WHEN generate 组装喂回
- THEN user 提示附加「补 JOIN 或改用已有别名」类修正指令
- AND SHALL NOT 附加 NO_RELEVANT_TABLE 哨兵出口(别名错可修,不是弃答出口)

#### Scenario: 列不存在不误引表哨兵(047)
- GIVEN error 含 `column "email" does not exist`
- WHEN generate 组装喂回
- THEN 指令为列纪律(只用清单内列、核对拼写、不得近似顶替)
- AND 不出现「只输出 NO_RELEVANT_TABLE」指引(表存在,出口是改列不是弃答)

#### Scenario: 表不存在保持哨兵出口(047)
- GIVEN error 含 `relation "public.staff" does not exist`
- WHEN generate 组装喂回
- THEN 指令含「核对清单;确无数据只输出 NO_RELEVANT_TABLE」
- AND 关系类以外的 does-not-exist 形态(函数/类型等)维持泛化喂回,
  SHALL NOT 误引表哨兵出口

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

### Requirement: ReAct 内核并存与终态等价

Agent 面 SHALL 在既有 8 节点 NL2SQL 问答图之外并存 ReAct 工具调用内核(独立端点),两内核的终态响应单一定义、逐字段同构(success/verdict/summary/insights/stats/columns/rows/sql/retry_count/error)。既有问答图的节点逻辑、路由语义、prompt 资产与流式事件契约 SHALL NOT 因 ReAct 内核引入而变化;ReAct 链路的流式事件在既有事件清单上纯增 tool_call 类型,老消费方不受影响。两内核 SHALL 共享同一套 LLM 客户端纪律(无自写重试,ReAct 循环本身即重试/决策机制)与同一套控制面回调鉴权口径(X-Internal-Token)。

#### Scenario: 双内核终态同构

- GIVEN 同一数据查询问题分别经 8 节点图与 ReAct 内核处理
- WHEN 两请求完成
- THEN 两者响应携带相同字段集合与语义(columns/rows 来自最终一次成功 SQL 执行,summary 为最终自然语言总结)

#### Scenario: 既有问答图零回归

- GIVEN 019 引入 ReAct 内核后
- WHEN 调用既有 /query 或 /query/stream
- THEN 节点链、事件序列与终态字段与引入前逐字段一致(016 评估基线不降)

#### Scenario: tool_call 事件与既有事件并列

- GIVEN ReAct 流式端点消费方
- WHEN 循环中发生工具调用
- THEN 事件流中 tool_call 事件与 stage/result/error 事件并存,既有事件的字段与语义不变

### Requirement: LLM 调用的任务级路由挂接

Agent 面 LLM 客户端 SHALL 在既有 chat/embeddings 接口上纯增任务类型参数(缺省=主模型,既有调用方零改动零回归);各图节点按语义传入任务类型(generate=SQL_GEN、understand=UNDERSTAND、summarize=SUMMARIZE、标题改写=TITLER、表润色=DESCRIBE、ReAct 决策=REACT)。路由判定 SHALL 在 LLM 客户端内单点完成(multi-model-routing spec),节点不感知模型选择细节。多轮上下文/指代消解/生成质量规则/结构化洞察等既有行为契约 SHALL NOT 因路由引入而变化(同一模型配置下行为逐字一致)。

#### Scenario: 节点任务类型全标记

- GIVEN 路由表已配置且注册表多模型可用
- WHEN 任一图节点调用 LLM
- THEN 调用携带该节点的任务类型,LLM 客户端按路由表选定模型

#### Scenario: 单模型配置零回归

- GIVEN 未配置注册表/路由表
- WHEN 任意问答链路执行
- THEN 全部调用走主模型,016 评估基线不降

### Requirement: 记忆段注入 generate 提示

Agent 面问答请求 SHALL 纯增可选字段 memory_hint(召回的用户已知信息段,控制面注入);generate 节点在拼接用户提示时 SHALL 将非空 memory_hint 作为独立「关于这位用户的已知信息」段置于 schema 提示之前(系统提示模板资产不变);memory_hint 缺省或为空时提示形态与注入前逐字一致。ReAct 内核 SHALL 在首轮 user 消息拼接同一字段(同一拼接语义)。

#### Scenario: 记忆段拼接

- GIVEN 请求携带 memory_hint「偏好月度口径」
- WHEN generate 节点拼接用户提示
- THEN 用户提示含「关于这位用户的已知信息」段且位于表结构之前,系统提示不变

#### Scenario: 无记忆零回归

- GIVEN 请求不携带 memory_hint
- WHEN generate 节点执行
- THEN 提示形态与 022 之前逐字一致

### Requirement: 召回响应语义段

/internal/rag/retrieve 响应 SHALL 纯增可选字段 relations_hint(召回表集合内的 JOIN path 文字段)与 metric_hints(命中指标口径段);缺省/空时 Agent 面行为与引入前一致。两段由控制面组装(语义层域),召回服务失败时随整体回退实时内省(两段缺省为空)。

#### Scenario: 响应携带语义段

- GIVEN 语义层有相关关系与命中指标
- WHEN Agent 面调用召回
- THEN 响应含 relations_hint 与 metric_hints 文字段

### Requirement: 生成提示拼接语义段

generate 节点 SHALL 在用户提示中拼接 relations_hint/metric_hints 非空段(位于表结构提示之后、历史成功 SQL 之前;系统提示模板不变),并在拼接存在时遵循「JOIN 优先依据 relations_hint,口径优先依据 metric_hints」;两段皆空时提示形态与引入前逐字一致。ReAct 内核的 rag_retrieve 工具结果携带同名字段时,LLM 观察原样可见(内核零改动)。

#### Scenario: 拼接位置正确

- GIVEN 召回响应含 relations_hint 与 metric_hints
- WHEN generate 拼接用户提示
- THEN 两段位于表结构之后、历史成功 SQL 之前

#### Scenario: 零注入零回归

- GIVEN 语义层无数据
- WHEN generate 执行
- THEN 提示形态与 023 之前逐字一致

### Requirement: 文档通道与知识分流节点

retrieve 节点 SHALL 在三路召回之外并行取文档通道检索结果(doc_hints:父块全文+source_ref 列表;零命中为空);分流判定(规则词表+schema 空+文档非空)成立时进入 doc_answer 节点直接产出文本答案(summary=回答,无 SQL),否则走既有链路。doc_answer 的提示 SHALL 只含问句与文档段(全新撰写资产),答案 SHALL 以文档内容为准并标注 source_ref;文档段超限时按序截断。既有节点链路与 prompt 资产 SHALL NOT 变化;文档通道失败 SHALL 降级为空(既不影响 schema 通道)。**分流 SHALL 具多轮门槛:会话历史在场(history 非空)时,若问题仅由泛系系词「是什么」命中知识意图词表(强文档词——政策/流程/口径/定义/规范/制度/办法/怎么规定/为什么——零命中)SHALL NOT 分流,回 generate**(047 实证:多轮数据追问「这些影片各自的分类是什么」被文档通道劫持,用户得到「文档中未提及」;会话在场本身即数据语境,泛系系词不足以证明文档意图)。无历史(单轮)时分流行为与 024 语义逐字一致;036④ 附件分流条件不变。

#### Scenario: 分流节点零 SQL 面

- GIVEN 分流条件成立
- WHEN doc_answer 执行
- THEN 终态无 generated_sql、无执行回调,summary 为文档答案

#### Scenario: 文档通道失败降级

- GIVEN 文档检索服务异常
- WHEN retrieve 执行
- THEN doc_hints 为空,链路按无文档处理(与 024 之前一致)

#### Scenario: 多轮数据追问不被文档劫持(047)
- GIVEN 租户已有文档命中能力,会话存在上轮数据问答
- WHEN 当前问题为「这些影片各自的分类是什么」(仅「是什么」命中)
- THEN 路由 generate,生成 SQL 回答(不进 doc_answer)

#### Scenario: 多轮强文档词照旧分流(047)
- GIVEN 会话存在历史,文档命中非空
- WHEN 当前问题为「这些政策的适用口径是什么」(命中强词 政策/口径)
- THEN 路由 doc_answer

#### Scenario: 单轮 024 语义不变(047)
- GIVEN 无历史,文档命中非空
- WHEN 问题为「退货政策是什么」
- THEN 路由 doc_answer(与 024 行为一致)

### Requirement: 表范围纪律与如实说明短路

generate 环节 SHALL 具备表范围纪律:生成的表名与列名 MUST 仅来自本次
注入的「可用表结构」上下文(表关系段与历史成功 SQL 段仅作口径参考,
SHALL NOT 成为引入其中不存在的表的依据);可用表结构未覆盖问题所需数据
时,生成 SHALL 以哨兵(固定 token `NO_RELEVANT_TABLE`)如实说明,系统
提示 MUST 显式声明该哨兵为「只输出 SQL」规则的唯一例外。「未覆盖」SHALL
为两级:可用表结构中无支撑本问题的表(表级),或问句所需关键信息项(排序/
筛选/统计所依据的列)不存在且无法由现有列合理推导(列级)——列级缺口
SHALL NOT 以其他列近似顶替冒充答案。**表级「支撑」SHALL 以实体核对为
判据(051):问题核心业务实体(具体业务名词,如门店/影片/客户/订单)必须
能在可用表结构的表说明或列语义中找到对应,方为支撑;「任何表都可 COUNT
行数或按某列分组」SHALL NOT 构成相关依据,把问题量词(库存数量/个数)读成
对任意表的计数、把任意文本列读成问题中的分类维度,均属硬凑;该判据的
自我合理化形态 SHALL 以对位反例形式在提示词中点名。**空表结构占位文案
MUST NOT 诱导臆测(不得出现「尽力生成」类表述),SHALL 与哨兵机制联动。
generate 节点 SHALL 识别哨兵输出(去除包裹标记后大小写不敏感全等比较)并
短路终止:不进入本地预检与执行、不进入 selfheal 重试(零执行、零 LLM
重试消耗),以用户友好文案收敛为失败终态——文案 SHALL 面向用户说明
「未找到回答该问题所需的数据,可切换数据源或调整问法」,且 SHALL NOT
携带表名、schema 名或内部术语。上次执行错误为「表或列不存在」类时,
喂回生成环节的修正上下文 SHALL 附「所引表或列不在可用表结构清单中,
清单确无所需数据只输出哨兵」的引导。

#### Scenario: 知识未覆盖哨兵短路(零执行零重试)

- GIVEN default 租户可用表结构不含问题所需表(如问 sakila 员工租赁)
- WHEN 模型输出哨兵 NO_RELEVANT_TABLE
- THEN 请求终止,不发起 SQL 执行、不进 selfheal(sys_sql_audit 零新增执行记录)
- AND 响应为失败终态,error 为「未找到回答该问题所需的数据,可切换数据源或调整问法」类友好文案

#### Scenario: 实体领域错配不硬凑选表(051)

- GIVEN 问题「每个门店库存中各影片分类的库存数量」而可用表结构仅商品表
  (名称/创建人等列,无门店/影片/分类语义)
- WHEN generate 环节执行
- THEN 模型输出哨兵短路(零执行零重试),SHALL NOT 把创建人列当影片分类
  分组、把任意 COUNT 当库存凑答案
- AND 洞察层不再出现「分类值为账号或 UUID」类事后质量提示(前置弃答后无结果集)

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
### Requirement: QueryOut 响应终态判别

`/query` 响应的 `verdict` SHALL 为四态:`PASS`(执行成功)/ `BLOCKED`
(权限/策略拦截)/ `ERROR`(执行失败)/ `NO_DATA`(知识未覆盖如实说明,
生成短路终态)。`NO_DATA` 态 `success=false`,SHALL NOT 记为执行失败
语义;`error` SHALL 携带用户可读说明文案;响应 SHALL 不含 SQL 与结果集
(短路发生于执行之前)。

#### Scenario: 短路终态对外判别为 NO_DATA

- GIVEN generate 输出哨兵短路(end_reason=no_data)
- WHEN agent 组装 /query 响应
- THEN verdict=NO_DATA、success=false、error 为友好说明文案
- AND 响应不含 SQL/结果集(短路前零执行)

#### Scenario: 既有三态零回归

- GIVEN PASS/BLOCKED/ERROR 三类既有终态
- WHEN agent 组装 /query 响应
- THEN 判别与字段形态与引入前逐字一致

### Requirement: 派生量接地与数值白名单

结果智能分析输出的 summary 与 insights 文本中的数值 MUST 来自以下白名单
之一:①本次查询结果集的数值单元格;②服务端确定性统计(stats)字段;
③计算工具本轮返回值;④结构性常量(结果行数、预览行数等系统自身给出的
计数)。不在白名单的数值 SHALL NOT 出现。增幅、占比、倍数、差值、复合
变化率等**派生量**若不在白名单,MUST NOT 由语言模型心算写入:要么经计算
工具/服务端预计算得到并入白名单,要么省略该表述(宁省略,不编造)。

#### Scenario: 增长率不得心算

- GIVEN 结果集两行数值 100 与 80,stats 无增长率字段,本轮无计算工具返回
- WHEN 生成 summary 与 insights
- THEN 输出不出现「增长 25%」或任何比率数值
- AND 允许「由 80 变为 100」这类仅含白名单数字的表述

#### Scenario: 计算工具结果可写

- GIVEN 模型调用计算工具得到 delta_pct=25.0
- WHEN 生成 summary
- THEN 允许写「增长 25%」(工具返回值在白名单)

#### Scenario: 结构性常量不误伤

- GIVEN 结果共 10 行,统计给出 avg=3.5
- WHEN 生成 summary
- THEN 允许出现 10 与 3.5,不被审计剥离

### Requirement: 确定性数值审计

总结收口 SHALL 对 summary 与 insights 文本做确定性数值抽取与白名单比对
(数值等价比较,百分数/千分位先归一化)。含非白名单数值的句子 SHALL 被
剥离或降级为不含该数值的表述;审计 SHALL NOT 触发语言模型重试、SHALL NOT
阻断 SQL 与结果字段。审计对结构性常量零误伤;零违规时输出与审计前逐字
一致(零回归)。

#### Scenario: 违规句剥离

- GIVEN summary 原文含一句「环比增长 15%」而 15 不在白名单
- WHEN 审计收口
- THEN 该句不出现在最终 summary
- AND 其余白名单合法句保留,columns/rows/stats 不变

#### Scenario: 零违规零回归

- GIVEN summary 全部数值均在白名单
- WHEN 审计收口
- THEN 输出与审计前逐字一致

### Requirement: 计算工具

系统 SHALL 提供确定性计算工具(至少增幅与占比两类),入参为显式数值,
输出确定性结果并纳入本轮数值白名单。入参 SHALL 校验属于本轮白名单
(结果集/stats/既有工具值),不属于则拒绝并返回可纠正错误。计算工具
SHALL 为只读无副作用,不写任何业务数据。

#### Scenario: 入参白名单校验

- GIVEN 模型以编造基数 50 调用占比计算
- WHEN 工具执行
- THEN 拒绝执行并返回纠正指引(入参不在本轮数值白名单)
- AND 不产生输出值

### Requirement: 趋势与异常类洞察收紧

insights 类型为 trend(趋势)时,SHALL 要求存在不少于 3 期可排序序列且
结论数字在白名单;类型为 anomaly(异常)时,定量结论 SHALL 以白名单值与
确定性统计为据。不满足时 SHALL 不产出该条定量洞察(可保留非定量的数据
质量提示)。

#### Scenario: 两期不写趋势

- GIVEN 结果仅 2 个时间点
- WHEN 生成 insights
- THEN 不出现 trend 类「逐月上升」定量断言

### Requirement: 生成提示词装配预算

送模型之前,生成环节的装配提示词 SHALL 经上界校验:上界由本次所用模型的
上下文窗口推出(窗口减去预留输出与安全边际),未声明窗口的模型 SHALL 回退
全局缺省窗口。超限时 SHALL 按分段优先级收敛后继续,SHALL NOT 让该轮
以服务不可用类失败终态收场。

**截断方向纪律**:

- 多轮历史段 SHALL 只从**最旧端**丢弃,SHALL NOT 中间删或重排,
  以保持提示词前缀字节稳定(前缀逐轮变化会令上游的会话缓存永不命中);
- 证据段(表结构 / 术语 / 黄金示例 / 历史成功 SQL)SHALL 按预筛名次从尾部丢弃,
  且**首位命中豁免预算**(首位为最高相关证据,丢弃它等同自废召回);
- 系统纪律段与本轮问句 SHALL NOT 被截——不得以正确性与安全约束换成本。

**估算纪律**:预算 SHALL 以保守的零依赖估算达成,SHALL NOT 为此引入第三方分词器;
估算 SHALL 可与该次调用实际上报的输入用量对照,用于校准。

**可观测**:预算触发事实、被裁段落标识与裁切量 SHALL 随 trace 下发,
SHALL NOT 静默降质。若不可切段本身已超预算,SHALL 如实失败并给出可归因原因,
SHALL NOT 伪装成服务故障。

**零回归**:未超限轮次 SHALL NOT 因本机制产生任何内容变化。

#### Scenario: 超大 schema 不再硬失败

- GIVEN 某租户 50 张表的表结构段使装配超出所用模型窗口
- WHEN 发起问答
- THEN 提示词按分段纪律收敛后正常送出,该轮不因输入过大而失败
- AND trace 记录被裁段落与裁切量

#### Scenario: 历史只从最旧端丢弃

- GIVEN 多轮历史与证据同时需要收敛
- WHEN 预算收敛执行
- THEN 保留的历史仍是最近若干轮的连续后缀,顺序不变,前缀内容不被中间掏空

#### Scenario: 首位证据豁免

- GIVEN 表结构段需要裁剪才能入预算
- WHEN 收敛执行
- THEN 名次首位的表始终保留,被裁者自尾端起

#### Scenario: 不可切段不被截

- GIVEN 装配超限
- WHEN 收敛执行
- THEN 生成纪律段与本问问句逐字完整保留

#### Scenario: 正常轮次零变化

- GIVEN 装配未超预算
- WHEN 发起问答
- THEN 提示词与引入预算机制前逐字一致

#### Scenario: 失败可归因

- GIVEN 即便证据段全裁,不可切段本身仍超预算
- WHEN 该轮送出失败
- THEN 终态与观测明确指向"输入超出模型窗口"类原因,SHALL NOT 呈现为服务暂不可用

### Requirement: 相关表预筛

生成环节注入的「可用表结构」上下文 SHALL 可经由问题驱动的表级预筛收敛:
预筛 SHALL 以改写后问题、术语库(term/aliases)、人工黄金示例、语义层指标
为输入,对表名、列名与表说明加权打分,产出上限可配的 top-N 表集合
(`N` 缺省 12),SHALL NOT 需要对目标库发起任何额外调用。

预筛 SHALL 可回退:存在开关可在不回滚代码的前提下退回全量注入(基线行为)。

预筛结果 SHALL 可解释:入选表与命中来源(术语/指标/列名)、被裁表数 SHALL
随 trace 下发,使「为何未选中某表」可排查;SHALL NOT 为不可归因的黑盒。

**零命中约束**:问题与全部表零命中时,系统 SHALL 维持既有哨兵路径
(`NO_RELEVANT_TABLE` 如实短路),SHALL NOT 以硬凑 top-N 顶替——把判别职责
从「模型如实说明未覆盖」推给「打分器随意选取」属质量倒退。

**不劣化约束**:预筛开启时的表召回与执行匹配 SHALL NOT 低于全量注入基线
(以评估集对拍为准);退化超过回归红线时 SHALL 可通过开关回退并记录。

#### Scenario: 大 schema 租户收敛注入

- GIVEN 某租户可用表结构含 40 张表,问题仅涉及其中 3 张
- WHEN 走预筛路径生成
- THEN 注入的可用表结构收敛至 top-N(≤12)且包含问题所需表
- AND trace 中可见被裁表数与各入选表的命中来源

#### Scenario: 零命中维持哨兵

- GIVEN 问题与任何表名、列名、术语、指标均零命中
- WHEN 预筛执行
- THEN 不生成硬凑的 top-N,链路按既有哨兵路径如实短路,
  不进入本地预检与执行

#### Scenario: 开关回退

- GIVEN 预筛选开关被关闭
- WHEN 发起问答
- THEN 注入全量可用表结构,行为与引入预筛前逐字一致

#### Scenario: 预筛零新增调用

- GIVEN 预筛开启
- WHEN 执行一次问答
- THEN 相较全量注入,对目标库与外部模型的调用次数不增加

#### Scenario: 不劣化有据

- GIVEN 评估集在预筛开启与关闭两种形态下的 Recall@K 与 Execution Match
- WHEN 对比
- THEN 预筛开启形态不劣于全量基线;若劣化超红线则记录并回退开关
