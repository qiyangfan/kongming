# eval 能力域

## Purpose

NL2SQL 评估体系:gold_sql 与生成 SQL 的匹配规则(列等价 + 行集合数值近似,严格分与宽松分并行)、评估基线管理、gold_sql 编写规范,量化问答质量并拦截回归。

## Requirements

### Requirement: 评估运行形态

评估器 SHALL 以进程外工具形态对真实链路运行:经登录凭证调用用户问答端点
(完整链路:认证→租户→RAG 召回→生成→执行→洞察),以 persist=false 临时对话
形态执行(不落会话/消息,不触发标题改写);gold_sql 由评估器直连目标库执行,
生成 SQL 取自链路响应。评估器与常规测试门禁隔离(SHALL NOT 进入无评估环境的
默认收集),评估分层 smoke(约 5 题)/full(全量单轮)/multiturn(多轮)三级
显式选择运行。

#### Scenario: 评估走真实用户链路

- **GIVEN** 评估环境(双进程+数据集+凭证)就绪
- **WHEN** 运行评估
- **THEN** 生成侧经用户问答端点获得(登录态+租户头+数据源 key 与真实用户同路径)
- **AND** 生成 SQL 取自链路响应,gold_sql 直连目标库执行,双侢单独比较
- **AND** 评估会话不落库(persist=false),不污染业务数据

#### Scenario: 默认门禁隔离

- **GIVEN** 无评估环境(无服务/无数据集/无凭证)的常规测试环境
- **WHEN** 运行默认测试收集
- **THEN** 评估用例不被收集执行(评估须显式指定目录与分层运行)

### Requirement: 评估匹配规则

宽松规则(黄金指标口径)SHALL 为双路径语义比较:路径一=列名集合相等或 gold 列集
⊆ 生成列集(大小写不敏感)→ 生成侧按 gold 列名投影 → 行集合近似相等(浮点
容差 1e-6,NULL 语义一致),gold_sql 含 ORDER BY 时保序比较、否则 multiset
比较;路径二=gold 数值 multiset ⊆ 生成数值 multiset(容忍聚合别名/投影列名
不兼容但数值全对)。纯文本结果集(gold 无数值)SHALL NOT 走路径二(空集子集恒真
误判),路径一列名不兼容时 SHALL 回退严格集合等值兜底。已知既定边界:路径二天然
无序——宽松口径不惩罚序错(值集对即放行,保序仅约束路径一);gold 投影
数值 id 列而生成侧缺失该数值时路径二判 fail,靠编写规范「投影业务可读列」
规避。严格规则(结果集 multiset 精确等值)SHALL 并行输出;基线对照 SHALL 用宽松分,
回归红线 SHALL 用宽松分不下降(劣化 ≥5pp 逐题溯源)。

#### Scenario: 宽松匹配列等价与数值近似

- **GIVEN** gold_sql 与生成 SQL 待比较
- **WHEN** 应用宽松规则
- **THEN** 路径一:列名集合相等或 gold 列集为生成列集子集,生成侧按 gold
    列名投影后行集合近似相等(浮点容差 1e-6)
- **AND** gold 含 ORDER BY 时路径一保序比较,否则集合比较
- **AND** 路径二:gold 数值 multiset ⊆ 生成数值 multiset(列名不兼容救场)
- **AND** 纯文本结果集(gold 无数值)回退严格集合等值(不走路径二)
- **AND** 严格分与宽松分并行输出

#### Scenario: gold 投影 ID、生成投影姓名+ID 经宽松放行

- **GIVEN** gold 投影 ID,生成投影 姓名+ID(列超集)
- **WHEN** 宽松匹配
- **THEN** 宽松 PASS(路径一投影后行集相等)

#### Scenario: 聚合别名差异经路径二放行

- **GIVEN** gold 列 [id, total],生成列 [id, sum](别名差异,数值全对)
- **WHEN** 宽松匹配
- **THEN** 宽松 PASS(路径二数值子集成立)

#### Scenario: 生成 SQL 缺 gold 要求的列

- **GIVEN** 生成 SQL 缺 gold 要求的列(数值也对不上)
- **WHEN** 宽松匹配
- **THEN** 宽松 FAIL

#### Scenario: 序错但值集对时宽松放行(既定边界)

- **GIVEN** gold 含 ORDER BY,生成侧行序不同但值集相同
- **WHEN** 宽松匹配
- **THEN** 宽松 PASS(路径二/纯文本回退天然无序,不惩罚序错;序错差异由
    严格分口径兜底记录)

### Requirement: 评估集结构

评估集按租户分文件,每题含 question/gold_sql/expected_tables/category;category
覆盖 single_table/join_2table/join_3table/join_4plus/aggregate/filter 与多轮(turns[])
分类;评估请求支持可选 datasourceKey(动态路径用例)。gold_sql 结果集 SHALL
≤1000 行(带 LIMIT 或聚合天然小集)——生成侧链路有 1000 行截断上限,超限题
双侧截断口径不一致会假阴性。

#### Scenario: 单轮题结构

- **GIVEN** 评估集文件
- **THEN** 每租户题集 15-20 题(另含 smoke 标记题约 2-5,计入文件但不占
    该区间),覆盖四类基础题型与 join 深度分级
- **AND** 每题含 question/gold_sql/expected_tables/category
- **AND** gold_sql 结果集 ≤1000 行(LIMIT 或聚合天然小集)

#### Scenario: 多轮题结构

- **GIVEN** 多轮评估用例
- **THEN** 每 case 含 turns[](每 turn: question + gold_sql)
- **AND** 覆盖指代(「这些」)/省略(「再查后10名」)/排序延续/过滤追加四类上下文依赖
- **AND** 执行时顺序跑各轮,前序问句作 history,每轮独立断言 Execution Match

#### Scenario: 动态数据源用例

- **GIVEN** 评估请求支持可选 datasourceKey
- **THEN** 含显式动态路径用例(≥3 题)
- **AND** 跨租户 datasourceKey 用例预期拒绝(同形 400 DATASOURCE_INVALID,
    与 chat 域防 key 探测口径一致),验证 fail-closed 不随评估器改动失效

### Requirement: 基线管理

每次评估方法或提示词变更后跑全量,记录严格/宽松/多轮/动态数据源四组基线分数;基线变更须标注原因;黄金指标口径不显著劣化(≥前基线 -5pp),劣化超预期逐题溯源。**触发面 SHALL 扩至环境性质量变量:表结构知识变更(收录/对账/AI 再生成)与文档增删后 SHALL 重跑评估并对照基线**——047 实证:知识再生成与文档灌入(均晚于前基线)独立造成宽松 -4pp、多轮 -25pp,提示词零变更亦漂移;重跑前 SHALL 清同题沉淀与 ctx 缓存(011 教训),连续两轮评估之间亦 SHALL 清缓存(否则验证的是缓存)。

#### Scenario: 变更后记录四组基线
- GIVEN 评估方法或提示词变更
- WHEN 跑全量
- THEN 记录严格/宽松/多轮/动态四组分数,写入基线表(标注变更原因)

#### Scenario: 回归红线
- GIVEN 新基线与前一基线并列
- WHEN 黄金指标劣化超 5pp
- THEN 必须逐题溯源(区分评估集改动影响与 LLM 采样波动)后才可合入

#### Scenario: 知识变更触发重跑(047)
- GIVEN 对某数据源执行了 fetch-metadata 对账(登记行被清偿)或知识 AI 再生成
- WHEN 收口该知识变更
- THEN 重跑评估并对照基线,基线表记录变更原因

#### Scenario: 评估间缓存隔离(047)
- GIVEN 同日已跑过一轮评估(ctx 缓存已填充)
- WHEN 再次跑评估对照基线
- THEN 先清 km:ragctx:* 缓存再跑(否则第二轮验证的是缓存不是链路)

### Requirement: gold_sql 编写规范

gold_sql 含 ORDER BY ... LIMIT N 时 MUST 追加唯一列作第二排序键;投影 SHALL 取业务可读列(SHALL NOT 裸投影 ID);join 新增表 MUST 同步 expected_tables;同题多路径副本 MUST 同步改。

#### Scenario: tiebreaker 必加保证行序确定
- GIVEN gold_sql 含 ORDER BY ... LIMIT N
- WHEN 编写
- THEN ORDER BY 追加唯一列(主键)作第二排序键,保证结果集行序确定(gold 连跑两次结果完全一致)

#### Scenario: 投影业务可读列
- GIVEN 问题问业务实体(客户/商品/会员)
- WHEN 编写 gold_sql
- THEN JOIN 出名称列投影,不裸投影 ID,消除「gen 投影姓名 vs gold 投影 ID」固有边界;名称可能重名时 GROUP BY 主键+名称

#### Scenario: join 新增表同步 expected_tables
- GIVEN gold_sql join 新增表
- WHEN 维护用例
- THEN 必须同步 expected_tables,同题多份副本同步改

#### Scenario: 规范沉淀
- GIVEN 编写规范
- WHEN 沉淀
- THEN 进评估 README,后续新增用例参照执行
### Requirement: 负例评估(如实弃答)

评估集 SHALL 支持「如实弃答」负例形态:`expect_no_data: true` 的用例断言
链路响应为哨兵短路业务终态——success=false、verdict=NO_DATA、不含 SQL
(零执行硬断言),SHALL NOT 与 `expect_reject` 的 HTTP 拒绝形态(fail-closed
400)混用。负例用例的通过由断言保证,SHALL NOT 计入严格 Execution Match
(防虚增,与拒绝用例同口径)。负例集 SHALL 覆盖问题实体与可用表领域错配
的哨兵守护(051 表级判据的回归闸),至少一题进入 smoke 集。

#### Scenario: expect_no_data 用例断言口径

- GIVEN 评估用例标记 expect_no_data(问题实体与可用表领域错配)
- WHEN 链路对该问句生成
- THEN 断言 success=false 且 verdict=NO_DATA 且 SQL 为空
- AND 通过计入宽松 pass、不计严格分(与拒绝用例同口径)

#### Scenario: 实体错配硬凑回归被 smoke 闸拦截

- GIVEN 模型对实体错配问句硬凑生成(哨兵未触发)
- WHEN smoke 集运行含 expect_no_data 负例
- THEN 断言失败并透出实际生成 SQL,阻止纪律劣化入库

### Requirement: 失败归因分类

评估运行 SHALL 对每个失败用例(宽松 Execution Match 未通过、负例守护未通过、
或被全量统计捕获的异常题)给出**唯一一级分类**与分类依据。判据 SHALL 为确定性
计算(SQL 词法、既有抽表能力、链路 verdict、结果集),SHALL NOT 调用 LLM 判定
——不可复现且自身会错。分类 SHALL 为纯函数,并由常规测试门禁单测守护
(不依赖评估环境)。

#### Scenario: 一题一类且带依据

- GIVEN 某失败题的表集与 JOIN 数与 gold 一致,仅聚合函数不同
- WHEN 归因执行
- THEN 归类为 METRIC,明细含两侧聚合函数集合与判据原文

#### Scenario: 负例守护失效自成类

- GIVEN expect_no_data 用例链路却生成了 SQL(哨兵未触发)
- WHEN 归因执行
- THEN 归类为 NEG_GUARD,不再按正例判据细分

#### Scenario: 分类纯函数受默认门禁守护

- GIVEN 归因分类为纯函数
- WHEN 常规测试门禁运行(无评估环境)
- THEN 分类逻辑单测执行,判定回归即时暴露

### Requirement: 失败归因分类优先级序

一级分类与优先级序(先匹配先归类,SHALL NOT 一题多类):

1. `NEG_GUARD` 负例/拒绝守护失效(`expect_reject` / `expect_no_data` 用例失败
   即此类,不再细分);
2. `EXEC` 生成 SQL 执行失败(含 selfheal 耗尽;链路 success=false 或无 SQL);
3. `REJECT` 生成 SQL 被防火墙拦截终止(verdict=BLOCKED);
4. `ABSTAIN` 误弃答(正例题链路返回 NO_DATA);
5. `SELECT` 选表缺失;
6. `JOIN` 关联缺失(gen 涉及表已覆盖 gold 表集,但 gen 的 JOIN 数少于 gold);
7. `METRIC` 口径差异(表与 JOIN 数一致,两侧聚合函数集合不一致);
8. `FILTER` 谓词/时间窗差异(聚合一致,过滤面不一致);
9. `OTHER` 未归因。

#### Scenario: 一题一类且带依据

- GIVEN 某失败题的表集与 JOIN 数与 gold 一致,仅聚合函数不同
- WHEN 归因执行
- THEN 归类为 METRIC,明细含两侧聚合函数集合与判据原文

### Requirement: 选表缺失归因的层拆分

`SELECT` 选表缺失 SHALL 附层拆分信号:期望表未覆盖归为召回层,期望表已覆盖
而 gold 表集未被覆盖归为生成层。

#### Scenario: 选表失败拆到召回层

- GIVEN 失败题的 expected_tables 未被 gen 涉及表覆盖
- WHEN 归因执行
- THEN 归类为 SELECT 且层信号=召回层(区别于期望表覆盖而 gold 其他表缺失
  的生成层)

### Requirement: 归因结果在评估输出中的呈现

评估统计输出 SHALL 在既有四组分数之后增列一级分类计数与 SELECT 的层拆分;
每条失败明细 SHALL 带分类名与命中的判据原文及原始信号(SHALL NOT 只给结论
不给依据)。多轮失败轮次 SHALL 以同一纯函数归因。

#### Scenario: 一题一类且带依据

- GIVEN 某失败题的表集与 JOIN 数与 gold 一致,仅聚合函数不同
- WHEN 归因执行
- THEN 归类为 METRIC,明细含两侧聚合函数集合与判据原文

### Requirement: 未归因桶显式报告

`OTHER` 桶 SHALL 显式报告数量与题目 id;占比 ≥20% 时,评估输出 SHALL 提示
归因规则不足需人工扩充,SHALL NOT 静默接受。

#### Scenario: OTHER 超额显式提示

- GIVEN 20 题失败中 5 题落入 OTHER
- WHEN 评估统计输出
- THEN 显式报告 OTHER=5/20 与题目 id,并提示归因规则不足需人工扩充

### Requirement: 归因用途边界

**用途边界(硬约束)**:归因结果 SHALL 用作提案立项与知识治理排序的证据面;
SHALL NOT 作为回归红线——红线仍为黄金指标劣化 ≥5pp 与提示词指纹可比性
(eval 域基线管理与 073)。启发式判据敌不过 LLM 采样波动,拿它当闸会误伤。

#### Scenario: 归因不作红线

- GIVEN 某次运行 METRIC 类占比较上次上升
- WHEN 定时门禁判定回归
- THEN 红线判定 SHALL NOT 读取归因计数,仍以黄金指标劣化与指纹可比性为准
